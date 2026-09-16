"""Stage 1: propose a storage plan from a schema, a query workload and statistics.

The first stage and the one implemented end-to-end. It reads only text inputs
(no C++ exists yet at this point), so it needs no workspace and exposes no write
tools — the model is reasoning about a design, not editing code.

Routing: the combined inputs are usually well under 100k characters, where RLM's
REPL round-trips cost more than they save, so the stage uses a
``ChainOfThought``. When a workload is large enough to exceed
``rlm.threshold_chars`` it switches to ``dspy.RLM`` with the inputs passed as
sandbox variables, so a workload that would not fit a prompt is still tractable.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Mapping, Optional

from agent.llm.lm_factory import configure_dspy
from agent.stages.base import Artifact, Stage, StageResult
from agent.trace.span import new_span, stage_context


def _signature_class() -> Any:
    """Build the DSPy signature.

    Defined in a function rather than at module scope so that importing this
    module does not require dspy — the CLI's ``doctor`` and ``--dry-run`` paths
    import every stage to build the graph.
    """
    import dspy

    class ProposeStoragePlan(dspy.Signature):
        """Design the in-memory storage layout for a read-only analytical C++ query engine.

        Ground every decision in the schema and the query workload. Do not invent
        tables, columns, indexes or workload characteristics that the inputs do
        not support. The plan is consumed by later code-generation stages, so it
        must be precise enough to implement without further guessing, while
        containing no C++ itself.
        """

        policy: str = dspy.InputField(desc="Instructions describing what the plan must cover.")
        # Named db_schema, not schema: `schema` shadows an attribute on
        # dspy.Signature's pydantic base and DSPy warns about it.
        db_schema: str = dspy.InputField(desc="The database schema: tables, columns, types, keys.")
        queries: str = dspy.InputField(desc="The fixed set of queries the engine must serve.")
        statistics: str = dspy.InputField(
            desc="Optional dataset statistics (row counts, cardinalities). May be empty."
        )
        storage_plan: str = dspy.OutputField(
            desc="The storage plan: physical layout per table, access paths, "
            "data movement, memory budget."
        )
        rationale: str = dspy.OutputField(
            desc="Which queries and schema facts justify the main layout decisions."
        )

    return ProposeStoragePlan


class StoragePlanStage(Stage):
    """Produce a storage plan from the schema, queries and optional statistics.

    Requires: nothing (reads config inputs directly).
    Produces: ``storage_plan`` (text), ``storage_plan_meta`` (JSON).

    Config:
        ``common.inputs.schema``     - required, path to the schema text.
        ``common.inputs.queries``    - required, path to the query workload.
        ``common.inputs.statistics`` - optional.
        ``stages.storage_plan.prompt_ids`` - defaults to ``("storage_plan_policy",)``.
        ``stages.storage_plan.params.policy`` - inline policy text, overriding the prompt file.
    """

    name = "storage_plan"
    requires = ()
    produces = ("storage_plan", "storage_plan_meta")
    description = "Propose an in-memory storage layout from the schema and query workload."
    default_prompt_ids = ("storage_plan_policy",)

    def run(self, inputs: Mapping[str, Artifact]) -> StageResult:
        started = time.perf_counter()
        cfg = self.cfg

        schema_path = cfg.input_path("schema", required=True)
        queries_path = cfg.input_path("queries", required=True)
        statistics_path = cfg.input_path("statistics", required=False)

        schema = _read(schema_path)
        queries = _read(queries_path)
        statistics = _read(statistics_path) if statistics_path else ""

        policy = self.registry.render_any(
            self.prompt_ids()[0] if self.prompt_ids() else None,
            inline=cfg.params.get("policy"),
        )

        # Keys must match the signature's input field names.
        payload = {
            "policy": policy.text,
            "db_schema": schema,
            "queries": queries,
            "statistics": statistics,
        }
        payload_chars = sum(len(v) for v in payload.values())

        key = self.cache.make_key(
            payload,
            prompt_fingerprint=policy.fingerprint,
            model_signature=cfg.model.signature(),
            kind="stage",
        )

        with stage_context(self.name):
            with new_span("stage", self.name, payload_chars=payload_chars):
                value, was_hit = self.cache.memoize(
                    key,
                    lambda: self._invoke(payload, payload_chars),
                    meta={
                        "stage": self.name,
                        "model": cfg.model.name,
                        "prompt_ids": list(policy.prompt_ids),
                    },
                )

        plan_text = value.get("storage_plan", "")
        if not plan_text.strip():
            return self.make_result(
                status="failed",
                error="The model returned an empty storage plan.",
                duration_s=time.perf_counter() - started,
            )

        fingerprint = self.input_fingerprint(inputs)
        meta = {
            "input_fingerprint": fingerprint,
            "model": cfg.model.name,
            "prompt_ids": list(policy.prompt_ids),
            "cache_hit": was_hit,
            "predictor": value.get("predictor", ""),
            "usage": value.get("usage", {}),
            "payload_chars": payload_chars,
            "run_id": self.ctx.run_id,
        }

        plan_artifact = self.store.put_text(
            self.name,
            "storage_plan",
            plan_text,
            meta=meta,
            filename="storage_plan.txt",
            target=cfg.outputs.get("storage_plan"),
        )
        meta_artifact = self.store.put_json(
            self.name,
            "storage_plan_meta",
            {
                **meta,
                "rationale": value.get("rationale", ""),
                "schema_path": str(schema_path),
                "queries_path": str(queries_path),
                "statistics_path": str(statistics_path) if statistics_path else None,
            },
            meta={"input_fingerprint": fingerprint},
        )

        return self.make_result(
            artifacts={"storage_plan": plan_artifact, "storage_plan_meta": meta_artifact},
            metrics={
                "cache_hit": was_hit,
                "plan_chars": len(plan_text),
                "payload_chars": payload_chars,
                "predictor": value.get("predictor", ""),
            },
            duration_s=time.perf_counter() - started,
        )

    # --- model invocation -------------------------------------------------

    def _invoke(self, payload: dict[str, str], payload_chars: int) -> dict[str, Any]:
        """Call the model. Only reached on a cache miss.

        Returns a plain dict rather than a ``dspy.Prediction`` because the
        result is what gets cached, and the cache stores JSON.
        """
        from agent.rlm.cpp_module import build_predictor, build_sub_lm

        cfg = self.cfg
        callbacks = [_callback(self.writer)]
        configure_dspy(cfg.model, callbacks=callbacks, require_key=self.ctx.require_api_key)

        sub_lm = build_sub_lm(cfg.rlm.sub_model, require_key=self.ctx.require_api_key)
        module, reason = build_predictor(
            _signature_class(),
            payload_chars=payload_chars,
            rlm_config=cfg.rlm,
            # No workspace: this stage reasons about a design, so it gets no
            # code-editing tools at all.
            workspace=None,
            sub_lm=sub_lm,
            writer=self.writer,
        )
        self.writer.write(
            {
                "event": "predictor_selected",
                "kind": "stage",
                "stage": self.name,
                "name": type(module).__name__,
                "outputs": reason,
            }
        )

        prediction = module(**payload)
        return {
            "storage_plan": getattr(prediction, "storage_plan", "") or "",
            "rationale": getattr(prediction, "rationale", "") or "",
            "predictor": f"{type(module).__name__} ({reason})",
            "usage": _usage_of(prediction),
        }


def _callback(writer: Any) -> Any:
    """The trace callback, imported lazily so the module loads without dspy."""
    from agent.stages.predict import trace_callback

    return trace_callback(writer)


def _usage_of(prediction: Any) -> dict[str, Any]:
    """Token usage from a prediction, when ``track_usage`` is on."""
    from agent.stages.predict import usage_of

    return usage_of(prediction)


def _read(path: Optional[Path]) -> str:
    """Read a text input, tolerating encoding problems in vendor-supplied files."""
    if path is None:
        return ""
    return Path(path).read_text(encoding="utf-8", errors="replace")
