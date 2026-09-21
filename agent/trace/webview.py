"""A step-by-step web view of a run's JSONL trace.

The trace already carries everything a viewer needs (see
:mod:`agent.trace.callbacks` and :mod:`agent.trace.span`): every module/LM/tool/
interpreter event, with prompts, responses, timing, token usage and span
parentage. This module only reads that back and renders it - no new capture
logic. It re-parses the trace file on every request instead of caching it, so
the same server works both for a finished run and for one a pipeline is still
appending to (:meth:`TraceWriter.write` flushes every record).
"""

from __future__ import annotations

import json
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Optional

# Kinds that place a step in the pipeline, outermost first. Interpreter/adapter
# events are left out of the breadcrumb - they add noise to "which LLM call is
# this", not context.
_BREADCRUMB_KINDS = {"run", "stage", "level", "query", "round", "module"}


def parse_trace_file(path: Path) -> list[dict[str, Any]]:
    """Read a JSONL trace file, skipping any line that fails to parse.

    A trace being tailed live can have a partially-written last line; it is
    dropped rather than failing the whole read.
    """
    try:
        text = Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    records: list[dict[str, Any]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records


def _breadcrumb(parent_span_id: Optional[str], spans: dict[str, dict[str, Any]]) -> list[str]:
    crumbs: list[str] = []
    seen: set[str] = set()
    current = parent_span_id
    while current and current in spans and current not in seen:
        seen.add(current)
        entry = spans[current]
        kind = entry.get("kind")
        if kind in _BREADCRUMB_KINDS:
            name = entry.get("name") or ""
            crumbs.append(f"{kind}={name}" if name else str(kind))
        current = entry.get("parent_span_id")
    crumbs.reverse()
    return crumbs


def _nearest_module_span(
    parent_span_id: Optional[str], spans: dict[str, dict[str, Any]]
) -> Optional[dict[str, Any]]:
    """The nearest ancestor ``module``-kind span entry, if any.

    Every ``dspy.Module`` subclass's ``__call__`` fires a ``module`` span (see
    ``JsonlTraceCallback.on_module_start``/``on_module_end``), including
    nested ones - an RLM call fires one for the ``CppRLM`` wrapper and another,
    closer one for the inner ``dspy.RLM`` instance whose sub-completions
    actually produce the ``lm`` spans. Walking up and stopping at the first
    match gives the concrete predictor class (``RLM``, ``ChainOfThought``,
    ``Predict``, ...) rather than the outer wrapper, and that same span also
    carries the ``tools`` list the module was built with (see
    ``JsonlTraceCallback._start``), so callers get both from one lookup.
    """
    current = parent_span_id
    seen: set[str] = set()
    while current and current in spans and current not in seen:
        seen.add(current)
        entry = spans[current]
        if entry.get("kind") == "module":
            return entry
        current = entry.get("parent_span_id")
    return None


def build_steps(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Turn a flat trace into one ordered step per LLM call.

    Start and end records for one call share a ``span_id`` (see
    ``JsonlTraceCallback._start``/``_end`` and ``new_span``'s ``writer``
    support), so merging on that key reconstructs one entry per call regardless
    of whether it came from a DSPy callback or a plain ``new_span(writer=...)``.
    """
    spans: dict[str, dict[str, Any]] = {}
    for record in records:
        span_id = record.get("span_id")
        if not span_id:
            continue
        entry = spans.setdefault(span_id, {})
        event = str(record.get("event", ""))
        for key, value in record.items():
            if key == "event":
                continue
            entry[key] = value
        if event.endswith("_start"):
            entry["start_ts"] = record.get("ts")
        elif event.endswith("_end"):
            entry["end_ts"] = record.get("ts")

    steps: list[dict[str, Any]] = []
    for span_id, entry in spans.items():
        if entry.get("kind") != "lm":
            continue
        module_span = _nearest_module_span(entry.get("parent_span_id"), spans)
        steps.append(
            {
                "span_id": span_id,
                "run_id": entry.get("run_id"),
                "stage": entry.get("stage"),
                "model": entry.get("name"),
                "module": (module_span.get("name") or None) if module_span else None,
                "tools": (module_span.get("tools") or []) if module_span else [],
                "breadcrumb": _breadcrumb(entry.get("parent_span_id"), spans),
                "ts": entry.get("start_ts", entry.get("end_ts")),
                "duration_ms": entry.get("duration_ms"),
                "inputs": entry.get("inputs"),
                "outputs": entry.get("outputs"),
                "usage": entry.get("usage"),
                "error": entry.get("error"),
            }
        )
    steps.sort(key=lambda s: s["ts"] if s["ts"] is not None else 0.0)
    for index, step in enumerate(steps):
        step["index"] = index
    return steps


_PAGE = """<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>LLM call trace</title>
<style>
  :root { color-scheme: light dark; }
  body { font-family: -apple-system, Segoe UI, Helvetica, Arial, sans-serif; margin: 0;
         display: flex; height: 100vh; }
  #sidebar { width: 320px; overflow-y: auto; border-right: 1px solid #8884; flex-shrink: 0; }
  #sidebar .item { padding: 8px 10px; cursor: pointer; border-bottom: 1px solid #8882;
                    font-size: 13px; }
  #sidebar .item:hover { background: #8882; }
  #sidebar .item.selected { background: #4a90d966; }
  #sidebar .item .crumb { opacity: 0.7; font-size: 11px; }
  #sidebar .item.error { border-left: 3px solid #d9534f; }
  .badge { display: inline-block; padding: 1px 7px; border-radius: 9px; font-size: 11px;
           color: #fff; margin-right: 6px; vertical-align: middle; }
  #main { flex: 1; overflow-y: auto; padding: 16px 24px; }
  #nav { margin-bottom: 12px; }
  #nav button { padding: 4px 12px; margin-right: 8px; }
  h2 { margin-top: 0; }
  .meta { color: #888; font-size: 13px; margin-bottom: 12px; }
  .meta.tools { margin-top: -8px; }
  .error-banner { background: #d9534f33; border: 1px solid #d9534f; padding: 8px;
                   margin-bottom: 12px; border-radius: 4px; white-space: pre-wrap; }
  pre { background: #8881; padding: 10px; border-radius: 4px; overflow-x: auto;
        white-space: pre-wrap; word-break: break-word; }
  #empty { padding: 24px; color: #888; }
</style>
</head>
<body>
<div id="sidebar"></div>
<div id="main"><div id="empty">Waiting for LLM calls...</div></div>
<script>
let steps = [];
let selected = null;

function escapeHtml(text) {
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

// A fixed, deterministic palette so the same module name (RLM, ChainOfThought,
// Predict, ...) always gets the same color without hardcoding every class DSPy
// might ship - any new module type just picks a color by name hash.
const MODULE_COLORS = [
  "#8e44ad", "#2980b9", "#16a085", "#d35400",
  "#c0392b", "#2c3e50", "#27ae60", "#8e5b3f",
];
function moduleColor(name) {
  let hash = 0;
  for (let i = 0; i < name.length; i++) hash = (hash * 31 + name.charCodeAt(i)) >>> 0;
  return MODULE_COLORS[hash % MODULE_COLORS.length];
}
function moduleBadge(name) {
  if (!name) return "";
  return "<span class=\\"badge\\" style=\\"background:" + moduleColor(name) + "\\">" +
         escapeHtml(name) + "</span>";
}

// C++ source is full of "<...>" (templates, includes), which a naive
// innerHTML insert would parse as tags and silently swallow or reorder - that
// is why a response could look empty. Escape first, then format: escaping a
// string leaves its real newlines alone, but JSON.stringify-ing a dict escapes
// any newline *inside* a string value as a literal backslash-n, so that gets
// turned back into a real line break afterwards for pre-wrap to render.
function fmt(v) {
  if (v === null || v === undefined) return "(none)";
  if (typeof v === "string") return escapeHtml(v);
  const text = JSON.stringify(v, null, 2);
  return escapeHtml(text).replace(/\\\\n/g, "\\n").replace(/\\\\t/g, "\\t");
}

function render() {
  const sidebar = document.getElementById("sidebar");
  sidebar.innerHTML = "";
  for (const s of steps) {
    const div = document.createElement("div");
    div.className = "item" + (s.index === selected ? " selected" : "") + (s.error ? " error" : "");
    const crumb = s.breadcrumb.length ? s.breadcrumb.join(" \\u203a ") : s.stage || "";
    div.innerHTML = "<div>#" + s.index + " " + moduleBadge(s.module) + escapeHtml(s.model || "lm") + "</div>" +
                     "<div class=\\"crumb\\">" + escapeHtml(crumb) + "</div>";
    div.onclick = () => { selected = s.index; renderMain(); render(); };
    sidebar.appendChild(div);
  }
}

function renderMain() {
  const main = document.getElementById("main");
  if (selected === null || !steps.length) {
    main.innerHTML = "<div id=\\"empty\\">Waiting for LLM calls...</div>";
    return;
  }
  const s = steps[selected];
  const usage = s.usage ? Object.entries(s.usage).map(([k, v]) => k + "=" + v).join(", ") : "n/a";
  main.innerHTML =
    "<div id=\\"nav\\">" +
    "<button id=\\"prev\\">&larr; prev</button>" +
    "<button id=\\"next\\">next &rarr;</button>" +
    "</div>" +
    "<h2>Step #" + s.index + " &mdash; " + moduleBadge(s.module) + escapeHtml(s.model || "lm") + "</h2>" +
    "<div class=\\"meta\\">" +
      escapeHtml(s.breadcrumb.join(" \\u203a ") || s.stage || "") +
      " | " + (s.duration_ms !== null && s.duration_ms !== undefined ? s.duration_ms.toFixed(1) + " ms" : "n/a") +
      " | tokens: " + usage +
    "</div>" +
    (s.tools && s.tools.length
      ? "<div class=\\"meta tools\\">tools: " + s.tools.map(escapeHtml).join(", ") + "</div>"
      : "") +
    (s.error ? "<div class=\\"error-banner\\">" + escapeHtml(s.error) + "</div>" : "") +
    "<h3>Prompt</h3><pre>" + fmt(s.inputs) + "</pre>" +
    "<h3>Response</h3><pre>" + fmt(s.outputs) + "</pre>";
  document.getElementById("prev").onclick = () => { if (selected > 0) { selected--; renderMain(); render(); } };
  document.getElementById("next").onclick = () => { if (selected < steps.length - 1) { selected++; renderMain(); render(); } };
}

async function poll() {
  try {
    const res = await fetch("/api/steps");
    const data = await res.json();
    const wasAtEnd = selected === null || selected === steps.length - 1;
    steps = data.steps;
    if (selected === null && steps.length) selected = 0;
    else if (wasAtEnd && steps.length) selected = steps.length - 1;
    else if (selected !== null) selected = Math.min(selected, steps.length - 1);
    render();
    renderMain();
  } catch (e) {
    // Trace file may not exist yet, or the server may be mid-restart; retry.
  }
}

document.addEventListener("keydown", (e) => {
  if (e.key === "ArrowRight" && selected !== null && selected < steps.length - 1) { selected++; render(); renderMain(); }
  if (e.key === "ArrowLeft" && selected !== null && selected > 0) { selected--; render(); renderMain(); }
});

poll();
setInterval(poll, 2000);
</script>
</body>
</html>
"""


class _TraceServer(ThreadingHTTPServer):
    def __init__(self, address: tuple[str, int], handler: type, trace_path: Path):
        super().__init__(address, handler)
        self.trace_path = trace_path


class _TraceRequestHandler(BaseHTTPRequestHandler):
    server: _TraceServer  # type: ignore[assignment]

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - keep the CLI quiet
        pass

    def do_GET(self) -> None:  # noqa: N802 - required name from BaseHTTPRequestHandler
        if self.path == "/":
            self._send(200, "text/html; charset=utf-8", _PAGE.encode("utf-8"))
            return
        if self.path.startswith("/api/steps"):
            records = parse_trace_file(self.server.trace_path)
            steps = build_steps(records)
            run_id = steps[-1]["run_id"] if steps else None
            body = json.dumps({"run_id": run_id, "steps": steps}, default=str).encode("utf-8")
            self._send(200, "application/json", body)
            return
        self._send(404, "text/plain; charset=utf-8", b"not found")

    def _send(self, status: int, content_type: str, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def serve(
    path: Path,
    host: str = "127.0.0.1",
    port: int = 8765,
    open_browser: bool = False,
) -> None:
    """Serve a step-by-step view of one trace file. Blocks until interrupted."""
    trace_path = Path(path)
    server = _TraceServer((host, port), _TraceRequestHandler, trace_path)
    url = f"http://{host}:{port}/"
    print(f"Serving trace {trace_path} at {url} (Ctrl+C to stop)")
    if open_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    try:
        server.serve_forever()
    finally:
        server.server_close()
