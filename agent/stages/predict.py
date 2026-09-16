"""One place where a stage turns a signature and a payload into an answer.

Every stage needs the same five steps — register the trace callbacks, configure
DSPy, build the inner LM, choose between RLM and a plain predictor, call it and
pull the usage counters off the prediction — so they live here rather than in
each stage.

The seam is deliberate: tests monkeypatch ``agent.stages.predict.configure_dspy``
to point a whole stage at a ``DummyLM`` while leaving the real path intact, so
prompt rendering, predictor selection, caching, artifact writing and tracing are
all still exercised.

``dspy`` itself is never imported at module scope, here or in the stages: the
CLI's ``doctor`` and ``--dry-run`` paths import every stage to build the graph,
and must work when the ``agent`` extra is not installed.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional, Sequence

from agent.config.models import ResolvedStageConfig
from agent.llm.lm_factory import configure_dspy
from agent.rlm.workspace import CppWorkspace
from agent.trace.callbacks import TraceWriter


def trace_callback(writer: TraceWriter) -> Any:
    """The DSPy callback that records a JSONL trace, imported lazily."""
    from agent.trace.callbacks import JsonlTraceCallback

    return JsonlTraceCallback(writer)


def usage_of(prediction: Any) -> dict[str, Any]:
    """Token usage from a prediction, when ``model.track_usage`` is on."""
    getter = getattr(prediction, "get_lm_usage", None)
    if not callable(getter):
        return {}
    try:
        return getter() or {}
    except Exception:
        return {}


def payload_size(payload: Mapping[str, Any]) -> int:
    """Total characters of a payload, which is what routes RLM vs. predictor."""
    return sum(len(str(value)) for value in payload.values())


def invoke(
    signature: Any,
    payload: Mapping[str, Any],
    outputs: Sequence[str],
    cfg: ResolvedStageConfig,
    writer: TraceWriter,
    stage: str,
    require_api_key: bool = True,
    workspace: Optional[CppWorkspace] = None,
    allow_writes: bool = False,
    allow_build: bool = False,
) -> dict[str, Any]:
    """Call the model once and return a plain dict.

    A dict rather than a ``dspy.Prediction`` because the result is what gets
    cached, and the cache stores JSON.

    Args:
        outputs: Output field names to read off the prediction. Missing fields
            come back as empty strings instead of raising, so a model that omits
            one produces a stage-level "empty output" error naming the field
            rather than an ``AttributeError``.
        workspace: Pass a workspace to give the model the cpplib read/edit/build
            tools. Left None, the stage keeps control of the edit loop, which is
            the default for every stage here; see ``params.rlm_tools``.
    """
    from agent.rlm.cpp_module import build_predictor, build_sub_lm

    callbacks = [trace_callback(writer)]
    configure_dspy(cfg.model, callbacks=callbacks, require_key=require_api_key)

    sub_lm = build_sub_lm(cfg.rlm.sub_model, require_key=require_api_key)
    chars = payload_size(payload)
    module, reason = build_predictor(
        signature,
        payload_chars=chars,
        rlm_config=cfg.rlm,
        workspace=workspace,
        sub_lm=sub_lm,
        writer=writer,
        allow_writes=allow_writes,
        allow_build=allow_build,
    )
    writer.write(
        {
            "event": "predictor_selected",
            "kind": "stage",
            "stage": stage,
            "name": type(module).__name__,
            "outputs": reason,
            "inputs": {"payload_chars": chars, "fields": sorted(payload)},
        }
    )

    prediction = module(**payload)
    result: dict[str, Any] = {name: getattr(prediction, name, "") or "" for name in outputs}
    result["predictor"] = f"{type(module).__name__} ({reason})"
    result["usage"] = usage_of(prediction)
    return result
