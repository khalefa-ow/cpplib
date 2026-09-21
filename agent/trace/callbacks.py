"""DSPy invocation hooks that record a JSONL trace.

Implements every ``BaseCallback`` hook DSPy 3.3 exposes, including the
interpreter-level ones that matter most for RLM:

- ``on_module_start/end`` — a predictor or module invocation.
- ``on_lm_start/end`` — one completion, including the RLM's recursive sub-calls.
- ``on_tool_start/end`` — a tool called through DSPy's ``Tool`` wrapper.
- ``on_interpreter_startup/shutdown`` — the Deno/Pyodide sandbox lifecycle.
- ``on_interpreter_execute_start/end`` — each REPL step the RLM runs. This is
  the RLM's actual reasoning trace: the code it wrote and what came back.
- ``on_interpreter_tool_call_start/end`` — a host tool invoked from inside the
  sandbox, i.e. every cpplib call the model makes.
- ``on_adapter_format/parse`` — prompt construction and output parsing, which is
  where malformed-output failures show up.

The base class defines all hooks as no-ops, so subclassing keeps this working if
DSPy adds more.
"""

from __future__ import annotations

import json
import re
import sys
import threading
import time
from pathlib import Path
from typing import Any, Optional, cast

from agent.trace.span import (
    current_run_id,
    current_span_id,
    current_stage,
    new_id,
    pop_span,
    push_span,
)

# Keys whose values must never reach a trace file.
#
# Anchored and segment-aware rather than a loose substring search: matching
# "token" anywhere would also redact `prompt_tokens` and `completion_tokens`,
# silently destroying the usage counts this trace exists to record. The optional
# leading group allows prefixed names (`x_api_key`, `access_token`) while the
# `$` anchor keeps plural counters (`total_tokens`) intact.
REDACT_PATTERN = re.compile(
    r"^(?:[\w-]*[_-])?"
    r"(?:api[_-]?key|apikey|authorization|auth|secret|password|passwd|pwd"
    r"|token|bearer|credentials?)$",
    re.I,
)
REDACTED = "<redacted>"


def _base_callback_class() -> type:
    """Return ``dspy.utils.callback.BaseCallback``, or ``object`` if absent.

    Falling back to ``object`` keeps this module importable without dspy, so the
    trace layer can be unit-tested and ``doctor`` can run without the extra.
    """
    try:
        from dspy.utils.callback import BaseCallback

        return cast(type, BaseCallback)
    except Exception:  # pragma: no cover - depends on env
        return object


_Base = _base_callback_class()


class TraceWriter:
    """Serializes trace records to a JSONL file and/or stdout.

    One lock guards the file handle: DSPy evaluates in threads, and interleaved
    partial lines would make the file unparseable.
    """

    def __init__(
        self,
        path: Optional[str | Path] = None,
        stdout: bool = False,
        max_field_chars: int = 4000,
    ):
        self.path = Path(path).expanduser() if path else None
        self.stdout = stdout
        self.max_field_chars = max_field_chars
        self.records: list[dict[str, Any]] = []
        # Kept in memory as well so tests and stage summaries can assert on the
        # trace without re-reading the file.
        self.keep_in_memory = True
        self._lock = threading.Lock()
        self._handle = None
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._handle = self.path.open("a", encoding="utf-8")

    # --- sanitizing -------------------------------------------------------

    def sanitize(self, value: Any, depth: int = 0) -> Any:
        """Make a value safe and small enough to write.

        Redacts credential-shaped keys, truncates long strings (a whole C++ file
        or a 100k-token context would otherwise dominate the trace), and renders
        anything non-JSON-serializable as a short repr.
        """
        if depth > 6:
            return "<max depth>"
        if value is None or isinstance(value, (bool, int, float)):
            return value
        if isinstance(value, str):
            if len(value) <= self.max_field_chars:
                return value
            return value[: self.max_field_chars] + f"…<+{len(value) - self.max_field_chars} chars>"
        if isinstance(value, dict):
            out: dict[str, Any] = {}
            for key, item in value.items():
                name = str(key)
                out[name] = (
                    REDACTED if REDACT_PATTERN.search(name) else self.sanitize(item, depth + 1)
                )
            return out
        if isinstance(value, (list, tuple, set)):
            items = list(value)
            trimmed = [self.sanitize(v, depth + 1) for v in items[:50]]
            if len(items) > 50:
                trimmed.append(f"<+{len(items) - 50} more>")
            return trimmed
        # dspy Predictions, LMs, modules, exceptions: identify without dumping.
        text = repr(value)
        if len(text) > self.max_field_chars:
            text = text[: self.max_field_chars] + "…"
        return text

    # --- writing ----------------------------------------------------------

    def write(self, record: dict[str, Any]) -> dict[str, Any]:
        """Write one record. Never raises: tracing must not fail a run."""
        record.setdefault("ts", time.time())
        try:
            safe = {k: self.sanitize(v) for k, v in record.items()}
        except Exception:
            safe = {"event": record.get("event", "unknown"), "error": "trace serialization failed"}
        line = json.dumps(safe, ensure_ascii=False, default=repr)
        with self._lock:
            if self.keep_in_memory:
                self.records.append(safe)
            if self._handle is not None:
                try:
                    self._handle.write(line + "\n")
                    self._handle.flush()
                except Exception:
                    pass
            if self.stdout:
                try:
                    sys.stdout.write(line + "\n")
                except Exception:
                    pass
        return safe

    def close(self) -> None:
        with self._lock:
            if self._handle is not None:
                try:
                    self._handle.close()
                finally:
                    self._handle = None

    def __enter__(self) -> "TraceWriter":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # --- querying (tests, stage summaries) --------------------------------

    def events(
        self, event: Optional[str] = None, kind: Optional[str] = None
    ) -> list[dict[str, Any]]:
        """In-memory records, optionally filtered."""
        out = self.records
        if event is not None:
            out = [r for r in out if r.get("event") == event]
        if kind is not None:
            out = [r for r in out if r.get("kind") == kind]
        return list(out)

    def usage_totals(self) -> dict[str, int]:
        """Sum token usage across every traced LM call."""
        totals: dict[str, int] = {}
        for record in self.records:
            usage = record.get("usage")
            if not isinstance(usage, dict):
                continue
            for key, value in usage.items():
                if isinstance(value, int):
                    totals[key] = totals.get(key, 0) + value
        return totals


class JsonlTraceCallback(_Base):  # type: ignore[misc,valid-type]
    """Bridges DSPy's callback hooks onto :class:`TraceWriter`.

    Each ``*_start`` opens a span keyed by DSPy's ``call_id`` and pushes it so
    that nested calls record it as their parent; the matching ``*_end`` pops it
    and records the duration. Unmatched ends (a crash between the two) are
    tolerated rather than raising.
    """

    def __init__(self, writer: TraceWriter):
        self.writer = writer
        # call_id -> (span_id, contextvar token, start time, kind, name)
        self._open: dict[str, tuple[str, Any, float, str, str]] = {}
        self._lock = threading.Lock()

    # --- span bookkeeping -------------------------------------------------

    def _start(self, call_id: str, kind: str, instance: Any, inputs: Any, name: str = "") -> None:
        span_id = new_id()
        label = name or _describe(instance)
        token = push_span(span_id)
        with self._lock:
            self._open[call_id] = (span_id, token, time.perf_counter(), kind, label)
        record: dict[str, Any] = {
            "event": f"{kind}_start",
            "kind": kind,
            "name": label,
            "run_id": current_run_id(),
            "stage": current_stage(),
            "span_id": span_id,
            # current_span_id() is now this span, so the parent is one below.
            "parent_span_id": _parent_of(span_id),
            "call_id": call_id,
            "inputs": inputs,
        }
        tools = _tool_names(instance)
        if tools:
            record["tools"] = tools
        self.writer.write(record)

    def _end(
        self, call_id: str, kind: str, outputs: Any, exception: Optional[BaseException]
    ) -> None:
        with self._lock:
            opened = self._open.pop(call_id, None)
        span_id, token, started, opened_kind, label = opened or (
            new_id(),
            None,
            time.perf_counter(),
            kind,
            "",
        )
        if token is not None:
            pop_span(token)
        record: dict[str, Any] = {
            "event": f"{opened_kind}_end",
            "kind": opened_kind,
            "name": label,
            "run_id": current_run_id(),
            "stage": current_stage(),
            "span_id": span_id,
            "parent_span_id": current_span_id(),
            "call_id": call_id,
            "duration_ms": round((time.perf_counter() - started) * 1000.0, 3),
            "outputs": outputs,
            "error": None if exception is None else f"{type(exception).__name__}: {exception}",
        }
        usage = _extract_usage(outputs)
        if usage:
            record["usage"] = usage
        self.writer.write(record)

    # --- module ----------------------------------------------------------

    def on_module_start(self, call_id: str, instance: Any, inputs: dict[str, Any]) -> None:
        self._start(call_id, "module", instance, inputs)

    def on_module_end(
        self, call_id: str, outputs: Any, exception: Optional[BaseException] = None
    ) -> None:
        self._end(call_id, "module", outputs, exception)

    # --- language model --------------------------------------------------

    def on_lm_start(self, call_id: str, instance: Any, inputs: dict[str, Any]) -> None:
        self._start(
            call_id,
            "lm",
            instance,
            inputs,
            name=getattr(instance, "model", "") or _describe(instance),
        )

    def on_lm_end(
        self,
        call_id: str,
        outputs: Optional[dict[str, Any]],
        exception: Optional[BaseException] = None,
    ) -> None:
        self._end(call_id, "lm", outputs, exception)

    # --- tools -----------------------------------------------------------

    def on_tool_start(self, call_id: str, instance: Any, inputs: dict[str, Any]) -> None:
        self._start(
            call_id,
            "tool",
            instance,
            inputs,
            name=getattr(instance, "name", "") or _describe(instance),
        )

    def on_tool_end(
        self,
        call_id: str,
        outputs: Optional[dict[str, Any]],
        exception: Optional[BaseException] = None,
    ) -> None:
        self._end(call_id, "tool", outputs, exception)

    # --- RLM interpreter --------------------------------------------------
    # These are the hooks that make an RLM run legible: the code it executed,
    # what the sandbox returned, and which host tools it reached for.

    def on_interpreter_startup_start(
        self, call_id: str, instance: Any, inputs: dict[str, Any]
    ) -> None:
        self._start(call_id, "interpreter_startup", instance, inputs)

    def on_interpreter_startup_end(
        self, call_id: str, outputs: Any, exception: Optional[BaseException] = None
    ) -> None:
        self._end(call_id, "interpreter_startup", outputs, exception)

    def on_interpreter_execute_start(
        self, call_id: str, instance: Any, inputs: dict[str, Any]
    ) -> None:
        self._start(call_id, "interpreter_execute", instance, inputs)

    def on_interpreter_execute_end(
        self, call_id: str, outputs: Any, exception: Optional[BaseException] = None
    ) -> None:
        self._end(call_id, "interpreter_execute", outputs, exception)

    def on_interpreter_tool_call_start(
        self, call_id: str, instance: Any, inputs: dict[str, Any]
    ) -> None:
        self._start(call_id, "interpreter_tool_call", instance, inputs)

    def on_interpreter_tool_call_end(
        self, call_id: str, outputs: Any, exception: Optional[BaseException] = None
    ) -> None:
        self._end(call_id, "interpreter_tool_call", outputs, exception)

    def on_interpreter_shutdown_start(
        self, call_id: str, instance: Any, inputs: dict[str, Any]
    ) -> None:
        self._start(call_id, "interpreter_shutdown", instance, inputs)

    def on_interpreter_shutdown_end(
        self, call_id: str, outputs: Any, exception: Optional[BaseException] = None
    ) -> None:
        self._end(call_id, "interpreter_shutdown", outputs, exception)

    # --- adapter ---------------------------------------------------------

    def on_adapter_format_start(self, call_id: str, instance: Any, inputs: dict[str, Any]) -> None:
        self._start(call_id, "adapter_format", instance, inputs)

    def on_adapter_format_end(
        self,
        call_id: str,
        outputs: Optional[dict[str, Any]],
        exception: Optional[BaseException] = None,
    ) -> None:
        self._end(call_id, "adapter_format", outputs, exception)

    def on_adapter_parse_start(self, call_id: str, instance: Any, inputs: dict[str, Any]) -> None:
        self._start(call_id, "adapter_parse", instance, inputs)

    def on_adapter_parse_end(
        self,
        call_id: str,
        outputs: Optional[dict[str, Any]],
        exception: Optional[BaseException] = None,
    ) -> None:
        self._end(call_id, "adapter_parse", outputs, exception)


def _parent_of(span_id: str) -> Optional[str]:
    """The span below ``span_id`` on the current stack."""
    from agent.trace.span import _span_stack  # local: internal to the span module

    stack = _span_stack.get()
    if span_id in stack:
        index = stack.index(span_id)
        return stack[index - 1] if index > 0 else None
    return stack[-1] if stack else None


def _describe(instance: Any) -> str:
    """A short label for whatever DSPy passed as the callback's instance."""
    for attr in ("name", "__name__"):
        value = getattr(instance, attr, None)
        if isinstance(value, str) and value:
            return value
    return type(instance).__name__


def _tool_names(instance: Any) -> list[str]:
    """The names of the tools a module instance was built with, if any.

    ``CppRLM`` (``agent/rlm/cpp_module.py``) keeps its tool list on the public
    ``.tools`` attribute - plain callables, each carrying its original
    ``__name__`` through ``functools.wraps`` (see
    ``agent/rlm/cpp_tools.py``'s ``_traced`` decorator). The ``dspy.RLM``
    instance it wraps normalizes the same list onto a private
    ``._user_tools`` dict of ``dspy.Tool`` keyed by name instead, so both are
    checked - one or the other resolves for every module actually built by
    this pipeline. Neither exists on a plain ``Predict``/``ChainOfThought``,
    which is the common case, so this quietly returns ``[]`` there.
    """
    tools = getattr(instance, "tools", None)
    if not tools:
        tools = getattr(instance, "_user_tools", None)
    if not tools:
        return []
    if isinstance(tools, dict):
        return sorted(tools.keys())
    names = []
    for tool in tools:
        name = getattr(tool, "name", None) or getattr(tool, "__name__", None) or type(tool).__name__
        names.append(str(name))
    return names


def _extract_usage(outputs: Any) -> dict[str, int]:
    """Pull token counts out of an LM response, if present.

    LiteLLM responses carry usage in a few shapes depending on provider and
    whether streaming was used, so this checks the common ones and gives up
    quietly rather than guessing.
    """
    candidates: list[Any] = []
    if isinstance(outputs, dict):
        candidates.extend([outputs.get("usage"), outputs.get("token_usage")])
        for key in ("response", "raw"):
            nested = outputs.get(key)
            if nested is not None:
                candidates.append(getattr(nested, "usage", None))
    candidates.append(getattr(outputs, "usage", None))

    for candidate in candidates:
        if candidate is None:
            continue
        data = candidate
        if not isinstance(data, dict):
            data = getattr(candidate, "model_dump", lambda: None)() or getattr(
                candidate, "__dict__", None
            )
        if isinstance(data, dict):
            usage = {k: v for k, v in data.items() if isinstance(v, int)}
            if usage:
                return usage
    return {}
