"""``agent.trace.webview``: turning a flat JSONL trace into step-by-step LLM calls."""

import http.client
import json
import shutil
import subprocess
import threading

import pytest

from agent.trace.webview import (
    _PAGE,
    _TraceRequestHandler,
    _TraceServer,
    build_steps,
    parse_trace_file,
)

needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="no node.js")

# One level -> one query -> one module -> one lm call, the shape query_codegen's
# span nesting produces once level/query spans are given a writer.
LEVEL_QUERY_LM_RECORDS = [
    {
        "event": "level_start", "kind": "level", "name": "hint_0",
        "run_id": "run1", "stage": "query_codegen",
        "span_id": "lvl1", "parent_span_id": None, "ts": 1.0, "inputs": {},
    },
    {
        "event": "query_start", "kind": "query", "name": "q1",
        "run_id": "run1", "stage": "query_codegen",
        "span_id": "qry1", "parent_span_id": "lvl1", "ts": 1.1, "inputs": {},
    },
    {
        "event": "module_start", "kind": "module", "name": "ChainOfThought",
        "run_id": "run1", "stage": "query_codegen", "call_id": "c1",
        "span_id": "mod1", "parent_span_id": "qry1", "ts": 1.2,
        "inputs": {"question": "generate q1"},
        "tools": ["read_function", "write_file"],
    },
    {
        "event": "lm_start", "kind": "lm", "name": "gpt-4",
        "run_id": "run1", "stage": "query_codegen", "call_id": "c2",
        "span_id": "lm1", "parent_span_id": "mod1", "ts": 1.3,
        "inputs": {"messages": ["hi"]},
    },
    {
        "event": "lm_end", "kind": "lm", "name": "gpt-4",
        "run_id": "run1", "stage": "query_codegen", "call_id": "c2",
        "span_id": "lm1", "parent_span_id": "mod1", "ts": 1.4,
        "outputs": {"text": "answer"}, "duration_ms": 100.0,
        "usage": {"prompt_tokens": 10, "completion_tokens": 5}, "error": None,
    },
    {
        "event": "module_end", "kind": "module", "name": "ChainOfThought",
        "run_id": "run1", "stage": "query_codegen", "call_id": "c1",
        "span_id": "mod1", "parent_span_id": "qry1", "ts": 1.5,
        "outputs": {}, "duration_ms": 300.0, "error": None,
    },
    {
        "event": "query_end", "kind": "query", "name": "q1",
        "run_id": "run1", "stage": "query_codegen",
        "span_id": "qry1", "parent_span_id": "lvl1", "ts": 1.6, "duration_ms": 500.0,
    },
    {
        "event": "level_end", "kind": "level", "name": "hint_0",
        "run_id": "run1", "stage": "query_codegen",
        "span_id": "lvl1", "parent_span_id": None, "ts": 1.7, "duration_ms": 700.0,
    },
]


def _write_jsonl(path, records):
    with open(path, "w", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record) + "\n")


class TestParseTraceFile:
    def test_reads_every_valid_line(self, tmp_path):
        path = tmp_path / "trace.jsonl"
        _write_jsonl(path, LEVEL_QUERY_LM_RECORDS)
        assert len(parse_trace_file(path)) == len(LEVEL_QUERY_LM_RECORDS)

    def test_skips_malformed_lines(self, tmp_path):
        path = tmp_path / "trace.jsonl"
        path.write_text(
            '{"event": "lm_start", "span_id": "a"}\n'
            "not json at all\n"
            '{"event": "lm_end", "span_id": "a"}\n',
            encoding="utf-8",
        )
        assert len(parse_trace_file(path)) == 2

    def test_missing_file_returns_no_records(self, tmp_path):
        assert parse_trace_file(tmp_path / "missing.jsonl") == []


class TestBuildSteps:
    def test_one_lm_call_becomes_one_step(self):
        steps = build_steps(LEVEL_QUERY_LM_RECORDS)
        assert len(steps) == 1
        step = steps[0]
        assert step["stage"] == "query_codegen"
        assert step["model"] == "gpt-4"
        assert step["module"] == "ChainOfThought"
        assert step["tools"] == ["read_function", "write_file"]
        assert step["breadcrumb"] == ["level=hint_0", "query=q1", "module=ChainOfThought"]
        assert step["duration_ms"] == 100.0
        assert step["usage"] == {"prompt_tokens": 10, "completion_tokens": 5}
        assert step["inputs"] == {"messages": ["hi"]}
        assert step["outputs"] == {"text": "answer"}
        assert step["error"] is None
        assert step["index"] == 0

    def test_only_lm_spans_become_steps(self):
        steps = build_steps(LEVEL_QUERY_LM_RECORDS)
        span_ids = {s["span_id"] for s in steps}
        assert span_ids == {"lm1"}

    def test_module_is_none_without_a_module_ancestor(self):
        records = [
            {
                "event": "lm_start", "kind": "lm", "name": "gpt-4",
                "run_id": "run1", "stage": "s", "span_id": "lm1", "parent_span_id": None, "ts": 1.0,
            },
            {
                "event": "lm_end", "kind": "lm", "name": "gpt-4",
                "run_id": "run1", "stage": "s", "span_id": "lm1", "parent_span_id": None, "ts": 1.1,
            },
        ]
        steps = build_steps(records)
        assert steps[0]["module"] is None
        assert steps[0]["tools"] == []

    def test_module_resolves_to_the_nearest_ancestor(self):
        """An RLM call nests a ``CppRLM`` module span around an inner ``RLM`` one.

        The inner, nearer module - the one that actually produced the LM
        sub-call - should win over the outer wrapper, and its tools travel
        with it.
        """
        records = [
            {
                "event": "module_start", "kind": "module", "name": "CppRLM",
                "run_id": "run1", "stage": "s", "span_id": "outer", "parent_span_id": None, "ts": 1.0,
                "tools": ["compile_file", "build_project"],
            },
            {
                "event": "module_start", "kind": "module", "name": "RLM",
                "run_id": "run1", "stage": "s", "span_id": "inner", "parent_span_id": "outer", "ts": 1.1,
                "tools": ["compile_file", "build_project"],
            },
            {
                "event": "lm_start", "kind": "lm", "name": "gpt-4",
                "run_id": "run1", "stage": "s", "span_id": "lm1", "parent_span_id": "inner", "ts": 1.2,
            },
            {
                "event": "lm_end", "kind": "lm", "name": "gpt-4",
                "run_id": "run1", "stage": "s", "span_id": "lm1", "parent_span_id": "inner", "ts": 1.3,
            },
        ]
        steps = build_steps(records)
        assert steps[0]["module"] == "RLM"
        assert steps[0]["tools"] == ["compile_file", "build_project"]

    def test_steps_are_ordered_and_indexed_by_timestamp(self):
        earlier = {
            "event": "lm_start", "kind": "lm", "name": "gpt-early",
            "run_id": "run1", "stage": "s", "span_id": "lmA", "parent_span_id": None, "ts": 0.1,
        }
        earlier_end = {
            "event": "lm_end", "kind": "lm", "name": "gpt-early",
            "run_id": "run1", "stage": "s", "span_id": "lmA", "parent_span_id": None, "ts": 0.2,
            "duration_ms": 10.0,
        }
        later = {
            "event": "lm_start", "kind": "lm", "name": "gpt-later",
            "run_id": "run1", "stage": "s", "span_id": "lmB", "parent_span_id": None, "ts": 5.0,
        }
        later_end = {
            "event": "lm_end", "kind": "lm", "name": "gpt-later",
            "run_id": "run1", "stage": "s", "span_id": "lmB", "parent_span_id": None, "ts": 5.1,
            "duration_ms": 10.0,
        }
        # Fed out of chronological order, on purpose.
        steps = build_steps([later, later_end, earlier, earlier_end])
        assert [s["model"] for s in steps] == ["gpt-early", "gpt-later"]
        assert [s["index"] for s in steps] == [0, 1]

    def test_unmatched_end_is_tolerated(self):
        """JsonlTraceCallback._end writes a record even when its start was lost."""
        lone_end = {
            "event": "lm_end", "kind": "lm", "name": "gpt-4",
            "run_id": "run1", "stage": "s", "span_id": "lmX", "parent_span_id": None,
            "ts": 1.0, "outputs": {"text": "ok"}, "duration_ms": 50.0,
        }
        steps = build_steps([lone_end])
        assert len(steps) == 1
        assert steps[0]["inputs"] is None


class TestServer:
    def _running_server(self, tmp_path, records=LEVEL_QUERY_LM_RECORDS):
        path = tmp_path / "trace.jsonl"
        _write_jsonl(path, records)
        server = _TraceServer(("127.0.0.1", 0), _TraceRequestHandler, path)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        return server, thread

    def _stop(self, server, thread):
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    def test_api_steps_returns_parsed_steps(self, tmp_path):
        server, thread = self._running_server(tmp_path)
        try:
            conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
            conn.request("GET", "/api/steps")
            resp = conn.getresponse()
            payload = json.loads(resp.read())
            assert resp.status == 200
            assert payload["run_id"] == "run1"
            assert len(payload["steps"]) == 1
            assert payload["steps"][0]["model"] == "gpt-4"
        finally:
            self._stop(server, thread)

    def test_index_page_is_html(self, tmp_path):
        server, thread = self._running_server(tmp_path)
        try:
            conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
            conn.request("GET", "/")
            resp = conn.getresponse()
            body = resp.read()
            assert resp.status == 200
            assert "text/html" in resp.getheader("Content-Type")
            assert b"<html>" in body
        finally:
            self._stop(server, thread)

    def test_unknown_path_is_404(self, tmp_path):
        server, thread = self._running_server(tmp_path)
        try:
            conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
            conn.request("GET", "/nope")
            resp = conn.getresponse()
            resp.read()
            assert resp.status == 404
        finally:
            self._stop(server, thread)

    def test_reflects_a_growing_trace_file_without_restart(self, tmp_path):
        """The same request re-parses the file, so a live run is visible too."""
        path = tmp_path / "trace.jsonl"
        _write_jsonl(path, LEVEL_QUERY_LM_RECORDS[:4])  # up to lm_start, no lm_end yet
        server = _TraceServer(("127.0.0.1", 0), _TraceRequestHandler, path)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
            conn.request("GET", "/api/steps")
            first = json.loads(conn.getresponse().read())
            assert first["steps"][0]["outputs"] is None

            with open(path, "a", encoding="utf-8") as f:
                for record in LEVEL_QUERY_LM_RECORDS[4:]:
                    f.write(json.dumps(record) + "\n")

            conn.request("GET", "/api/steps")
            second = json.loads(conn.getresponse().read())
            assert second["steps"][0]["outputs"] == {"text": "answer"}
        finally:
            self._stop(server, thread)


class TestPageRendering:
    """Guards the page's ``fmt``/``escapeHtml`` JS against regressing.

    C++ source is full of "<...>" (templates, includes), so text inserted into
    the page via innerHTML without escaping gets parsed as HTML tags and can
    visually disappear - this is what made some responses look empty. These
    tests run the actual JS the page serves (via node), not a Python
    reimplementation of it, so they catch a regression in the real logic.
    """

    def _run_js(self, js: str) -> str:
        script = _PAGE[_PAGE.index("function escapeHtml") : _PAGE.index("function render()")]
        result = subprocess.run(
            ["node", "-e", script + js], capture_output=True, text=True, timeout=10
        )
        assert result.returncode == 0, result.stderr
        return result.stdout

    @needs_node
    def test_angle_brackets_are_escaped(self):
        out = self._run_js('console.log(fmt("std::vector<int> v;"));')
        assert out.strip() == "std::vector&lt;int&gt; v;"

    @needs_node
    def test_embedded_newlines_in_json_become_real_line_breaks(self):
        out = self._run_js(
            'console.log(JSON.stringify(fmt({"content": "line one\\nline two"})));'
        )
        # JSON-encoding the *test's* own stdout is just how the real newline
        # survives the round trip back out of node; a literal backslash-n
        # would appear here as the two characters \\n instead of \n.
        assert "line one\\nline two" in out
        assert "line one\\\\nline two" not in out

    @needs_node
    def test_plain_strings_are_escaped_too(self):
        out = self._run_js('console.log(fmt("a < b && b > c"));')
        assert out.strip() == "a &lt; b &amp;&amp; b &gt; c"

    @needs_node
    def test_module_badge_escapes_its_name(self):
        out = self._run_js('console.log(moduleBadge("<img src=x onerror=alert(1)>"));')
        assert "<img" not in out
        assert "&lt;img" in out

    @needs_node
    def test_module_badge_is_empty_for_no_module(self):
        out = self._run_js('console.log(JSON.stringify(moduleBadge(null)));')
        assert out.strip() == '""'

    @needs_node
    def test_tools_list_is_escaped_per_entry(self):
        out = self._run_js(
            'console.log(["a", "<script>"].map(escapeHtml).join(", "));'
        )
        assert out.strip() == "a, &lt;script&gt;"

    def test_page_uses_escapeHtml_for_every_raw_field(self):
        """A cheaper, node-free guard: every raw trace field goes through fmt/escapeHtml."""
        main_fn = _PAGE[_PAGE.index("function renderMain") : _PAGE.index("function poll")]
        assert "escapeHtml(s.model" in main_fn
        assert "escapeHtml(s.breadcrumb" in main_fn
        assert "escapeHtml(s.error)" in main_fn
        assert "fmt(s.inputs)" in main_fn
        assert "fmt(s.outputs)" in main_fn
        assert "moduleBadge(s.module)" in main_fn
        assert "s.tools.map(escapeHtml)" in main_fn

    def test_sidebar_also_uses_module_badge(self):
        render_fn = _PAGE[_PAGE.index("function render()") : _PAGE.index("function renderMain")]
        assert "moduleBadge(s.module)" in render_fn
