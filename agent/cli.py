"""Command line entry point: ``python -m agent.cli <command>``.

Commands:
    doctor          Report the environment: interpreter, deno, cmake, compiler,
                    optional packages and API keys.
    run             Execute the pipeline against a config.
    prompts build   Regenerate the prompt manifest from a prompts directory.
    prompts show    Print a prompt, optionally rendered with variables.
    prompts list    List the manifest's entries.
    config show     Print a config's resolved, merged per-stage settings.
    trace serve     Serve a step-by-step web view of a trace file's LLM calls.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Optional, Sequence

from agent import __version__
from agent.errors import AgentError

if TYPE_CHECKING:
    from agent.config.loader import LoadedConfig
    from agent.pipeline import Pipeline

# Load environment variables from .env file if it exists
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    # python-dotenv not installed, try manual loading
    env_file = Path(".env")
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                key, _, value = line.partition("=")
                if key:
                    os.environ.setdefault(key, value)

# --------------------------------------------------------------------------
# doctor
# --------------------------------------------------------------------------


def _probe_package(name: str) -> str:
    """Report whether an optional package is importable, and its version."""
    if importlib.util.find_spec(name) is None:
        return "MISSING"
    try:
        module = __import__(name)
        return getattr(module, "__version__", "installed")
    except Exception as exc:
        return f"BROKEN ({type(exc).__name__})"


def _probe_command(command: str, args: Sequence[str] = ("--version",)) -> str:
    """Report whether an external tool is on PATH, and its version line."""
    path = shutil.which(command)
    if path is None:
        return "MISSING"
    try:
        proc = subprocess.run([command, *args], capture_output=True, text=True, timeout=10)
        first = (proc.stdout or proc.stderr or "").strip().splitlines()
        return f"{first[0] if first else 'ok'}  [{path}]"
    except Exception:
        return f"present  [{path}]"


def cmd_doctor(args: argparse.Namespace) -> int:
    """Print an environment report.

    Deliberately checks everything and reports rather than failing at the first
    problem: the point is to see the whole picture in one shot, since these
    prerequisites tend to be missing in combination.
    """
    print(f"agent {__version__}")
    print(f"  python           {sys.version.split()[0]}  [{sys.executable}]")
    print()
    print("Python packages:")
    for package in ("dspy", "cpplib", "pydantic", "duckdb", "pyarrow", "litellm", "weave", "wandb"):
        print(f"  {package:16s} {_probe_package(package)}")
    print()
    print("External tools:")
    print(f"  {'deno':16s} {_probe_command('deno')}        <- required by dspy.RLM's sandbox")
    print(f"  {'cmake':16s} {_probe_command('cmake')}")
    for compiler in ("g++", "clang++"):
        print(f"  {compiler:16s} {_probe_command(compiler, ('--version',))}")
    print()

    print("API keys:")
    from agent.llm.lm_factory import PROVIDER_DEFAULTS

    seen: set[str] = set()
    for env_var, _ in PROVIDER_DEFAULTS.values():
        if not env_var or env_var in seen:
            continue
        seen.add(env_var)
        print(f"  {env_var:24s} {'set' if os.environ.get(env_var) else 'not set'}")
    print()

    manifest = Path(__file__).parent / "prompting" / "manifest.json"
    if manifest.exists():
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
            print(f"Prompt manifest: {len(data.get('entries', {}))} entries at {manifest}")
        except Exception as exc:
            print(f"Prompt manifest: UNREADABLE ({exc})")
    else:
        print(f"Prompt manifest: MISSING — run `{_self()} prompts build`")

    if args.config:
        print()
        print(f"Config {args.config}:")
        try:
            from agent.config.loader import LoadedConfig
            from agent.llm.lm_factory import describe_model

            config = LoadedConfig.from_file(args.config)
            print(f"  base_dir: {config.base_dir}")
            for name in config.stage_names() or ["<none declared>"]:
                if name.startswith("<"):
                    print(f"  stages: {name}")
                    continue
                stage = config.resolve_stage(name)
                print(f"  {name:16s} {describe_model(stage.model)}")
                for key, path in sorted(stage.inputs.items()):
                    state = "ok" if path.exists() else "MISSING"
                    print(f"      input {key:14s} {state:8s} {path}")
        except AgentError as exc:
            print(f"  ERROR: {exc}")
            return 1
    return 0


# --------------------------------------------------------------------------
# run
# --------------------------------------------------------------------------


def _apply_cache_override(config: "LoadedConfig", no_cache: bool, refresh_cache: bool) -> None:
    """Force every stage's cache behavior from the CLI, overriding the config file.

    Mutates ``config.raw`` (what ``LoadedConfig.resolve_stage`` actually merges
    from) before any stage resolves, and also strips per-stage ``cache``
    overrides so a stage-specific cache block in the config file cannot
    silently re-enable what ``--no-cache``/``--refresh-cache`` turned on.
    """
    if not no_cache and not refresh_cache:
        return
    common = config.raw.setdefault("common", {})
    cache = dict(common.get("cache") or {})
    if no_cache:
        cache["enabled"] = False
    if refresh_cache:
        cache["refresh"] = True
    common["cache"] = cache
    for stage_cfg in (config.raw.get("stages") or {}).values():
        stage_cfg.pop("cache", None)


def cmd_run(args: argparse.Namespace) -> int:
    from agent.config.loader import LoadedConfig
    from agent.pipeline import Pipeline

    try:
        config = LoadedConfig.from_file(args.config)
        _apply_cache_override(config, no_cache=args.no_cache, refresh_cache=args.refresh_cache)
        pipeline = Pipeline(
            config,
            manifest_path=args.manifest,
            require_api_key=not args.no_api_key_check,
        )
        only = [s.strip() for s in args.stages.split(",") if s.strip()] if args.stages else None

        if args.dry_run:
            print(pipeline.describe(only=only, start_from=args.start_from))
            return 0

        if args.steps is not None:
            return _run_step_by_step(pipeline, args, only)

        summary = pipeline.run(
            only=only,
            start_from=args.start_from,
            force=args.force,
            stop_on_error=not args.keep_going,
        )
        print(summary.report())
        if args.result_json:
            Path(args.result_json).parent.mkdir(parents=True, exist_ok=True)
            Path(args.result_json).write_text(
                summary.model_dump_json(indent=2) + "\n", encoding="utf-8"
            )
            print(f"  result written to {args.result_json}")
        return 0 if summary.ok else 1
    except AgentError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


def _run_step_by_step(pipeline: "Pipeline", args: argparse.Namespace, only: Optional[list[str]]) -> int:
    """Run the pipeline one stage at a time, advancing automatically.

    Each step is a single pipeline stage (``storage_plan``, ``divide``,
    ``hppgen``, ``query_codegen``, ``optimize`` by default), so ``--steps 5``
    runs the full default pipeline stage by stage. A stage that itself covers
    several hint levels (``hppgen``, ``query_codegen``) still generates every
    level configured in ``active_levels`` within that one step; ``--steps``
    counts stages, not levels.

    Progress prints after every step, so a long run can be watched without
    waiting for the whole pipeline to finish, and a run that dies partway
    through can be resumed with ``--start-from`` at the failed stage.
    """
    names = pipeline.plan(only=only, start_from=args.start_from)
    if not names:
        print("no stages to run")
        return 0
    limit = min(args.steps, len(names)) if args.steps > 0 else len(names)

    run_id: Optional[str] = None
    all_ok = True
    for index, name in enumerate(names[:limit], start=1):
        print(f"\n[step {index}/{limit}] {name}")
        summary = pipeline.run(
            only=[name],
            force=args.force,
            run_id=run_id,
            stop_on_error=True,
        )
        run_id = summary.run_id
        print(summary.report())
        if not summary.ok:
            all_ok = False
            if not args.keep_going:
                print(f"\nstopped after {index}/{limit} step(s): {name} failed")
                return 1

    print(f"\ncompleted {limit}/{len(names)} step(s)" + ("" if all_ok else " (some failed)"))
    return 0 if all_ok else 1


# --------------------------------------------------------------------------
# prompts
# --------------------------------------------------------------------------


def cmd_prompts(args: argparse.Namespace) -> int:
    from agent.prompting.build_manifest import import_prompts_dir, rebuild_manifest, set_prompt_text
    from agent.prompting.registry import PromptRegistry

    try:
        if args.prompts_command == "build":
            # With --prompts-dir: import/merge those .txt files as inline
            # entries. Without it: recompute placeholders/version for every
            # entry already in the manifest from its own text field.
            if args.prompts_dir:
                manifest = import_prompts_dir(args.prompts_dir, args.manifest, strict=not args.lax)
            else:
                manifest = rebuild_manifest(args.manifest, strict=not args.lax)
            print(f"wrote {args.manifest} with {len(manifest.entries)} entries")
            for entry in manifest.sorted_entries():
                placeholders = ", ".join(entry.placeholders) or "-"
                print(
                    f"  {entry.id:42s} [{entry.stage or '-'}/{entry.role}] "
                    f"v{entry.version} {placeholders}"
                )
            return 0

        if args.prompts_command == "set":
            if args.file:
                text = Path(args.file).read_text(encoding="utf-8")
            elif args.text is not None:
                text = args.text
            else:
                text = sys.stdin.read()
            manifest = set_prompt_text(
                args.manifest,
                args.prompt_id,
                text,
                stage=args.stage,
                role=args.role,
                description=args.description,
                strict=not args.lax,
            )
            entry = manifest.entries[args.prompt_id]
            placeholders = ", ".join(entry.placeholders) or "-"
            print(
                f"wrote '{args.prompt_id}' to {args.manifest} "
                f"[{entry.stage or '-'}/{entry.role}] v{entry.version} {placeholders}"
            )
            return 0

        registry = PromptRegistry.from_manifest(args.manifest)

        if args.prompts_command == "list":
            for prompt_id in registry.ids():
                entry = registry.get(prompt_id)
                preview = entry.text.strip().splitlines()[0][:60] if entry.text.strip() else ""
                print(f"{prompt_id:42s} [{entry.stage or '-'}/{entry.role}] {preview}")
            return 0

        if args.prompts_command == "show":
            variables = dict(_parse_vars(args.var))
            if args.raw:
                print(registry.text_of(args.prompt_id), end="")
                return 0
            # variables= not **variables: a --var name could otherwise collide
            # with render()'s own keyword-only parameters.
            rendered = registry.render(args.prompt_id, variables=variables)
            print(rendered.text)
            print(
                f"\n--- composed from {', '.join(rendered.prompt_ids)} "
                f"(fingerprint {rendered.fingerprint[:16]})",
                file=sys.stderr,
            )
            return 0
    except AgentError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


def _parse_vars(pairs: Optional[Sequence[str]]) -> list[tuple[str, str]]:
    """Parse repeated ``--var name=value`` arguments."""
    out: list[tuple[str, str]] = []
    for pair in pairs or ():
        if "=" not in pair:
            raise AgentError(f"--var expects name=value, got {pair!r}")
        name, value = pair.split("=", 1)
        out.append((name, value))
    return out


# --------------------------------------------------------------------------
# config
# --------------------------------------------------------------------------


def cmd_config(args: argparse.Namespace) -> int:
    from agent.config.loader import LoadedConfig

    try:
        config = LoadedConfig.from_file(args.config)
        if args.config_command == "show":
            names = [args.stage] if args.stage else (config.stage_names() or [])
            if not names:
                print("(config declares no stages)")
            payload = {
                name: json.loads(config.resolve_stage(name).model_dump_json()) for name in names
            }
            print(json.dumps(payload, indent=2, sort_keys=True))
            return 0
        if args.config_command == "normalize":
            print(json.dumps(config.raw, indent=2, sort_keys=True))
            return 0
    except AgentError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


# --------------------------------------------------------------------------
# trace
# --------------------------------------------------------------------------


def cmd_trace(args: argparse.Namespace) -> int:
    from agent.trace.webview import serve

    if args.trace_command == "serve":
        trace_path = Path(args.trace_path)
        if not trace_path.exists():
            print(f"error: no trace file at {trace_path}", file=sys.stderr)
            return 1
        serve(trace_path, port=args.port, open_browser=args.open_browser)
        return 0
    return 0


# --------------------------------------------------------------------------
# argument parsing
# --------------------------------------------------------------------------


def _self() -> str:
    return "python -m agent.cli"


DEFAULT_MANIFEST = str(Path(__file__).parent / "prompting" / "manifest.json")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agent", description=__doc__)
    parser.add_argument("--version", action="version", version=f"agent {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    doctor = sub.add_parser("doctor", help="report the environment and prerequisites")
    doctor.add_argument("--config", help="also validate this config and its input paths")
    doctor.set_defaults(func=cmd_doctor)

    run = sub.add_parser("run", help="run the pipeline")
    run.add_argument("--config", required=True, help="path to the JSON config")
    run.add_argument("--stages", help="comma-separated subset of stages to run")
    run.add_argument("--start-from", help="begin at this stage, skipping earlier ones")
    run.add_argument(
        "--force", action="store_true", help="re-run stages even if artifacts are current"
    )
    run.add_argument("--dry-run", action="store_true", help="print the stage plan and exit")
    run.add_argument("--keep-going", action="store_true", help="continue after a stage fails")
    run.add_argument("--result-json", help="write the run summary here")
    run.add_argument("--manifest", default=DEFAULT_MANIFEST, help="prompt manifest path")
    run.add_argument(
        "--no-api-key-check",
        action="store_true",
        help="skip the API key check (for offline/cached runs)",
    )
    cache_group = run.add_mutually_exclusive_group()
    cache_group.add_argument(
        "--no-cache",
        action="store_true",
        help=(
            "bypass the disk cache entirely: every model call is made fresh, "
            "and nothing is read from or written to the cache"
        ),
    )
    cache_group.add_argument(
        "--refresh-cache",
        action="store_true",
        help=(
            "ignore existing cache entries and recompute, but still write the "
            "new results (use to refresh stale or bad cached answers)"
        ),
    )
    run.add_argument(
        "--steps",
        type=int,
        default=None,
        help=(
            "run N pipeline stages one at a time, advancing automatically "
            "(e.g. --steps 5 runs storage_plan, divide, hppgen, query_codegen, "
            "optimize in sequence). A stage covering several hint levels "
            "(hppgen, query_codegen) still generates every active level "
            "within its one step."
        ),
    )
    run.set_defaults(func=cmd_run)

    prompts = sub.add_parser("prompts", help="inspect and rebuild the prompt manifest")
    prompts_sub = prompts.add_subparsers(dest="prompts_command", required=True)

    prompts_build = prompts_sub.add_parser(
        "build",
        help="recompute placeholders/version from each entry's text "
        "(or import a directory of .txt files with --prompts-dir)",
    )
    prompts_build.add_argument(
        "--prompts-dir",
        default=None,
        help="import/merge .txt files from this directory as inline entries, "
        "instead of just recomputing from the manifest's existing text",
    )
    prompts_build.add_argument("--manifest", default=DEFAULT_MANIFEST)
    prompts_build.add_argument(
        "--lax",
        action="store_true",
        help="do not fail on templates containing an invalid '$' sequence",
    )

    prompts_set = prompts_sub.add_parser("set", help="add or update one prompt's text")
    prompts_set.add_argument("prompt_id")
    prompts_set.add_argument("--manifest", default=DEFAULT_MANIFEST)
    prompts_set.add_argument("--file", help="read the prompt text from this file")
    prompts_set.add_argument("--text", help="the prompt text, given directly")
    prompts_set.add_argument("--stage", help="override the entry's stage")
    prompts_set.add_argument("--role", help="override the entry's role")
    prompts_set.add_argument("--description", help="override the entry's description")
    prompts_set.add_argument(
        "--lax",
        action="store_true",
        help="do not fail on text containing an invalid '$' sequence",
    )

    prompts_list = prompts_sub.add_parser("list", help="list manifest entries")
    prompts_list.add_argument("--manifest", default=DEFAULT_MANIFEST)

    prompts_show = prompts_sub.add_parser("show", help="print a prompt, rendered by default")
    prompts_show.add_argument("prompt_id")
    prompts_show.add_argument("--manifest", default=DEFAULT_MANIFEST)
    prompts_show.add_argument("--var", action="append", help="placeholder value as name=value")
    prompts_show.add_argument("--raw", action="store_true", help="print the unrendered template")

    prompts.set_defaults(func=cmd_prompts)

    config = sub.add_parser("config", help="inspect a configuration")
    config_sub = config.add_subparsers(dest="config_command", required=True)

    config_show = config_sub.add_parser("show", help="print resolved per-stage config")
    config_show.add_argument("--config", required=True)
    config_show.add_argument("--stage", help="only this stage")

    config_normalize = config_sub.add_parser(
        "normalize", help="print the config after legacy normalization"
    )
    config_normalize.add_argument("--config", required=True)

    config.set_defaults(func=cmd_config)

    trace = sub.add_parser("trace", help="inspect a run's JSONL trace")
    trace_sub = trace.add_subparsers(dest="trace_command", required=True)

    trace_serve = trace_sub.add_parser(
        "serve", help="serve a step-by-step web view of LLM calls in a trace file"
    )
    trace_serve.add_argument("trace_path", help="path to a trace.jsonl file")
    trace_serve.add_argument("--port", type=int, default=8765)
    trace_serve.add_argument(
        "--open-browser", action="store_true", help="also open a browser tab automatically"
    )

    trace.set_defaults(func=cmd_trace)

    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except AgentError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
