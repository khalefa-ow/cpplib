"""Stage 4: generate per-query C++ and loop until it compiles and matches gold.

Wired but not implemented; see :class:`QueryCodegenStage` for the contract.
"""

from __future__ import annotations

from typing import Mapping

from agent.stages.base import Artifact, Stage, StageResult


class QueryCodegenStage(Stage):
    """Generate a C++ implementation per query, then fix it until it is correct.

    Requires: ``schema_levels``, ``storage_layout_headers``.
    Produces: ``query_sources``, ``correctness_report``.

    Contract for the implementation:

    Inputs
        ``common.inputs.queries`` (the workload), the ``schema_levels`` text for
        the active level, and the generated header from
        ``storage_layout_headers``. ``params.task`` carries the long task
        description the legacy configs embed, including the loader/builder file
        split and the apply_patch output format.

    Gold results
        Driven by ``common.gold``:

        - ``mode="duckdb"``  - run each query against ``gold.duckdb_path`` with
          the ``duckdb`` package, optionally ``INSTALL spatial``, writing
          ``gold.dir / <query_id><gold.extension>``. Respect ``gold.overwrite``:
          regenerating gold on every run is both slow and a correctness hazard
          if the dataset moved.
        - ``mode="command"`` - run ``gold.command`` as a template, substituting
          ``{query_text}``, ``{query_id}``, ``{gold_output}`` and
          ``{dataset_dir}``.
        - ``mode="none"``    - skip generation and require the files to exist
          already; fail with a clear message if they do not.

    Loop, per query
        generate -> ``compile_file`` -> fix -> ``build_project`` -> ``run_query``
        -> compare to gold -> fix. Bound the fix attempts by
        ``compile.max_fix_rounds`` for compile failures and by a separate
        ``params.max_correctness_rounds`` for result mismatches: the two failures
        need different budgets, since a mismatch usually needs a larger rewrite
        than a missing include.

    Comparison
        Compare full outputs, not a checksum, and report the first differing row
        with its column. Floating-point columns need a tolerance from
        ``params.float_tolerance`` rather than exact equality. A diff the model
        can read is what makes the fix loop converge.

    Output
        ``query_sources`` - JSON map of query id to written source path.
        ``correctness_report`` - JSON: per query, whether it compiled, whether it
        matched, the round counts, and the first mismatch if any. This is the
        artifact the ``optimize`` stage gates on, so a query that never matched
        must be clearly marked rather than omitted.

    Model
        ``CppRLM`` with writes and build enabled, and ``run_query`` wired to the
        built binary. The compiler and gold output are the reward signal; the
        model should be able to read both through tools.
    """

    name = "query_codegen"
    requires = ("schema_levels", "storage_layout_headers")
    produces = ("query_sources", "correctness_report")
    description = "Generate C++ per query and fix until it compiles and matches gold results."
    default_prompt_ids = ("fix_compile_errors",)

    def run(self, inputs: Mapping[str, Artifact]) -> StageResult:
        raise self.not_implemented()
