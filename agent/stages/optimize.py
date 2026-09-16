"""Stage 5: generate optimization hints, apply them, measure, reflect, repeat.

Every round is a bet, and the snapshot is what makes losing one cheap. All four
optimization prompts end with "Make sure the performance improved. Otherwise,
try again or remove your changes" — advisory text a model cannot be relied on to
honour. Here it is enforced: the workspace is snapshotted before the round,
correctness is re-verified against gold afterwards, runtime is measured as a
median of repeated runs, and anything that fails to clear
``params.min_improvement`` is restored rather than kept.

The stage refuses to start on code that is not already correct. Optimizing a
wrong answer produces a faster wrong answer and spends the whole budget doing
it, so the gate is a hard failure, not a warning.
"""

from __future__ import annotations

import tempfile
import time
from pathlib import Path
from typing import Any, Mapping, Optional

from agent.stages.base import Artifact, Stage, StageResult
from agent.stages.execution import Measurement, QueryRunner
from agent.stages.gold import GoldProvider, GoldSet
from agent.stages.parsing import Query, extract_code, parse_workload
from agent.stages.predict import invoke
from agent.stages.results import compare_tables, load_table, parse_table
from agent.trace.span import new_span, stage_context

# params.strategy -> the prompt that implements it. All four auto-compose
# `optim_constraints`, and the expert variant also pulls in `expert_knowledge`.
PROMPT_BY_STRATEGY: dict[str, str] = {
    "trace": "optim_w_trace",
    "expert_knowledge": "optim_w_expert_knowledge",
    "human_reference": "optim_w_human_reference",
    "sample_plan": "optim_with_sample_plan",
}

DEFAULT_TRACE_LOG = "tracing_output.log"


def _signature_class() -> Any:
    """Build the optimization signature."""
    import dspy

    class OptimizeQuery(dspy.Signature):
        """Propose and apply one round of optimizations to a query implementation.

        Return your edit as an apply_patch block::

            *** Begin Patch
            *** Update File: <path>
            +<complete new file content, one '+' per line>
            *** End Patch

        ``Update File`` replaces the whole file, so emit every line you want to
        keep. Paths are relative to the project root. Use only
        ``*** Add File:``, ``*** Update File:`` and ``*** Delete File:``, and do
        not wrap the patch in markdown fences.

        The storage layout is shared by every query: changing it to speed up one
        query risks regressing the others, and any regression makes the whole
        round get reverted.
        """

        instruction: str = dspy.InputField(desc="The optimization strategy and its constraints.")
        query_id: str = dspy.InputField(desc="The query being optimized.")
        source_path: str = dspy.InputField(desc="Project-relative path of its implementation.")
        current_source: str = dspy.InputField(desc="That file's current content, in full.")
        measurements: str = dspy.InputField(
            desc="Baseline and current runtimes, and what earlier rounds changed and scored."
        )
        tracing_output: str = dspy.InputField(
            desc="Contents of the tracing log, if instrumentation produced one. May be empty."
        )
        patch: str = dspy.OutputField(desc="An apply_patch block implementing the optimization.")
        hints: str = dspy.OutputField(desc="The optimizations applied, one per line.")

    return OptimizeQuery


class OptimizeStage(Stage):
    """Improve the generated code's runtime without breaking correctness.

    Requires: ``query_sources``, ``correctness_report``.
    Produces: ``optimization_report``.

    Config (``stages.optimize.params``):
        ``strategy`` - one of ``trace`` (default), ``expert_knowledge``,
            ``human_reference``, ``sample_plan``. Selects the prompt.
        ``max_rounds`` - optimization rounds to attempt (default 4).
        ``min_improvement`` - fractional runtime reduction a round must achieve
            to be kept, e.g. 0.05 for 5% (default 0.05).
        ``repeat_runs`` - runs per measurement; the median is used (default 3).
        ``sf`` - scale factor, quoted into the prompts and the run command.
        ``target_runtime`` / ``target_factor`` - stop once the scope's total
            runtime reaches this many seconds, or this factor below baseline
            (default factor 2.0).
        ``queries`` - restrict the scope to these query ids.
        ``target_query`` - always optimize this query instead of whichever is
            currently slowest.
        ``run_command`` - how to execute a query. Defaults to the command
            recorded in ``correctness_report``, so it need not be repeated.
        ``trace_log`` - path, relative to the project root, of the log the
            ``trace`` strategy reads (default ``tracing_output.log``).
        ``duckdb_plan`` - inline ``EXPLAIN`` output for the ``sample_plan``
            strategy; otherwise it is collected from ``gold.duckdb_path``.

    Output artifact ``optimization_report``: per round, the strategy, the target
    query, the hints applied, before/after runtimes, whether it was kept or
    reverted and why, plus the final speedup per query.
    """

    name = "optimize"
    requires = ("query_sources", "correctness_report")
    produces = ("optimization_report",)
    description = "Generate hints, apply, measure and reflect until runtime targets are met."
    default_prompt_ids = ("optim_w_trace",)

    def run(self, inputs: Mapping[str, Artifact]) -> StageResult:
        started = time.perf_counter()
        cfg = self.cfg

        correctness = inputs["correctness_report"].read_json()
        sources_doc = inputs["query_sources"].read_json()
        sources = sources_doc.get("sources") or {}

        queries_path = cfg.input_path("queries", required=True)
        all_queries = parse_workload(
            Path(str(queries_path)).read_text(encoding="utf-8", errors="replace")
        )
        scope = self._scope(all_queries, correctness, sources)
        gate = self._gate(scope, correctness)
        if gate is not None:
            return self.make_result(
                status="failed", error=gate, duration_s=time.perf_counter() - started
            )

        try:
            strategy, prompt_id = self._strategy()
            workspace = self.ctx.workspace_for(cfg)
        except Exception as exc:
            return self.make_result(
                status="failed", error=str(exc), duration_s=time.perf_counter() - started
            )

        runner = self._runner(correctness.get("run_command"))
        if not runner.available():
            return self.make_result(
                status="failed",
                error=(
                    "optimize measures runtime, so it needs a run command. "
                    + runner.unavailable_reason()
                ),
                duration_s=time.perf_counter() - started,
            )

        gold = GoldProvider(cfg.gold, timeout_s=cfg.compile.timeout_s).ensure(scope)

        with stage_context(self.name):
            with new_span("stage", self.name, strategy=strategy):
                report = self._optimize(
                    scope=scope,
                    sources=sources,
                    strategy=strategy,
                    prompt_id=prompt_id,
                    runner=runner,
                    gold=gold,
                    workspace=workspace,
                )

        meta: dict[str, Any] = {
            "model": cfg.model.name,
            "prompt_ids": [prompt_id],
            "strategy": strategy,
            "run_id": self.ctx.run_id,
        }
        if not report.get("error"):
            # See hppgen: the fingerprint marks the artifact current, so a run
            # that failed must not write one or the retry would be skipped.
            meta["input_fingerprint"] = self.input_fingerprint(inputs)

        artifact = self.store.put_json(
            self.name,
            "optimization_report",
            report,
            meta=meta,
            target=cfg.outputs.get("optimization_report"),
        )

        if report.get("error"):
            return self.make_result(
                artifacts={"optimization_report": artifact},
                metrics=report.get("metrics", {}),
                status="failed",
                error=str(report["error"]),
                duration_s=time.perf_counter() - started,
            )
        return self.make_result(
            artifacts={"optimization_report": artifact},
            metrics=report.get("metrics", {}),
            duration_s=time.perf_counter() - started,
        )

    # --- the loop ---------------------------------------------------------

    def _optimize(
        self,
        scope: list[Query],
        sources: Mapping[str, str],
        strategy: str,
        prompt_id: str,
        runner: QueryRunner,
        gold: GoldSet,
        workspace: Any,
    ) -> dict[str, Any]:
        """Run rounds until a stop condition, keeping only measured wins."""
        max_rounds = self._int_param("max_rounds", 4)
        repeats = self._int_param("repeat_runs", 3)
        min_improvement = self._float_param("min_improvement", 0.05)

        baseline = self._measure_scope(scope, runner, repeats)
        if baseline is None:
            return {
                "strategy": strategy,
                "error": (
                    "The baseline could not be measured: the generated engine did not run "
                    "successfully for every query in scope. Re-run query_codegen first."
                ),
                "rounds": [],
                "metrics": {"rounds": 0},
            }

        target = self._target_runtime(baseline)
        report: dict[str, Any] = {
            "strategy": strategy,
            "prompt_id": prompt_id,
            "scope": [query.id for query in scope],
            "baseline_s": round(baseline, 6),
            "target_s": round(target, 6),
            "repeat_runs": repeats,
            "min_improvement": min_improvement,
            "rounds": [],
            "per_query": {},
        }

        current = baseline
        history: list[str] = []
        stale = 0
        snapshot_root = self._snapshot_root(workspace)

        for index in range(1, max_rounds + 1):
            if current <= target:
                report["stopped"] = f"target of {target:.4f}s reached"
                break

            query = self._pick_query(scope, runner, repeats)
            source_rel = self._relative_source(sources.get(query.id, ""), workspace)
            if not source_rel:
                report["stopped"] = f"no source recorded for {query.id}"
                break

            with new_span("round", str(index)):
                round_report = self._run_round(
                    index=index,
                    query=query,
                    source_rel=source_rel,
                    prompt_id=prompt_id,
                    strategy=strategy,
                    scope=scope,
                    runner=runner,
                    gold=gold,
                    workspace=workspace,
                    baseline=baseline,
                    current=current,
                    target=target,
                    repeats=repeats,
                    min_improvement=min_improvement,
                    history=history,
                    snapshot_root=snapshot_root,
                )
            report["rounds"].append(round_report)
            history.append(_history_line(index, query.id, round_report))

            if round_report["kept"]:
                improvement = round_report["improvement"] or 0.0
                current = round_report["after_s"] or current
                stale = 0 if improvement >= min_improvement else stale + 1
            else:
                stale += 1

            if stale >= 2:
                report["stopped"] = (
                    f"two consecutive rounds below the {min_improvement:.0%} improvement floor"
                )
                break
        else:
            report["stopped"] = f"round budget of {max_rounds} exhausted"

        report.setdefault("stopped", f"round budget of {max_rounds} exhausted")
        report["final_s"] = round(current, 6)
        report["speedup"] = round(baseline / current, 4) if current else None
        final = self._measure_each(scope, runner, repeats)
        report["per_query"] = {
            query_id: {"median_s": measurement.median_s, "samples": measurement.samples}
            for query_id, measurement in final.items()
        }
        kept = sum(1 for entry in report["rounds"] if entry["kept"])
        report["metrics"] = {
            "strategy": strategy,
            "rounds": len(report["rounds"]),
            "kept": kept,
            "reverted": len(report["rounds"]) - kept,
            "baseline_s": round(baseline, 4),
            "final_s": round(current, 4),
            "speedup": report["speedup"],
            "stopped": report["stopped"],
        }
        return report

    def _run_round(
        self,
        index: int,
        query: Query,
        source_rel: str,
        prompt_id: str,
        strategy: str,
        scope: list[Query],
        runner: QueryRunner,
        gold: GoldSet,
        workspace: Any,
        baseline: float,
        current: float,
        target: float,
        repeats: int,
        min_improvement: float,
        history: list[str],
        snapshot_root: Path,
    ) -> dict[str, Any]:
        """One snapshot → edit → rebuild → verify → measure → keep-or-revert cycle."""
        entry: dict[str, Any] = {
            "round": index,
            "query_id": query.id,
            "source": source_rel,
            "before_s": round(current, 6),
            "after_s": None,
            "improvement": None,
            "kept": False,
            "outcome": "",
            "hints": "",
            "patch_summary": "",
        }

        snapshot = snapshot_root / f"round{index}"
        workspace.snapshot(snapshot)

        if strategy == "trace":
            # Refresh the log the prompt tells the model to read. A stale log
            # would point it at bottlenecks a previous round already removed.
            runner.run(query, trace=True)

        instruction = self._render_instruction(prompt_id, query, current, target, baseline)
        current_source = self._read(workspace, source_rel)
        value = invoke(
            _signature_class(),
            {
                "instruction": instruction,
                "query_id": query.id,
                "source_path": source_rel,
                "current_source": current_source,
                "measurements": self._measurements_text(baseline, current, target, history),
                "tracing_output": self._trace_log(workspace),
            },
            outputs=("patch", "hints"),
            cfg=self.cfg,
            writer=self.writer,
            stage=self.name,
            require_api_key=self.ctx.require_api_key,
            workspace=self._tool_workspace(),
            allow_writes=True,
            allow_build=True,
        )
        entry["hints"] = str(value["hints"]).strip()

        patch_result = workspace.apply_patch(_as_patch(str(value["patch"]), source_rel))
        entry["patch_summary"] = patch_result.summary()
        if not patch_result.ok:
            workspace.restore(snapshot)
            entry["outcome"] = f"reverted: patch did not apply ({patch_result.error})"
            return entry

        if self._should_build(workspace):
            build = workspace.build_project()
            if not build.ok and not build.compiler_missing:
                workspace.restore(snapshot)
                entry["outcome"] = "reverted: build failed"
                entry["report"] = build.brief()
                return entry

        regression = self._verify_scope(scope, runner, gold)
        if regression is not None:
            workspace.restore(snapshot)
            entry["outcome"] = f"reverted: {regression}"
            return entry

        after = self._measure_scope(scope, runner, repeats)
        if after is None:
            workspace.restore(snapshot)
            entry["outcome"] = "reverted: could not measure the optimized build"
            return entry

        improvement = (current - after) / current if current else 0.0
        entry["after_s"] = round(after, 6)
        entry["improvement"] = round(improvement, 4)
        if improvement < min_improvement:
            workspace.restore(snapshot)
            entry["outcome"] = (
                f"reverted: {improvement:.1%} improvement is below the "
                f"{min_improvement:.0%} floor"
            )
            return entry

        entry["kept"] = True
        entry["outcome"] = f"kept: {improvement:.1%} faster"
        return entry

    # --- gate and scope ---------------------------------------------------

    def _scope(
        self,
        queries: list[Query],
        correctness: Mapping[str, Any],
        sources: Mapping[str, str],
    ) -> list[Query]:
        """The queries this run may optimize, in workload order."""
        wanted = self.cfg.params.get("queries")
        if wanted:
            selected = {str(value) for value in wanted}
            return [query for query in queries if query.id in selected]
        reported = correctness.get("queries") or {}
        return [query for query in queries if query.id in reported or query.id in sources]

    def _gate(self, scope: list[Query], correctness: Mapping[str, Any]) -> Optional[str]:
        """Why optimization must not start, or None when it may.

        Deliberately strict: a query that was never executed is as disqualifying
        as one that produced the wrong answer, because in both cases there is no
        evidence the code is correct.
        """
        if not scope:
            return (
                "No queries are in scope. Check stages.optimize.params.queries against the "
                "ids in the correctness report."
            )
        rows = correctness.get("queries") or {}
        if not rows:
            return "The correctness report has no per-query results. Re-run query_codegen."
        problems: list[str] = []
        for query in scope:
            row = rows.get(query.id)
            if row is None:
                problems.append(f"{query.id}: absent from the correctness report")
            elif not row.get("matched"):
                problems.append(f"{query.id}: {row.get('status', 'not matched')}")
        if problems:
            return (
                "Refusing to optimize code that is not verified correct. "
                + "; ".join(problems[:8])
                + ". Optimizing a wrong answer produces a faster wrong answer."
            )
        return None

    # --- measurement ------------------------------------------------------

    def _measure_each(
        self, scope: list[Query], runner: QueryRunner, repeats: int
    ) -> dict[str, Measurement]:
        return {query.id: runner.measure(query, repeats=repeats) for query in scope}

    def _measure_scope(
        self, scope: list[Query], runner: QueryRunner, repeats: int
    ) -> Optional[float]:
        """Total median runtime over the scope, or None if any query failed.

        A failed run makes the total meaningless, so it is reported as "no
        measurement" rather than as a suspiciously fast one.
        """
        total = 0.0
        for query in scope:
            measurement = runner.measure(query, repeats=repeats)
            if not measurement.ok or measurement.median_s is None:
                return None
            total += measurement.median_s
        return total

    def _verify_scope(
        self, scope: list[Query], runner: QueryRunner, gold: GoldSet
    ) -> Optional[str]:
        """The first correctness regression in the scope, or None.

        Every query is re-checked, not just the one that was edited: the storage
        layout is shared, so an edit made for one query is exactly how the
        others break.
        """
        for query in scope:
            gold_file = gold.files.get(query.id)
            if gold_file is None or not gold_file.ok:
                return f"{query.id} has no gold result to re-verify against"
            outcome = runner.run(query)
            if not outcome.ok:
                return f"{query.id} no longer runs ({outcome.brief().splitlines()[0]})"
            comparison = compare_tables(
                load_table(gold_file.path, delimiter=self._delimiter()),
                parse_table(outcome.output, delimiter=self._delimiter()),
                float_tolerance=self._float_param("float_tolerance", 1e-6),
                header_mode=str(self.cfg.params.get("header_mode", "gold")),
                sort_rows=bool(self.cfg.params.get("sort_rows", False)),
            )
            if not comparison.matched:
                return f"{query.id} no longer matches gold ({comparison.reason})"
        return None

    def _pick_query(self, scope: list[Query], runner: QueryRunner, repeats: int) -> Query:
        """Which query to optimize this round.

        The slowest one by default: that is where the remaining time is, so it
        is where a round has the most to win. ``params.target_query`` pins one
        instead.
        """
        pinned = self.cfg.params.get("target_query")
        if pinned:
            for query in scope:
                if query.id == str(pinned):
                    return query
        if len(scope) == 1:
            return scope[0]
        timings = {query.id: runner.measure(query, repeats=1).median_s or 0.0 for query in scope}
        return max(scope, key=lambda query: timings.get(query.id, 0.0))

    def _target_runtime(self, baseline: float) -> float:
        explicit = self.cfg.params.get("target_runtime")
        if explicit is not None:
            try:
                return float(explicit)
            except (TypeError, ValueError):
                pass
        factor = self._float_param("target_factor", 2.0)
        return baseline / factor if factor > 0 else baseline

    # --- prompt rendering -------------------------------------------------

    def _strategy(self) -> tuple[str, str]:
        """The configured strategy and the prompt that implements it."""
        strategy = str(self.cfg.params.get("strategy", "trace"))
        if strategy not in PROMPT_BY_STRATEGY:
            known = ", ".join(sorted(PROMPT_BY_STRATEGY))
            raise ValueError(f"Unknown optimize strategy {strategy!r}. Available: {known}.")
        configured = list(self.cfg.prompt_ids)
        prompt_id = configured[0] if configured else PROMPT_BY_STRATEGY[strategy]
        return strategy, prompt_id

    def _render_instruction(
        self, prompt_id: str, query: Query, current: float, target: float, baseline: float
    ) -> str:
        """Render the strategy prompt, supplying only the placeholders it has.

        Rendering is strict, so every placeholder a prompt declares has to be
        provided; passing values a prompt does not declare would be an error
        too, hence the intersection.
        """
        entry = self.registry.get(prompt_id)
        available = {
            "query_id": query.id,
            "sf": str(self.cfg.params.get("sf", "")),
            "current_rt": f"{current:.4f}s",
            "target_rt": f"{target:.4f}s",
            "factor": f"{(current / target):.2f}" if target else "1.00",
            "bespoke_storage_related": str(
                self.cfg.params.get("bespoke_storage_related", " and the shared storage layout")
            ),
            "duckdb_plan": self._duckdb_plan(query),
            "query_impl_path": str(self.cfg.params.get("query_impl_path", "")),
            "affinity_prompt": str(self.cfg.params.get("affinity_prompt", "")),
            "core_id": str(self.cfg.params.get("core_id", "0")),
        }
        variables = {name: value for name, value in available.items() if name in entry.placeholders}
        return self.registry.render(prompt_id, variables=variables).text

    def _measurements_text(
        self, baseline: float, current: float, target: float, history: list[str]
    ) -> str:
        lines = [
            f"baseline total: {baseline:.4f}s",
            f"current total:  {current:.4f}s",
            f"target total:   {target:.4f}s",
        ]
        if history:
            lines.append("previous rounds:")
            lines.extend(f"  {item}" for item in history[-5:])
        else:
            lines.append("previous rounds: none, this is the first.")
        return "\n".join(lines)

    def _duckdb_plan(self, query: Query) -> str:
        """An ``EXPLAIN`` for the sample_plan strategy."""
        inline = self.cfg.params.get("duckdb_plan")
        if inline:
            return str(inline)
        path = self.cfg.gold.duckdb_path
        if path is None or not Path(path).exists():
            return "(no DuckDB plan available: set params.duckdb_plan or common.gold.duckdb_path)"
        try:
            import duckdb
        except ImportError:
            return "(no DuckDB plan available: the 'duckdb' package is not installed)"
        try:
            connection = duckdb.connect(str(path), read_only=True)
            try:
                rows = connection.execute(f"EXPLAIN {query.text.strip().rstrip(';')}").fetchall()
            finally:
                connection.close()
        except Exception as exc:
            return f"(DuckDB EXPLAIN failed: {exc})"
        return "\n".join(str(row[-1]) for row in rows)

    def _trace_log(self, workspace: Any) -> str:
        name = str(self.cfg.params.get("trace_log", DEFAULT_TRACE_LOG))
        try:
            path = workspace.safe_path(name)
        except Exception:
            return ""
        if not path.exists():
            return ""
        text = Path(path).read_text(encoding="utf-8", errors="replace")
        limit = 20_000
        if len(text) <= limit:
            return text
        # Keep the tail: the log is appended to, so the newest run is at the end.
        return f"… {len(text) - limit} earlier chars omitted …\n" + text[-limit:]

    # --- plumbing ---------------------------------------------------------

    def _runner(self, recorded_command: Any) -> QueryRunner:
        cfg = self.cfg
        template = cfg.params.get("run_command") or recorded_command
        return QueryRunner(
            template=str(template) if template else None,
            project_root=cfg.gen_project_root or cfg.base_dir,
            output_dir=cfg.out_dir or (cfg.artifacts_dir / "optimize_output"),
            build_dir=cfg.compile.build_dir,
            dataset_dir=cfg.gold.dataset_dir,
            sf=str(cfg.params.get("sf", "")),
            trace_flag=str(cfg.params.get("trace_flag", "--trace")),
            runtime_pattern=cfg.params.get("runtime_pattern"),
            timeout_s=cfg.compile.timeout_s,
        )

    def _snapshot_root(self, workspace: Any) -> Path:
        """Where round snapshots go.

        Never inside the workspace: ``snapshot`` copies the whole tree, so a
        destination under the root would copy the snapshots into themselves.
        """
        candidate = Path(self.cfg.artifacts_dir) / "snapshots"
        if _is_inside(candidate, Path(workspace.root)):
            return Path(tempfile.mkdtemp(prefix="agent_optimize_snapshots_"))
        candidate.mkdir(parents=True, exist_ok=True)
        return candidate

    def _relative_source(self, recorded: str, workspace: Any) -> str:
        if not recorded:
            return ""
        return str(workspace.rel(recorded))

    def _read(self, workspace: Any, rel_path: str) -> str:
        try:
            return str(workspace.read_file(rel_path))
        except Exception:
            return ""

    def _tool_workspace(self) -> Any:
        if not self.cfg.params.get("rlm_tools"):
            return None
        return self.ctx.workspace_for(self.cfg)

    def _should_build(self, workspace: Any) -> bool:
        """Whether to rebuild after applying a patch.

        Same ``"auto"`` detection as ``query_codegen``: only build a tree that
        really is a CMake project, so a configuration error cannot masquerade as
        an optimization that broke the build.
        """
        setting = self.cfg.params.get("build_project", "auto")
        if isinstance(setting, str) and setting.lower() == "auto":
            return bool(workspace.has_cmake_project())
        return bool(setting)

    def _delimiter(self) -> str:
        return str(self.cfg.params.get("delimiter", ","))

    def _int_param(self, name: str, default: int) -> int:
        try:
            return max(0, int(self.cfg.params.get(name, default)))
        except (TypeError, ValueError):
            return default

    def _float_param(self, name: str, default: float) -> float:
        try:
            return float(self.cfg.params.get(name, default))
        except (TypeError, ValueError):
            return default


def _is_inside(path: Path, parent: Path) -> bool:
    """Whether ``path`` lies within ``parent``."""
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def _history_line(index: int, query_id: str, round_report: Mapping[str, Any]) -> str:
    """One round summarized for the next round's prompt.

    This is the reflection step: the model sees what it already tried and what
    the measurement said about it, so round three does not re-propose round
    one's reverted change.
    """
    after = round_report["after_s"]
    measured = "not measured" if after is None else f"{after:.4f}s"
    return (
        f"round {index} ({query_id}): {round_report['outcome']} — "
        f"{round_report['before_s']:.4f}s -> {measured}"
        f" | {str(round_report['hints'])[:200]}"
    )


def _as_patch(output: str, source_rel: str) -> str:
    """Accept either an apply_patch block or a bare file, return a patch.

    Models asked for a patch sometimes return the file. Wrapping it is strictly
    better than rejecting the round: the intent is unambiguous, since the
    signature named exactly one file to edit.
    """
    text = output.strip()
    if "*** Begin Patch" in text:
        return text
    body = extract_code(output).rstrip("\n")
    if not body:
        return text
    lines = "\n".join("+" + line for line in body.splitlines())
    return f"*** Begin Patch\n*** Update File: {source_rel}\n{lines}\n*** End Patch\n"
