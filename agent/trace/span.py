"""Run and span identity, carried in context variables.

DSPy hands each callback a ``call_id`` that correlates a ``*_start`` with its
matching ``*_end``, but it does not say which call *contains* which. An RLM
invocation produces a module span, interpreter execute spans, tool calls and a
fan of sub-LM calls; without parentage the trace is a flat pile of events.

A context-variable stack gives that parentage for free, and because it is a
``ContextVar`` it survives DSPy's threaded evaluation without spans from one
worker adopting another's parent.
"""

from __future__ import annotations

import contextlib
import time
import uuid
from contextvars import ContextVar
from typing import Any, Iterator, Optional

from pydantic import BaseModel, ConfigDict, Field

_run_id: ContextVar[Optional[str]] = ContextVar("agent_run_id", default=None)
_stage: ContextVar[Optional[str]] = ContextVar("agent_stage", default=None)
_span_stack: ContextVar[tuple[str, ...]] = ContextVar("agent_span_stack", default=())


def new_id() -> str:
    """A short unique id. 16 hex chars is plenty for one run's spans."""
    return uuid.uuid4().hex[:16]


class Span(BaseModel):
    """One timed, parented unit of work."""

    model_config = ConfigDict(extra="forbid")

    span_id: str
    parent_span_id: Optional[str] = None
    run_id: Optional[str] = None
    stage: Optional[str] = None
    kind: str = "span"
    name: str = ""
    started_at: float = Field(default_factory=time.time)
    ended_at: Optional[float] = None
    meta: dict[str, Any] = Field(default_factory=dict)

    def duration_ms(self) -> Optional[float]:
        if self.ended_at is None:
            return None
        return (self.ended_at - self.started_at) * 1000.0


# --- ambient accessors ----------------------------------------------------


def current_run_id() -> Optional[str]:
    return _run_id.get()


def current_stage() -> Optional[str]:
    return _stage.get()


def current_span_id() -> Optional[str]:
    stack = _span_stack.get()
    return stack[-1] if stack else None


@contextlib.contextmanager
def run_context(run_id: Optional[str] = None, stage: Optional[str] = None) -> Iterator[str]:
    """Establish the run (and optionally stage) identity for a block."""
    rid = run_id or new_id()
    run_token = _run_id.set(rid)
    stage_token = _stage.set(stage)
    stack_token = _span_stack.set(())
    try:
        yield rid
    finally:
        _span_stack.reset(stack_token)
        _stage.reset(stage_token)
        _run_id.reset(run_token)


@contextlib.contextmanager
def stage_context(stage: str) -> Iterator[str]:
    """Tag every span opened inside the block with a stage name."""
    token = _stage.set(stage)
    try:
        yield stage
    finally:
        _stage.reset(token)


@contextlib.contextmanager
def new_span(kind: str, name: str = "", **meta: Any) -> Iterator[Span]:
    """Open a child of the current span for the duration of the block.

    The span is pushed before the body runs, so anything the body triggers -
    including DSPy callbacks fired deep inside a module - sees it as the parent.
    """
    span = Span(
        span_id=new_id(),
        parent_span_id=current_span_id(),
        run_id=current_run_id(),
        stage=current_stage(),
        kind=kind,
        name=name,
        meta=dict(meta),
    )
    token = _span_stack.set(_span_stack.get() + (span.span_id,))
    try:
        yield span
    finally:
        span.ended_at = time.time()
        _span_stack.reset(token)


def push_span(span_id: str) -> Any:
    """Push a span id manually. Returns a token for :func:`pop_span`.

    Needed by the DSPy callback bridge, where ``on_*_start`` and ``on_*_end``
    are separate calls and so cannot use a ``with`` block.
    """
    return _span_stack.set(_span_stack.get() + (span_id,))


def pop_span(token: Any) -> None:
    """Undo a :func:`push_span`."""
    try:
        _span_stack.reset(token)
    except ValueError:
        # The token belongs to a different context (DSPy ran the end callback on
        # another thread). Dropping the reset is correct: that context's stack
        # goes away with it.
        pass
