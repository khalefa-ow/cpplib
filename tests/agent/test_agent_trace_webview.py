"""``agent.trace.webview``: turning a flat JSONL trace into step-by-step LLM calls."""

import http.client
import json
import threading

from agent.trace.webview import (
    _TraceRequestHandler,
    _TraceServer,
    build_steps,
    parse_trace_file,
)

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
