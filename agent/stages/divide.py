"""Stage 2: split the schema and storage plan into hint levels.

Wired but not implemented. Everything except :meth:`DivideStage.run`'s body is
real: config resolution, prompt binding, artifact keys and cache keying all work
and are covered by the pipeline tests.
"""

from __future__ import annotations

from typing import Mapping

from agent.stages.base import Artifact, Stage, StageResult


class DivideStage(Stage):
    """Split the schema and storage plan into ablation levels.

    Requires: ``storage_plan``.
    Produces: ``schema_levels``.

    Contract for the implementation:

    Inputs
        ``common.inputs.schema`` plus the ``storage_plan`` artifact, and the
        level names declared in ``common.levels``.

    Output
        ``schema_levels`` - JSON of exactly this shape, keyed by the declared
        level names::

            {
              "levels": {"<level_name>": "<level text>"},
              "data_structure_descriptions": {"<level_name>": "<one-sentence description>"}
            }

        Validate before writing: every declared level name must appear in both
        maps, and no extra keys may be present. A level missing from the output
        is the failure mode that quietly breaks ``hppgen`` downstream, so it
        should fail here instead.

    Prompt
        ``divide_policy`` (``prompts/divide_policy.txt``), whose ``${level_names}``
        placeholder takes the comma-separated declared level names. An inline
        ``params.policy`` overrides it - that is where the legacy configs'
        ``divide.policy`` string lands.

    Model
        A single ``dspy.Predict``/``ChainOfThought`` call with a JSON output
        field is sufficient; this is a text-partitioning task with no code
        access, so no workspace or tools should be passed.

    Caching
        Key on the storage-plan content hash, the schema hash, the prompt
        fingerprint and the declared level names, so adding a level re-runs it.
    """

    name = "divide"
    requires = ("storage_plan",)
    produces = ("schema_levels",)
    description = "Split schema and storage plan into no_hints / organization / all_hints levels."
    default_prompt_ids = ("divide_policy",)

    def run(self, inputs: Mapping[str, Artifact]) -> StageResult:
        raise self.not_implemented()
