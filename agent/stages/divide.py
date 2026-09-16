"""Stage 2: split the schema and storage plan into hint levels.

This is the stage that makes the pipeline an experiment rather than a one-shot
generator: the same engine is generated once per level, so the value of the
storage plan can be measured by how much the generated code improves as more of
the plan is allowed through.

The output shape is validated before it is written. A level that is missing from
the model's JSON would otherwise surface two stages later as a ``KeyError`` in
``hppgen``, long after the run that produced it, so the check belongs here.
"""

from __future__ import annotations

import time
from typing import Any, Mapping

from agent.errors import AgentError
from agent.stages.base import Artifact, Stage, StageResult
from agent.stages.parsing import ParseError, extract_json
from agent.stages.predict import invoke
from agent.trace.span import new_span, stage_context

LEVELS_KEY = "levels"
DESCRIPTIONS_KEY = "data_structure_descriptions"


def _signature_class() -> Any:
    """Build the DSPy signature.

    In a function rather than at module scope so importing this module does not
    require dspy; ``doctor`` and ``--dry-run`` import every stage.
    """
    import dspy

    class SplitIntoLevels(dspy.Signature):
        """Split a schema and its storage plan into nested hint levels.

        Each level is a self-contained description handed to a code generator
        that sees nothing else. Levels are cumulative in information: a later
        level may add hints, never remove schema facts.

        Move information between levels; do not create it. Every fact in every
        level must be present in the schema or the storage plan. If a level's
        policy forbids hints, that level must contain none — not softened ones.
        """

        policy: str = dspy.InputField(desc="How to split the inputs, and what each level may hold.")
        # Named db_schema, not schema: `schema` shadows an attribute on
        # dspy.Signature's pydantic base and DSPy warns about it.
        db_schema: str = dspy.InputField(desc="The database schema: tables, columns, types, keys.")
        storage_plan: str = dspy.InputField(desc="The storage plan produced by the previous stage.")
        level_names: str = dspy.InputField(desc="Comma-separated level names to produce, in order.")
        schema_levels: str = dspy.OutputField(
            desc=(
                'JSON only, exactly: {"levels": {"<level_name>": "<level text>"}, '
                '"data_structure_descriptions": {"<level_name>": "<one sentence>"}}. '
                "Use the given level names verbatim as keys, and add no other keys."
            )
        )

    return SplitIntoLevels


class LevelValidationError(AgentError):
    """The model's level split does not match the declared levels."""


class DivideStage(Stage):
    """Split the schema and storage plan into ablation levels.

    Requires: ``storage_plan``.
    Produces: ``schema_levels``.

    Config:
        ``common.levels`` - the levels to produce. Their names become the JSON
            keys every later stage looks up.
        ``stages.divide.prompt_ids`` - defaults to ``("divide_policy",)``, whose
            ``${level_names}`` placeholder is filled from ``common.levels``.
        ``stages.divide.params.policy`` - inline policy text, overriding the
            prompt file. This is where the legacy configs' ``divide.policy``
            string lands.

    Output artifact ``schema_levels`` is JSON of exactly this shape::

        {
          "levels": {"<level_name>": "<level text>"},
          "data_structure_descriptions": {"<level_name>": "<one-sentence description>"}
        }

    No workspace and no tools: this is a text-partitioning task, and a stage
    that only reorganizes prose has no business holding a file-writing tool.
    """

    name = "divide"
    requires = ("storage_plan",)
    produces = ("schema_levels",)
    description = "Split schema and storage plan into no_hints / organization / all_hints levels."
    default_prompt_ids = ("divide_policy",)

    def run(self, inputs: Mapping[str, Artifact]) -> StageResult:
        started = time.perf_counter()
        cfg = self.cfg

        levels = cfg.levels
        if not levels:
            return self.make_result(
                status="failed",
                error=(
                    "No levels are declared. Set common.levels to at least one "
                    "{name, namespace, file} entry; its names become the keys every "
                    "later stage looks up."
                ),
                duration_s=time.perf_counter() - started,
            )
        names = [level.name for level in levels]

        schema_path = cfg.input_path("schema", required=True)
        schema = schema_path.read_text(encoding="utf-8", errors="replace") if schema_path else ""
        plan = inputs["storage_plan"].read_text()

        policy = self.registry.render_any(
            self.prompt_ids()[0] if self.prompt_ids() else None,
            inline=cfg.params.get("policy"),
            level_names=", ".join(names),
        )

        payload = {
            "policy": policy.text,
            "db_schema": schema,
            "storage_plan": plan,
            "level_names": ", ".join(names),
        }
        key = self.cache.make_key(
            payload,
            prompt_fingerprint=policy.fingerprint,
            model_signature=cfg.model.signature(),
            kind="stage",
        )

        with stage_context(self.name):
            with new_span("stage", self.name, levels=len(names)):
                try:
                    value, was_hit = self.cache.memoize(
                        key,
                        lambda: self._invoke(payload, names),
                        meta={
                            "stage": self.name,
                            "model": cfg.model.name,
                            "prompt_ids": list(policy.prompt_ids),
                            "levels": names,
                        },
                    )
                except (ParseError, LevelValidationError) as exc:
                    # Not cached: a malformed split is a transient model failure,
                    # and caching it would make every retry return the same
                    # broken answer until the cache was cleared by hand.
                    return self.make_result(
                        status="failed",
                        error=str(exc),
                        duration_s=time.perf_counter() - started,
                    )

        split = {
            LEVELS_KEY: value[LEVELS_KEY],
            DESCRIPTIONS_KEY: value[DESCRIPTIONS_KEY],
        }
        fingerprint = self.input_fingerprint(inputs)
        meta = {
            "input_fingerprint": fingerprint,
            "model": cfg.model.name,
            "prompt_ids": list(policy.prompt_ids),
            "cache_hit": was_hit,
            "predictor": value.get("predictor", ""),
            "usage": value.get("usage", {}),
            "levels": names,
            "run_id": self.ctx.run_id,
        }
        artifact = self.store.put_json(
            self.name,
            "schema_levels",
            split,
            meta=meta,
            target=cfg.outputs.get("schema_levels"),
        )

        return self.make_result(
            artifacts={"schema_levels": artifact},
            metrics={
                "cache_hit": was_hit,
                "levels": len(names),
                "predictor": value.get("predictor", ""),
                **{f"chars[{name}]": len(split[LEVELS_KEY][name]) for name in names},
            },
            duration_s=time.perf_counter() - started,
        )

    # --- model invocation -------------------------------------------------

    def _invoke(self, payload: dict[str, str], names: list[str]) -> dict[str, Any]:
        """Call the model and validate the split. Only reached on a cache miss."""
        value = invoke(
            _signature_class(),
            payload,
            outputs=("schema_levels",),
            cfg=self.cfg,
            writer=self.writer,
            stage=self.name,
            require_api_key=self.ctx.require_api_key,
        )
        parsed = extract_json(value["schema_levels"], what="schema_levels output")
        levels, descriptions = validate_split(parsed, names)
        return {
            LEVELS_KEY: levels,
            DESCRIPTIONS_KEY: descriptions,
            "predictor": value["predictor"],
            "usage": value["usage"],
        }


def validate_split(parsed: Any, names: list[str]) -> tuple[dict[str, str], dict[str, str]]:
    """Check a parsed split covers exactly the declared levels.

    Both maps must hold every declared name and nothing else, and no level text
    may be empty. An empty level is worse than a missing one: ``hppgen`` would
    accept it and generate a header from nothing.
    """
    if not isinstance(parsed, dict):
        raise LevelValidationError(
            f"Expected a JSON object with '{LEVELS_KEY}' and '{DESCRIPTIONS_KEY}', "
            f"got {type(parsed).__name__}."
        )

    levels = parsed.get(LEVELS_KEY)
    descriptions = parsed.get(DESCRIPTIONS_KEY)
    if not isinstance(levels, dict):
        raise LevelValidationError(f"'{LEVELS_KEY}' is missing or is not a JSON object.")
    if not isinstance(descriptions, dict):
        # Recoverable: the descriptions are a convenience for the generator,
        # while the level text is the actual product of this stage.
        descriptions = {}

    missing = [name for name in names if name not in levels]
    extra = [key for key in levels if key not in names]
    if missing or extra:
        problems = []
        if missing:
            problems.append(f"missing level(s): {', '.join(missing)}")
        if extra:
            problems.append(f"unexpected level(s): {', '.join(sorted(extra))}")
        raise LevelValidationError(
            f"The split does not match common.levels. "
            f"{'; '.join(problems)}. Declared levels: {', '.join(names)}."
        )

    empty = [name for name in names if not str(levels[name]).strip()]
    if empty:
        raise LevelValidationError(
            f"Level text is empty for: {', '.join(empty)}. "
            f"A level with no content would produce a header generated from nothing."
        )

    return (
        {name: str(levels[name]).strip() for name in names},
        {name: str(descriptions.get(name, "")).strip() for name in names},
    )
