"""Stage 3: generate the storage-layout header, one per active hint level.

Each level gets its own header in its own namespace, so the same query code can
be compiled against a layout designed with no hints, with organized schema
facts, or with the full storage plan — and the three compared.

The compile-and-fix loop is driven by the stage rather than by the model. The
model could be handed the write and compile tools and left to iterate (set
``params.rlm_tools`` to do that), but the default keeps the loop here for three
reasons: the round budget is actually enforced, the accepted header is exactly
what gets cached, and the per-level round count is a real measurement rather
than something the model self-reports.
"""

from __future__ import annotations

import time
from typing import Any, Mapping, Optional

from agent.config.models import LevelConfig
from agent.rlm.compile import CompileResult
from agent.stages.base import Artifact, Stage, StageResult
from agent.stages.divide import DESCRIPTIONS_KEY, LEVELS_KEY
from agent.stages.fixing import (
    FixOutcome,
    FixRound,
    compile_verdict,
    fix_loop,
    repair_signature,
)
from agent.stages.parsing import extract_code
from agent.stages.predict import invoke
from agent.trace.span import new_span, stage_context

FIX_PROMPT_ID = "fix_compile_errors"


def _signature_class() -> Any:
    """Build the generation signature."""
    import dspy

    class GenerateStorageHeader(dspy.Signature):
        """Write the storage-layout header for one hint level.

        Implement exactly what the level text supports. The level text is the
        only description of the layout you get; if it omits something you would
        want, leave it out rather than inventing it, because a later level is
        supposed to be the one that adds it.

        Output the complete header source and nothing else.
        """

        policy: str = dspy.InputField(desc="What the header must contain and the rules it obeys.")
        level_name: str = dspy.InputField(desc="Name of the hint level being generated.")
        namespace: str = dspy.InputField(desc="Namespace to wrap every declaration in.")
        level_text: str = dspy.InputField(
            desc="Schema facts, and hints if this level includes any."
        )
        data_structure_description: str = dspy.InputField(
            desc="One-sentence summary of the intended data structures. May be empty."
        )
        cpp_standard: str = dspy.InputField(desc="C++ standard the header must compile under.")
        header_code: str = dspy.OutputField(desc="The complete header source. No markdown fences.")
        notes: str = dspy.OutputField(
            desc="Anything the level text left underspecified, and what you assumed."
        )

    return GenerateStorageHeader


class HppGenStage(Stage):
    """Generate the storage-layout ``.hpp`` for each active level.

    Requires: ``schema_levels``.
    Produces: ``storage_layout_headers``.

    Config:
        ``common.gen_project_root`` - required; the C++ tree headers are written
            into. Each level's ``file`` is relative to it.
        ``stages.hppgen.active_levels`` - which levels to generate. Empty means
            every level declared in ``common.levels``.
        ``stages.hppgen.prompt_ids`` - defaults to ``("hppgen_policy",
            "fix_compile_errors")``. The first is the generation policy; the
            repair prompt is looked up by id, and is in the default list so that
            editing it invalidates this stage's cache.
        ``compile.max_fix_rounds`` - repair attempts per level.
        ``stages.hppgen.params.rlm_tools`` - hand the model the workspace tools
            and let it edit and compile directly instead of using the loop here.

    Output artifact ``storage_layout_headers``::

        {"headers": {"<level>": "<absolute path>"},
         "detail":  {"<level>": {namespace, file, verified, repair_rounds, report}}}

    One artifact rather than one per level: ``query_codegen`` then depends on a
    single key instead of a set that changes with ``active_levels``.
    """

    name = "hppgen"
    requires = ("schema_levels",)
    produces = ("storage_layout_headers",)
    description = "Generate the storage-layout header for each active hint level."
    default_prompt_ids = ("hppgen_policy", FIX_PROMPT_ID)

    def run(self, inputs: Mapping[str, Artifact]) -> StageResult:
        started = time.perf_counter()
        cfg = self.cfg

        split = inputs["schema_levels"].read_json()
        levels_text = split.get(LEVELS_KEY) or {}
        descriptions = split.get(DESCRIPTIONS_KEY) or {}

        try:
            levels = cfg.resolved_active_levels()
        except Exception as exc:
            return self.make_result(
                status="failed", error=str(exc), duration_s=time.perf_counter() - started
            )
        if not levels:
            return self.make_result(
                status="failed",
                error="No levels to generate. Declare common.levels, or clear active_levels.",
                duration_s=time.perf_counter() - started,
            )

        missing = [
            level.name for level in levels if not str(levels_text.get(level.name, "")).strip()
        ]
        if missing:
            return self.make_result(
                status="failed",
                error=(
                    f"The schema_levels artifact has no text for level(s): {', '.join(missing)}. "
                    f"Re-run divide, whose output must cover every declared level."
                ),
                duration_s=time.perf_counter() - started,
            )

        try:
            workspace = self.ctx.workspace_for(cfg)
        except Exception as exc:
            return self.make_result(
                status="failed", error=str(exc), duration_s=time.perf_counter() - started
            )

        policy = self.registry.render_any(
            self.prompt_ids()[0] if self.prompt_ids() else None,
            inline=cfg.params.get("policy"),
        )

        headers: dict[str, str] = {}
        detail: dict[str, dict[str, Any]] = {}
        metrics: dict[str, Any] = {}
        failures: list[str] = []

        with stage_context(self.name):
            for level in levels:
                with new_span("level", level.name):
                    outcome, extra = self._generate_level(
                        level=level,
                        level_text=str(levels_text[level.name]),
                        description=str(descriptions.get(level.name, "")),
                        policy_text=policy.text,
                        policy_fingerprint=policy.fingerprint,
                        workspace=workspace,
                    )
                path = workspace.safe_path(level.file)
                headers[level.name] = str(path)
                detail[level.name] = {
                    "namespace": level.namespace,
                    "file": workspace.rel(path),
                    "verified": extra["verified"],
                    "compiler_missing": extra["compiler_missing"],
                    "repair_rounds": outcome.repair_rounds,
                    "stopped": outcome.stopped,
                    "report": outcome.last_report,
                    "cache_hit": extra["cache_hit"],
                    "predictor": extra["predictor"],
                    "notes": extra["notes"],
                }
                metrics[f"rounds[{level.name}]"] = outcome.repair_rounds
                metrics[f"verified[{level.name}]"] = extra["verified"]
                if not outcome.ok:
                    failures.append(f"{level.name}: {outcome.last_report.splitlines()[0]}")

        meta: dict[str, Any] = {
            "model": cfg.model.name,
            "prompt_ids": list(policy.prompt_ids),
            "levels": [level.name for level in levels],
            "run_id": self.ctx.run_id,
        }
        if not failures:
            # The fingerprint is what marks an artifact as current, so it is
            # written only when the artifact is trustworthy. Stamping a failed
            # run would make the pipeline skip the retry and report the broken
            # headers as up to date.
            meta["input_fingerprint"] = self.input_fingerprint(inputs)

        artifact = self.store.put_json(
            self.name,
            "storage_layout_headers",
            {"headers": headers, "detail": detail},
            meta=meta,
            target=cfg.outputs.get("storage_layout_headers"),
        )

        if failures:
            # The headers stay on disk and the artifact is written either way:
            # a header that nearly compiles is the most useful thing to hand a
            # human debugging the failure.
            return self.make_result(
                artifacts={"storage_layout_headers": artifact},
                metrics=metrics,
                status="failed",
                error=f"{len(failures)} level(s) did not compile. " + " | ".join(failures),
                duration_s=time.perf_counter() - started,
            )

        return self.make_result(
            artifacts={"storage_layout_headers": artifact},
            metrics={**metrics, "levels": len(levels)},
            duration_s=time.perf_counter() - started,
        )

    # --- per level --------------------------------------------------------

    def _generate_level(
        self,
        level: LevelConfig,
        level_text: str,
        description: str,
        policy_text: str,
        policy_fingerprint: str,
        workspace: Any,
    ) -> tuple[FixOutcome, dict[str, Any]]:
        """Generate, compile and repair one level's header.

        Returns the loop outcome plus the bookkeeping the caller reports. Only a
        header that actually compiled is cached: caching a broken one would make
        every retry return the same broken header until the cache was cleared.
        """
        cfg = self.cfg
        payload = {
            "policy": policy_text,
            "level_name": level.name,
            "namespace": level.namespace,
            "level_text": level_text,
            "data_structure_description": description,
            "cpp_standard": cfg.compile.cpp_standard,
        }
        key = self.cache.make_key(
            {**payload, "file": level.file, "flags": sorted(cfg.compile.extra_flags)},
            prompt_fingerprint=policy_fingerprint,
            model_signature=cfg.model.signature(),
            kind="stage",
        )

        cached = self.cache.get(key)
        if cached is not None and isinstance(cached.value, dict):
            content = str(cached.value.get("header", ""))
            workspace.write_file(level.file, content)
            result = workspace.compile_file(level.file, syntax_only=True)
            ok, report = compile_verdict(result)
            outcome = FixOutcome(
                ok=ok,
                content=content,
                stopped="cached",
                rounds=[FixRound(index=0, ok=ok, report=report)],
            )
            return outcome, {
                "verified": ok and not result.compiler_missing,
                "compiler_missing": result.compiler_missing,
                "cache_hit": True,
                "predictor": str(cached.value.get("predictor", "")),
                "notes": str(cached.value.get("notes", "")),
            }

        first = self._call_model(payload)
        last: dict[str, Optional[CompileResult]] = {"result": None}

        def apply(content: str) -> None:
            workspace.write_file(level.file, content)

        def verify() -> tuple[bool, str]:
            result = workspace.compile_file(level.file, syntax_only=True)
            last["result"] = result
            return compile_verdict(result)

        def repair(current: str, report: str) -> str:
            return self._call_repair(current, report, last["result"])

        outcome = fix_loop(
            content=extract_code(first["header_code"]),
            apply=apply,
            verify=verify,
            repair=repair,
            max_rounds=cfg.compile.max_fix_rounds,
        )

        result = last["result"]
        compiler_missing = bool(result is not None and result.compiler_missing)
        verified = outcome.ok and not compiler_missing
        if verified:
            self.cache.put(
                key,
                {
                    "header": outcome.content,
                    "predictor": first["predictor"],
                    "notes": first["notes"],
                    "repair_rounds": outcome.repair_rounds,
                },
                meta={"stage": self.name, "level": level.name, "model": cfg.model.name},
            )
        return outcome, {
            "verified": verified,
            "compiler_missing": compiler_missing,
            "cache_hit": False,
            "predictor": first["predictor"],
            "notes": first["notes"],
        }

    # --- model calls ------------------------------------------------------

    def _call_model(self, payload: dict[str, str]) -> dict[str, Any]:
        return invoke(
            _signature_class(),
            payload,
            outputs=("header_code", "notes"),
            cfg=self.cfg,
            writer=self.writer,
            stage=self.name,
            require_api_key=self.ctx.require_api_key,
            workspace=self._tool_workspace(),
            allow_writes=True,
            allow_build=True,
        )

    def _call_repair(self, current: str, report: str, result: Optional[CompileResult]) -> str:
        """Ask the model to fix a header that did not compile."""
        instruction = self.registry.render(
            FIX_PROMPT_ID,
            variables={
                "compiler": self.cfg.compile.compiler,
                "cpp_standard": self.cfg.compile.cpp_standard,
                "command": " ".join(result.command) if result and result.command else "(n/a)",
                "diagnostics": report,
            },
        )
        value = invoke(
            repair_signature(),
            {"instruction": instruction.text, "report": report, "current_code": current},
            outputs=("fixed_code", "explanation"),
            cfg=self.cfg,
            writer=self.writer,
            stage=self.name,
            require_api_key=self.ctx.require_api_key,
            workspace=self._tool_workspace(),
            allow_writes=True,
            allow_build=True,
        )
        return extract_code(value["fixed_code"])

    def _tool_workspace(self) -> Any:
        """The workspace to hand the model, when ``params.rlm_tools`` is set."""
        if not self.cfg.params.get("rlm_tools"):
            return None
        return self.ctx.workspace_for(self.cfg)
