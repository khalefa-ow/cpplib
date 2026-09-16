"""Stage 5: generate optimization hints, apply them, measure, reflect, repeat.

Wired but not implemented; see :class:`OptimizeStage` for the contract. Unlike
the other stubs this one's prompts already exist — the twelve files in
``prompts/`` were written for exactly this loop.
"""

from __future__ import annotations

from typing import Mapping

from agent.stages.base import Artifact, Stage, StageResult


class OptimizeStage(Stage):
    """Improve the generated code's runtime without breaking correctness.

    Requires: ``query_sources``, ``correctness_report``.
    Produces: ``optimization_report``.

    Contract for the implementation:

    Gate
        Refuse to start unless ``correctness_report`` shows the baseline
        compiles and matches gold for every query in scope. Optimizing code that
        is already wrong produces a faster wrong answer and wastes the budget.

    Prompts (all already present in ``prompts/``)
        Strategy is selected by ``params.strategy``:

        - ``"trace"``            -> ``optim_w_trace`` (needs ``${target_rt}``,
          ``${current_rt}``, ``${factor}``, ``${sf}``, ``${query_id}``)
        - ``"expert_knowledge"`` -> ``optim_w_expert_knowledge``, which
          auto-composes the ``expert_knowledge`` blob
        - ``"human_reference"``  -> ``optim_w_human_reference``
        - ``"sample_plan"``      -> ``optim_with_sample_plan`` (needs
          ``${duckdb_plan}`` from a DuckDB ``EXPLAIN``)

        All four auto-compose ``optim_constraints`` through the registry's
        ``composes`` map, so the shared hard constraints are injected without
        being passed explicitly. ``optim_add_timings_collect_stats`` sets up the
        ``#ifdef TRACE`` instrumentation the ``trace`` strategy reads, and
        ``optim_pinning`` pins execution to one core before timing.

    Loop, per round
        1. Snapshot the workspace (``CppWorkspace.snapshot``).
        2. Generate hints, then apply them via the write tools.
        3. Rebuild and re-run; re-verify against gold.
        4. Measure. Keep the change only if runtime improved by at least
           ``params.min_improvement`` and correctness held; otherwise
           ``CppWorkspace.restore`` the snapshot. The prompts all end with
           "Make sure the performance improved. Otherwise, try again or remove
           your changes", and the snapshot/restore pair is what makes that
           instruction actually enforceable rather than advisory.
        5. Reflect: feed the measured delta back into the next round's prompt.

    Stopping
        Whichever comes first: ``params.target_runtime`` reached,
        ``params.max_rounds`` exhausted, or two consecutive rounds below
        ``params.min_improvement``.

    Measurement
        Time with instrumentation off - the ``TRACE`` build exists to find
        bottlenecks, not to benchmark. Report a median of
        ``params.repeat_runs`` runs rather than a single sample, and pin to a
        core so the numbers are comparable between rounds.

    Output
        ``optimization_report`` - JSON: per round, the strategy used, the hints
        applied, the measured before/after runtime, whether it was kept or
        reverted, and the final speedup per query.
    """

    name = "optimize"
    requires = ("query_sources", "correctness_report")
    produces = ("optimization_report",)
    description = "Generate hints, apply, measure and reflect until runtime targets are met."
    default_prompt_ids = ("optim_w_trace",)

    def run(self, inputs: Mapping[str, Artifact]) -> StageResult:
        raise self.not_implemented()
