"""Stage 4: generate per-query C++ and loop until it compiles and matches gold.

The heavy stage, and the one where the reward signal is real: the compiler and
the gold results decide whether a query is done, not the model's own assessment.

Two budgets, not one. ``compile.max_fix_rounds`` bounds compile and link
failures; ``params.max_correctness_rounds`` bounds result mismatches. They are
separate because the failures are different sizes — a missing include is a
one-line fix, while a wrong answer usually means the plan was wrong and the
implementation has to change shape — and a single shared budget would let a
string of trivial compile errors consume the rounds a mismatch needs.

What this stage will not do is claim correctness it did not check. With no run
command configured, or with gold unavailable, each query is reported as
``unverified`` and the run summary carries ``verified=False``; the ``optimize``
stage then refuses to start rather than optimizing code of unknown correctness.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Mapping, Optional

from agent.config.models import LevelConfig
from agent.rlm.compile import CompileResult
from agent.stages.base import Artifact, Stage, StageResult
from agent.stages.divide import DESCRIPTIONS_KEY, LEVELS_KEY
from agent.stages.execution import QueryRunner
from agent.stages.fixing import no_progress_reason, repair_signature
from agent.stages.gold import GoldProvider, GoldSet
from agent.stages.parsing import Query, extract_code, parse_workload
from agent.stages.predict import invoke
from agent.stages.results import Comparison, compare_tables, load_table, parse_table
from agent.trace.span import new_span, stage_context

FIX_PROMPT_ID = "fix_compile_errors"

# Terminal states a query can end in. Every query gets exactly one, and they are
# all recorded: a query that is quietly absent from the report reads as "fine".
STATUS_MATCHED = "matched"
STATUS_MISMATCH = "mismatch"
STATUS_COMPILE_FAILED = "compile_failed"
STATUS_BUILD_FAILED = "build_failed"
STATUS_RUN_FAILED = "run_failed"
STATUS_NO_GOLD = "no_gold"
STATUS_UNVERIFIED = "unverified"


def _signature_class() -> Any:
    """Build the generation signature."""
    import dspy

    class GenerateQueryImplementation(dspy.Signature):
        """Implement one SQL query as C++ over an in-memory storage layout.

        The storage layout is fixed and shared by every query: read it, never
        change it, and never add a structure that exists only to serve this one
        query. Compile the query's plan into the code — no SQL parsing at
        runtime.

        Output the complete translation unit and nothing else.
        """

        task: str = dspy.InputField(desc="The interface, output format and constraints to obey.")
        query_id: str = dspy.InputField(desc="Identifier for this query.")
        query_sql: str = dspy.InputField(desc="The SQL statement to implement.")
        query_intent: str = dspy.InputField(
            desc="The query's header comment, if the workload file had one. May be empty."
        )
        namespace: str = dspy.InputField(desc="Namespace the storage layout is declared in.")
        storage_header: str = dspy.InputField(desc="The generated storage-layout header, in full.")
        header_include: str = dspy.InputField(desc="The #include line for that header.")
        level_text: str = dspy.InputField(desc="The hint level's description of schema and layout.")
        cpp_standard: str = dspy.InputField(desc="C++ standard the source must compile under.")
        source_code: str = dspy.OutputField(desc="The complete C++ source. No markdown fences.")
        notes: str = dspy.OutputField(desc="The plan you implemented, in two or three sentences.")

    return GenerateQueryImplementation


class QueryCodegenStage(Stage):
    """Generate a C++ implementation per query, then fix it until it is correct.

    Supports one or more hint levels sequentially. Each level generates separate
    code into separate output directories. To generate multiple levels from a
    single config, list them all in ``stages.query_codegen.active_levels``; the
    stage will iterate through each one automatically.

    Requires: ``schema_levels``, ``storage_layout_headers``.
    Produces: ``query_sources``, ``correctness_report``.

    Config:
        ``common.inputs.queries`` - the workload, split into queries whose
            ``-- Q<n>`` headers become the ids used throughout the report.
        ``stages.query_codegen.active_levels`` - one or more levels. When
            multiple levels are specified, they are generated sequentially,
            each into a separate subdirectory under ``gen_project_root``.
            For backward compatibility, a single level works as before.
        ``common.gold`` - where reference results come from. See
            :class:`agent.stages.gold.GoldProvider`.
        ``compile.max_fix_rounds`` - compile and link repair budget per query.
        ``stages.query_codegen.params``:
            ``run_command`` - how to execute one query. Placeholders:
                ``{query_id}``, ``{query_text}``, ``{output}``,
                ``{project_root}``, ``{build_dir}``, ``{dataset_dir}``,
                ``{sf}``, ``{trace}``. Without it, nothing is verified.
            ``max_correctness_rounds`` - mismatch repair budget (default 3).
            ``float_tolerance`` - absolute tolerance for numeric cells (1e-6).
            ``header_mode`` - which side has a header row that is not data:
                ``"gold"`` (default, since DuckDB writes gold with a header and
                the prompt tells the engine to print bare rows), ``"none"``,
                ``"actual"`` or ``"both"``.
            ``sort_rows`` - compare as multisets. Only for a workload with no
                ``ORDER BY``; it hides ordering bugs otherwise.
            ``source_dir`` - where sources are written (default ``src/queries``).
            ``entry_signature`` - overrides the generated entry point.
            ``build_project`` - ``"auto"`` (default), true or false.
            ``rlm_tools`` - hand the model the workspace tools as well.

    Output artifacts:
        ``query_sources`` - list of artifacts, one per level:
            ``{"level":..., "sources": {"<query id>": path}}``.
        ``correctness_report`` - per query per level: status, round counts,
            the first mismatch, and the gold file it was compared against.
    """

    name = "query_codegen"
    requires = ("schema_levels", "storage_layout_headers")
    produces = ("query_sources", "correctness_report")
    description = "Generate C++ per query and fix until it compiles and matches gold results."
    default_prompt_ids = ("query_codegen_task", FIX_PROMPT_ID)

    def run(self, inputs: Mapping[str, Artifact]) -> StageResult:
        started = time.perf_counter()
        cfg = self.cfg

        try:
            levels = self._get_levels()
            if not levels:
                raise ValueError("No active levels configured for query_codegen")
            workspace = self.ctx.workspace_for(cfg)
        except Exception as exc:
            return self._fail(str(exc), started)

        queries_path = cfg.input_path("queries", required=True)
        queries = parse_workload(
            Path(str(queries_path)).read_text(encoding="utf-8", errors="replace")
        )
        if not queries:
            return self._fail(f"No SQL statements found in {queries_path}.", started)

        split = inputs["schema_levels"].read_json()
        headers = inputs["storage_layout_headers"].read_json().get("headers") or {}
        gold = self._gold(queries)
        runner = self._runner()

        # Collect all level results
        all_level_results = []

        with stage_context(self.name):
            for level in levels:
                # The auto-generated CMake dispatch build assumes one set of
                # query sources for the whole project; it isn't level-aware,
                # so with several active levels it is skipped in favor of
                # each query compiling on its own via run_command. Single
                # level runs keep the original auto-setup behavior.
                if len(levels) == 1:
                    if not self._setup_build_environment(workspace, queries, level=level):
                        # Fallback: not critical, queries may still compile individually
                        pass

                level_text = str((split.get(LEVELS_KEY) or {}).get(level.name, ""))
                description = str((split.get(DESCRIPTIONS_KEY) or {}).get(level.name, ""))
                if not level_text.strip():
                    return self._fail(
                        f"The schema_levels artifact has no text for level '{level.name}'.",
                        started,
                    )

                header_path = headers.get(level.name)
                if not header_path or not Path(header_path).exists():
                    return self._fail(
                        f"No generated header for level '{level.name}' at {header_path!r}. "
                        f"Run hppgen for this level first.",
                        started,
                    )
                header_text = Path(header_path).read_text(encoding="utf-8", errors="replace")

                task = self.registry.render_any(
                    self.prompt_ids()[0] if self.prompt_ids() else None,
                    inline=cfg.params.get("task"),
                    entry_signature=self._entry_signature(level),
                    delimiter=self._param("delimiter", ","),
                    header_rule=self._header_rule(),
                )

                sources: dict[str, str] = {}
                report: dict[str, Any] = {}

                with new_span("level", level.name, writer=self.writer):
                    for query in queries:
                        with new_span("query", query.id, writer=self.writer):
                            row = self._process_query(
                                query=query,
                                level=level,
                                level_text=level_text,
                                description=description,
                                header_path=Path(header_path),
                                header_text=header_text,
                                task_text=task.text,
                                task_fingerprint=task.fingerprint,
                                gold=gold,
                                runner=runner,
                                workspace=workspace,
                            )
                        report[query.id] = row
                        if row.get("source"):
                            sources[query.id] = row["source"]

                counts = _tally(report)
                verified = counts.get(STATUS_MATCHED, 0) == len(queries)
                unverified = counts.get(STATUS_UNVERIFIED, 0) + counts.get(STATUS_NO_GOLD, 0)

                broken = [
                    qid
                    for qid, row in report.items()
                    if row["status"] in (STATUS_COMPILE_FAILED, STATUS_BUILD_FAILED, STATUS_RUN_FAILED)
                ]
                mismatched = [qid for qid, row in report.items() if row["status"] == STATUS_MISMATCH]

                meta: dict[str, Any] = {
                    "model": cfg.model.name,
                    "prompt_ids": list(task.prompt_ids),
                    "level": level.name,
                    "run_id": self.ctx.run_id,
                }
                if not broken and not mismatched:
                    # Only a trustworthy artifact carries the fingerprint that marks it
                    # current; otherwise the next run would skip the retry.
                    meta["input_fingerprint"] = self.input_fingerprint(inputs)

                sources_artifact = self.store.put_json(
                    self.name,
                    "query_sources",
                    {"level": level.name, "namespace": level.namespace, "sources": sources},
                    meta=meta,
                    target=cfg.outputs.get("query_sources"),
                )
                report_artifact = self.store.put_json(
                    self.name,
                    "correctness_report",
                    {
                        "level": level.name,
                        "verified": verified,
                        "run_command": runner.template,
                        "gold": {"mode": gold.mode, "summary": gold.summary()},
                        "counts": counts,
                        "queries": report,
                    },
                    meta=meta,
                    target=cfg.outputs.get("correctness_report"),
                )

                all_level_results.append(
                    {
                        "level": level.name,
                        "sources_artifact": sources_artifact,
                        "report_artifact": report_artifact,
                        "counts": counts,
                        "verified": verified,
                        "unverified": unverified,
                        "broken": broken,
                        "mismatched": mismatched,
                        "num_queries": len(queries),
                    }
                )

        # Aggregate results from all levels. For the common single-level case
        # these collapse to exactly that level's own numbers, so the metrics
        # shape stays backward compatible with a single-level run.
        all_broken: list[str] = []
        all_mismatched: list[str] = []
        combined_counts: dict[str, int] = {}
        for r in all_level_results:
            all_broken.extend(r["broken"])
            all_mismatched.extend(r["mismatched"])
            for status, count in r["counts"].items():
                combined_counts[status] = combined_counts.get(status, 0) + count

        verified = all(r["verified"] for r in all_level_results)
        unverified = sum(r["unverified"] for r in all_level_results)

        metrics: dict[str, Any] = {
            "queries": len(queries),
            "verified": verified,
            "unverified": unverified,
            **{f"n[{status}]": count for status, count in sorted(combined_counts.items())},
        }
        if len(all_level_results) > 1:
            metrics["levels"] = len(all_level_results)
            metrics["levels_verified"] = sum(r["verified"] for r in all_level_results)

        # Use the last level's artifacts as the primary ones
        if all_level_results:
            last = all_level_results[-1]
            artifacts = {
                "query_sources": last["sources_artifact"],
                "correctness_report": last["report_artifact"],
            }
        else:
            artifacts = {}

        if all_broken or all_mismatched:
            problems = []
            if all_broken:
                problems.append(
                    f"{len(all_broken)} did not build ({', '.join(set(all_broken[:5]))})"
                )
            if all_mismatched:
                problems.append(
                    f"{len(all_mismatched)} did not match gold ({', '.join(set(all_mismatched[:5]))})"
                )
            return self.make_result(
                artifacts=artifacts,
                metrics=metrics,
                status="failed",
                error="; ".join(problems) + ". See the correctness_report artifact.",
                duration_s=time.perf_counter() - started,
            )

        return self.make_result(
            artifacts=artifacts, metrics=metrics, duration_s=time.perf_counter() - started
        )

    # --- per query --------------------------------------------------------

    def _process_query(
        self,
        query: Query,
        level: LevelConfig,
        level_text: str,
        description: str,
        header_path: Path,
        header_text: str,
        task_text: str,
        task_fingerprint: str,
        gold: GoldSet,
        runner: QueryRunner,
        workspace: Any,
    ) -> dict[str, Any]:
        """Generate, compile, build, run and compare one query.

        The loop is written out rather than delegated to
        :func:`agent.stages.fixing.fix_loop` because the two budgets have to be
        spent independently, which a single-verifier loop cannot express.
        """
        cfg = self.cfg
        rel_source = self._source_path(query, level)
        row: dict[str, Any] = {
            "query_id": query.id,
            "sql": query.text,
            "status": STATUS_UNVERIFIED,
            "source": "",
            "compiled": False,
            "built": False,
            "ran": False,
            "matched": False,
            "compile_rounds": 0,
            "correctness_rounds": 0,
            "report": "",
            "gold": str(gold.files[query.id].path) if query.id in gold.files else "",
            "cache_hit": False,
            "predictor": "",
            "notes": "",
        }

        payload = {
            "task": task_text,
            "query_id": query.id,
            "query_sql": query.text,
            "query_intent": query.label,
            "namespace": level.namespace,
            "storage_header": header_text,
            "header_include": self._include_line(header_path, workspace),
            "level_text": f"{description}\n\n{level_text}".strip(),
            "cpp_standard": cfg.compile.cpp_standard,
        }
        key = self.cache.make_key(
            {
                **payload,
                "level": level.name,
                "source": rel_source,
                "tolerance": self._float_tolerance(),
                "header_mode": self._header_mode(),
            },
            prompt_fingerprint=task_fingerprint,
            model_signature=cfg.model.signature(),
            kind="stage",
        )

        cached = self.cache.get(key)
        if cached is not None and isinstance(cached.value, dict):
            content = str(cached.value.get("source", ""))
            row["cache_hit"] = True
            row["predictor"] = str(cached.value.get("predictor", ""))
            row["notes"] = str(cached.value.get("notes", ""))
        else:
            first = self._call_model(payload)
            content = extract_code(first["source_code"])
            row["predictor"] = first["predictor"]
            row["notes"] = first["notes"]

        gold_file = gold.files.get(query.id)
        max_compile = cfg.compile.max_fix_rounds
        max_correctness = self._max_correctness_rounds()

        while True:
            workspace.write_file(rel_source, content)
            row["source"] = str(workspace.safe_path(rel_source))

            compiled = workspace.compile_file(rel_source, syntax_only=True)
            if not compiled.ok and not compiled.compiler_missing:
                if row["compile_rounds"] >= max_compile:
                    row["status"] = STATUS_COMPILE_FAILED
                    row["report"] = compiled.brief()
                    return row
                candidate = self._call_repair(content, compiled.brief(), compiled)
                stop = no_progress_reason(content, candidate)
                if stop:
                    row["status"] = STATUS_COMPILE_FAILED
                    row["report"] = f"{compiled.brief()}\n(stopped: {stop})"
                    return row
                content, row["compile_rounds"] = candidate, row["compile_rounds"] + 1
                continue
            row["compiled"] = True

            if self._should_build(workspace):
                build = workspace.build_project()
                if not build.ok and not build.compiler_missing:
                    if row["compile_rounds"] >= max_compile:
                        row["status"] = STATUS_BUILD_FAILED
                        row["report"] = build.brief()
                        return row
                    candidate = self._call_repair(content, build.brief(), build)
                    stop = no_progress_reason(content, candidate)
                    if stop:
                        row["status"] = STATUS_BUILD_FAILED
                        row["report"] = f"{build.brief()}\n(stopped: {stop})"
                        return row
                    content, row["compile_rounds"] = candidate, row["compile_rounds"] + 1
                    continue
                row["built"] = True

            if not runner.available():
                row["status"] = STATUS_UNVERIFIED
                row["report"] = runner.unavailable_reason()
                break
            if gold_file is None or not gold_file.ok:
                row["status"] = STATUS_NO_GOLD
                row["report"] = (
                    gold_file.error
                    if gold_file is not None and gold_file.error
                    else f"No gold result for {query.id}."
                )
                break

            outcome = runner.run(query, level=level.name)
            if not outcome.ok:
                if row["correctness_rounds"] >= max_correctness:
                    row["status"] = STATUS_RUN_FAILED
                    row["report"] = outcome.brief()
                    return row
                candidate = self._call_repair(content, outcome.brief(), None)
                stop = no_progress_reason(content, candidate)
                if stop:
                    row["status"] = STATUS_RUN_FAILED
                    row["report"] = f"{outcome.brief()}\n(stopped: {stop})"
                    return row
                content, row["correctness_rounds"] = candidate, row["correctness_rounds"] + 1
                continue
            row["ran"] = True

            comparison = self._compare(gold_file.path, outcome.output)
            row["report"] = comparison.brief()
            if comparison.matched:
                row["status"] = STATUS_MATCHED
                row["matched"] = True
                row["runtime_s"] = round(outcome.runtime_s, 6)
                break
            row["mismatch"] = comparison.model_dump(mode="json")
            if row["correctness_rounds"] >= max_correctness:
                row["status"] = STATUS_MISMATCH
                return row
            candidate = self._call_repair(content, comparison.brief(), None)
            stop = no_progress_reason(content, candidate)
            if stop:
                row["status"] = STATUS_MISMATCH
                row["report"] = f"{comparison.brief()}\n(stopped: {stop})"
                return row
            content, row["correctness_rounds"] = candidate, row["correctness_rounds"] + 1

        if row["status"] == STATUS_MATCHED and not row["cache_hit"]:
            # Only a verified implementation is cached, so a re-run retries a
            # broken one instead of returning it from disk forever.
            self.cache.put(
                key,
                {"source": content, "predictor": row["predictor"], "notes": row["notes"]},
                meta={"stage": self.name, "query": query.id, "model": cfg.model.name},
            )
        return row

    # --- model calls ------------------------------------------------------

    def _call_model(self, payload: dict[str, str]) -> dict[str, Any]:
        return invoke(
            _signature_class(),
            payload,
            outputs=("source_code", "notes"),
            cfg=self.cfg,
            writer=self.writer,
            stage=self.name,
            require_api_key=self.ctx.require_api_key,
            workspace=self._tool_workspace(),
            allow_writes=True,
            allow_build=True,
        )

    def _call_repair(self, current: str, report: str, result: Optional[CompileResult]) -> str:
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

    # --- configuration helpers -------------------------------------------

    def _param(self, name: str, default: Any) -> Any:
        value = self.cfg.params.get(name)
        return default if value is None else value

    def _get_levels(self) -> list[LevelConfig]:
        """Return all active levels, supporting generation of multiple levels sequentially.

        Returns:
            All active levels to be generated. The stage will iterate through each one.
        """
        levels = self.cfg.resolved_active_levels()
        return levels

    def _source_path(self, query: Query, level: LevelConfig) -> str:
        """Where a query's generated source is written.

        Nested under the level's name whenever more than one level is active,
        so two levels generating the same query id do not overwrite each
        other's source file (and so a later level's leftover file from an
        earlier level can't make ``_setup_build_environment`` believe a full
        dispatch build is already wired up for a level it never generated).
        """
        directory = str(self._param("source_dir", "src/queries")).strip("/")
        if len(self._get_levels()) > 1:
            directory = f"{directory}/{level.name}" if directory else level.name
        return f"{directory}/{query.slug}.cpp" if directory else f"{query.slug}.cpp"

    def _include_line(self, header_path: Path, workspace: Any) -> str:
        return f'#include "{workspace.rel(header_path)}"'

    def _entry_signature(self, level: LevelConfig) -> str:
        override = self.cfg.params.get("entry_signature")
        if override:
            return str(override)
        params = self.cfg.params
        db_type = params.get("database_type_name", "Database")
        db_param = params.get("database_param_name", "db")
        prefix = params.get("query_namespace_prefix", "queries::")
        function = params.get("query_function_name", "run")
        return (
            f"void {prefix}{function}(const {level.namespace}::{db_type}& {db_param}, "
            f"std::ostream& out)"
        )

    def _header_rule(self) -> str:
        """The output-format sentence handed to the generator.

        Derived from ``header_mode`` rather than stated separately so the
        instruction and the comparison cannot disagree — an engine told to print
        a header while the comparison drops one from the other side would fail
        every query on a phantom row-count mismatch.
        """
        if self._header_mode() in ("actual", "both"):
            return "Print a header row naming the columns, matching the reference output exactly."
        return (
            "Do not print a header row; the reference output's header row is dropped "
            "before comparison."
        )

    def _header_mode(self) -> str:
        return str(self._param("header_mode", "gold"))

    def _float_tolerance(self) -> float:
        try:
            return float(self._param("float_tolerance", 1e-6))
        except (TypeError, ValueError):
            return 1e-6

    def _max_correctness_rounds(self) -> int:
        try:
            return max(0, int(self._param("max_correctness_rounds", 3)))
        except (TypeError, ValueError):
            return 3

    def _should_build(self, workspace: Any) -> bool:
        """Whether to build the whole project after a file compiles.

        ``"auto"`` builds when the tree actually is a CMake project, so a plain
        source directory does not fail every query on a configuration error that
        has nothing to do with the generated code.
        """
        setting = self._param("build_project", "auto")
        if isinstance(setting, str) and setting.lower() == "auto":
            return bool(workspace.has_cmake_project())
        return bool(setting)

    def _gold(self, queries: list[Query]) -> GoldSet:
        provider = GoldProvider(self.cfg.gold, timeout_s=self.cfg.compile.timeout_s)
        try:
            return provider.ensure(queries)
        except Exception as exc:
            # Gold being unavailable must not lose the codegen work: every query
            # is reported as no_gold instead, and the stage says why.
            result = GoldSet(mode=self.cfg.gold.mode)
            from agent.stages.gold import GoldFile

            for query in queries:
                result.files[query.id] = GoldFile(
                    query_id=query.id,
                    path=Path(str(self.cfg.gold.dir or "."))
                    / f"{query.slug}{self.cfg.gold.extension}",
                    error=f"{type(exc).__name__}: {exc}",
                )
            return result

    def _runner(self) -> QueryRunner:
        cfg = self.cfg
        return QueryRunner(
            template=cfg.params.get("run_command"),
            project_root=cfg.gen_project_root or cfg.base_dir,
            output_dir=cfg.out_dir or (cfg.artifacts_dir / "query_output"),
            build_dir=cfg.compile.build_dir,
            dataset_dir=cfg.gold.dataset_dir,
            sf=str(cfg.params.get("sf", "")),
            trace_flag=str(cfg.params.get("trace_flag", "")),
            runtime_pattern=cfg.params.get("runtime_pattern"),
            timeout_s=cfg.compile.timeout_s,
        )

    def _compare(self, gold_path: Path, output: str) -> Comparison:
        expected = load_table(gold_path, delimiter=str(self._param("delimiter", ",")))
        actual = parse_table(output, delimiter=str(self._param("delimiter", ",")), source="engine")
        return compare_tables(
            expected,
            actual,
            float_tolerance=self._float_tolerance(),
            header_mode=self._header_mode(),
            sort_rows=bool(self._param("sort_rows", False)),
        )

    def _tool_workspace(self) -> Any:
        if not self.cfg.params.get("rlm_tools"):
            return None
        return self.ctx.workspace_for(self.cfg)

    def _setup_build_environment(self, workspace: Any, queries: list[Query], level: Optional[LevelConfig] = None) -> bool:
        """Generate CMakeLists.txt, query wrappers, and dispatch code if needed.

        Args:
            workspace: The workspace for code generation.
            queries: The queries to build.
            level: The active level (for potential future use in multi-level builds).

        Returns True if setup was successful, False otherwise.
        """
        cmake_path = Path(self.cfg.compile.cmake_dir or workspace.cmake_dir) / "CMakeLists.txt"
        if cmake_path.exists():
            return True  # Already set up

        try:
            import re

            # Discover query files
            queries_dir = Path(self.cfg.compile.cmake_dir or workspace.cmake_dir) / "src" / "queries"
            query_files = sorted(queries_dir.glob("q*.cpp"))
            if not query_files:
                return False  # No queries to build

            # Generate CMakeLists.txt dynamically
            query_sources = "\n  ".join(f"src/queries/{f.name}" for f in query_files)
            wrapped_sources = "\n  ".join(f"q{i}_wrapped.cpp" for i in range(1, len(query_files) + 1))

            cmake_content = f"""cmake_minimum_required(VERSION 3.15)
project(QueryEngine)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# Main executable with query dispatch
add_executable(engine
  ${{CMAKE_CURRENT_SOURCE_DIR}}/src/main.cpp
  ${{CMAKE_CURRENT_SOURCE_DIR}}/query_dispatch.cpp
  {wrapped_sources}
)

target_include_directories(engine PRIVATE ${{CMAKE_CURRENT_SOURCE_DIR}})
"""
            cmake_path.write_text(cmake_content)

            # Wrap each query file with unique namespace
            for i, qfile in enumerate(query_files, 1):
                content = qfile.read_text()
                wrapped = content.replace(
                    "namespace queries {", f"namespace q{i}_namespace {{"
                )
                wrapped_path = cmake_path.parent / f"q{i}_wrapped.cpp"
                wrapped_path.write_text(wrapped)

            # Generate dispatch code
            forward_decls = "\n".join(
                f"namespace q{i}_namespace {{ void run(const basic::Database& db, std::ostream& out); }}"
                for i in range(1, len(query_files) + 1)
            )

            dispatch_cases = "\n  ".join(
                f'if (query_id == "q{i}" || query_id == "{i}") return q{i}_namespace::run;'
                for i in range(1, len(query_files) + 1)
            )

            dispatch_content = f'''#include "storage_layout_basic.hpp"
#include <string>
#include <ostream>

{forward_decls}

typedef void (*QueryFunc)(const basic::Database&, std::ostream&);

QueryFunc get_query_func(const std::string& query_id) {{
  {dispatch_cases}
  return nullptr;
}}
'''
            dispatch_path = cmake_path.parent / "query_dispatch.cpp"
            dispatch_path.write_text(dispatch_content)

            return True
        except Exception as e:
            from agent.trace.span import set_error

            set_error(f"Build setup failed: {e}")
            return False

    def _fail(self, error: str, started: float) -> StageResult:
        return self.make_result(
            status="failed", error=error, duration_s=time.perf_counter() - started
        )


def _tally(report: Mapping[str, Mapping[str, Any]]) -> dict[str, int]:
    """Count queries per terminal status."""
    counts: dict[str, int] = {}
    for row in report.values():
        status = str(row.get("status", "unknown"))
        counts[status] = counts.get(status, 0) + 1
    return counts
