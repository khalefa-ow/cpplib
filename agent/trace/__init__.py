"""JSONL tracing for DSPy invocations, plus optional weave/wandb."""

from agent.trace.callbacks import JsonlTraceCallback, TraceWriter
from agent.trace.integrations import (
    finish_wandb,
    log_metrics,
    maybe_init_wandb,
    maybe_init_weave,
)
from agent.trace.span import (
    Span,
    current_run_id,
    current_span_id,
    current_stage,
    new_id,
    new_span,
    run_context,
    stage_context,
)

__all__ = [
    "JsonlTraceCallback",
    "Span",
    "TraceWriter",
    "current_run_id",
    "current_span_id",
    "current_stage",
    "finish_wandb",
    "log_metrics",
    "maybe_init_wandb",
    "maybe_init_weave",
    "new_id",
    "new_span",
    "run_context",
    "stage_context",
]
