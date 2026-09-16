"""Stage 3: generate the storage-layout header, one per active hint level.

Wired but not implemented; see :class:`HppGenStage` for the contract.
"""

from __future__ import annotations

from typing import Mapping

from agent.stages.base import Artifact, Stage, StageResult


class HppGenStage(Stage):
    """Generate the storage-layout ``.hpp`` for each active level.

    Requires: ``schema_levels``.
    Produces: ``storage_layout_headers``.

    Contract for the implementation:

    Inputs
        The ``schema_levels`` artifact, and ``cfg.resolved_active_levels()`` -
        which already resolves ``active_levels`` against ``common.levels`` and
        raises on an undeclared name. Each :class:`LevelConfig` carries the
        ``namespace`` to wrap the declarations in and the ``file`` to write.

    Output
        One header per active level, written to
        ``common.gen_project_root / level.file`` (or ``common.out_dir`` when
        set). Register the set as a single ``storage_layout_headers`` artifact -
        a JSON map of level name to written path - so ``query_codegen`` can
        depend on one key rather than a variable number of them.

    Loop
        For each level: generate, then ``workspace.compile_file()`` on the
        header, then on failure render ``fix_compile_errors`` with the
        ``CompileResult.brief()`` diagnostics and retry, up to
        ``compile.max_fix_rounds``. Report the round count per level in
        ``metrics``; a header that only compiles after several rounds is a signal
        the level text was too thin.

    Model
        ``CppRLM`` with ``allow_writes=True`` and ``allow_build=True``, so the
        model can write the header and compile it itself rather than round-trip
        through the stage. Pass the level text as a kwarg, not in the
        instruction.

    Caching
        Key per level on the level text, the prompt fingerprint and the model
        signature. Cache the *accepted* header - the one that compiled - not the
        first attempt.
    """

    name = "hppgen"
    requires = ("schema_levels",)
    produces = ("storage_layout_headers",)
    description = "Generate the storage-layout header for each active hint level."
    default_prompt_ids = ()

    def run(self, inputs: Mapping[str, Artifact]) -> StageResult:
        raise self.not_implemented()
