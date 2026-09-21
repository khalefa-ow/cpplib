"""Disk cache behaviour and JSONL tracing."""

import json

from agent.config.models import ModelConfig, TraceConfig
from agent.llm.cache import CacheKey, DiskCache, canonical_hash
from agent.llm.lm_factory import (
    describe_model,
    provider_of,
    resolve_api_base,
    resolve_api_key_env,
    validate_model_config,
)
from agent.trace.callbacks import REDACT_PATTERN, JsonlTraceCallback, TraceWriter
from agent.trace.integrations import maybe_init_wandb, maybe_init_weave
from agent.trace.span import current_span_id, new_span, run_context


class TestCanonicalHash:
    def test_key_order_does_not_matter(self):
        assert canonical_hash({"a": 1, "b": 2}) == canonical_hash({"b": 2, "a": 1})

    def test_different_values_hash_differently(self):
        assert canonical_hash({"a": 1}) != canonical_hash({"a": 2})

    def test_non_serializable_values_do_not_raise(self):
        assert canonical_hash({"fn": object()})


class TestCacheKey:
    def test_every_component_affects_the_digest(self):
        base = CacheKey(
            stage="s",
            prompt_fingerprint="fp",
            payload_hash="ph",
            model_signature="ms",
        )
        for field, value in [
            ("stage", "other"),
            ("kind", "tool"),
            ("prompt_fingerprint", "fp2"),
            ("payload_hash", "ph2"),
            ("model_signature", "ms2"),
            ("schema_version", 99),
        ]:
            assert base.model_copy(update={field: value}).digest() != base.digest(), field

    def test_digest_is_stable(self):
        key = CacheKey(stage="s", payload_hash="p")
        assert key.digest() == key.digest()


class TestDiskCache:
    def test_miss_then_hit(self, tmp_path):
        cache = DiskCache(tmp_path / "c", stage="s")
        key = cache.make_key({"x": 1}, prompt_fingerprint="fp", model_signature="ms")

        calls = []

        def compute():
            calls.append(1)
            return {"answer": "first"}

        value, hit = cache.memoize(key, compute)
        assert value == {"answer": "first"} and hit is False

        value, hit = cache.memoize(key, lambda: {"answer": "RECOMPUTED"})
        assert value == {"answer": "first"} and hit is True
        assert len(calls) == 1
        assert cache.stats.hits == 1 and cache.stats.misses == 1

    def test_prompt_change_invalidates(self, tmp_path):
        cache = DiskCache(tmp_path / "c", stage="s")
        first = cache.make_key({"x": 1}, prompt_fingerprint="fp1", model_signature="ms")
        second = cache.make_key({"x": 1}, prompt_fingerprint="fp2", model_signature="ms")
        cache.memoize(first, lambda: "a")
        value, hit = cache.memoize(second, lambda: "b")
        assert value == "b" and hit is False

    def test_model_change_invalidates(self, tmp_path):
        cache = DiskCache(tmp_path / "c", stage="s")
        first = cache.make_key({"x": 1}, model_signature="gpt|t=0")
        second = cache.make_key({"x": 1}, model_signature="gpt|t=1")
        cache.memoize(first, lambda: "a")
        assert cache.memoize(second, lambda: "b") == ("b", False)

    def test_refresh_recomputes_but_still_writes(self, tmp_path):
        warm = DiskCache(tmp_path / "c", stage="s")
        key = warm.make_key({"x": 1})
        warm.memoize(key, lambda: "old")

        refreshing = DiskCache(tmp_path / "c", stage="s", refresh=True)
        value, hit = refreshing.memoize(refreshing.make_key({"x": 1}), lambda: "new")
        assert value == "new" and hit is False

        # The refreshed value is what a later non-refreshing run reads.
        after = DiskCache(tmp_path / "c", stage="s")
        assert after.memoize(after.make_key({"x": 1}), lambda: "unused") == ("new", True)

    def test_disabled_cache_always_recomputes(self, tmp_path):
        cache = DiskCache(tmp_path / "c", enabled=False, stage="s")
        key = cache.make_key({"x": 1})
        assert cache.memoize(key, lambda: "a") == ("a", False)
        assert cache.memoize(key, lambda: "b") == ("b", False)
        assert cache.count() == 0

    def test_corrupt_entry_is_a_miss_not_a_crash(self, tmp_path):
        cache = DiskCache(tmp_path / "c", stage="s")
        key = cache.make_key({"x": 1})
        cache.memoize(key, lambda: "good")
        cache.path_for(key.digest()).write_text("{not json", encoding="utf-8")
        value, hit = cache.memoize(key, lambda: "recovered")
        assert value == "recovered" and hit is False
        assert cache.stats.errors == 1

    def test_writes_are_atomic(self, tmp_path):
        """No .tmp leftovers, so a crashed run cannot leave a half entry."""
        cache = DiskCache(tmp_path / "c", stage="s")
        cache.memoize(cache.make_key({"x": 1}), lambda: {"a": 1})
        assert list((tmp_path / "c").rglob("*.tmp")) == []
        assert cache.count() == 1

    def test_entry_is_readable_json(self, tmp_path):
        cache = DiskCache(tmp_path / "c", stage="mystage")
        key = cache.make_key({"x": 1}, prompt_fingerprint="fp")
        cache.memoize(key, lambda: {"plan": "columnar"}, meta={"model": "m"})
        payload = json.loads(cache.path_for(key.digest()).read_text(encoding="utf-8"))
        assert payload["value"] == {"plan": "columnar"}
        assert payload["key"]["stage"] == "mystage"
        assert payload["meta"]["model"] == "m"

    def test_clear_removes_entries(self, tmp_path):
        cache = DiskCache(tmp_path / "c", stage="s")
        cache.memoize(cache.make_key({"x": 1}), lambda: 1)
        cache.memoize(cache.make_key({"x": 2}), lambda: 2)
        assert cache.clear() == 2
        assert cache.count() == 0


class TestLmFactory:
    def test_provider_detection(self):
        assert provider_of("openai/gpt-4o") == "openai"
        assert provider_of("deepseek/deepseek-chat") == "deepseek"
        assert provider_of("bare-model") == ""

    def test_provider_defaults_fill_unset_fields(self):
        deepseek = ModelConfig(name="deepseek/deepseek-chat")
        assert resolve_api_key_env(deepseek) == "DEEPSEEK_API_KEY"
        assert resolve_api_base(deepseek) == "https://api.deepseek.com"

        openai = ModelConfig(name="openai/gpt-4o")
        assert resolve_api_key_env(openai) == "OPENAI_API_KEY"
        assert resolve_api_base(openai) is None

    def test_explicit_config_overrides_defaults(self):
        cfg = ModelConfig(
            name="deepseek/deepseek-chat",
            api_key_env="MY_KEY",
            api_base="https://proxy.invalid",
        )
        assert resolve_api_key_env(cfg) == "MY_KEY"
        assert resolve_api_base(cfg) == "https://proxy.invalid"

    def test_empty_api_key_env_means_no_key_needed(self):
        cfg = ModelConfig(name="ollama_chat/llama3", api_key_env="")
        assert resolve_api_key_env(cfg) is None
        validate_model_config(cfg, require_key=True)

    def test_missing_key_is_reported_with_the_env_var_name(self, monkeypatch):
        from agent.errors import MissingApiKeyError

        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        try:
            validate_model_config(ModelConfig(name="openai/gpt-4o"), require_key=True)
        except MissingApiKeyError as exc:
            assert "OPENAI_API_KEY" in str(exc)
        else:
            raise AssertionError("expected MissingApiKeyError")

    def test_deepseek_behind_openai_prefix_without_api_base_is_rejected(self):
        """Otherwise LiteLLM cheerfully sends the request to api.openai.com."""
        from agent.errors import LMError

        cfg = ModelConfig(name="openai/deepseek-chat")
        try:
            validate_model_config(cfg, require_key=False)
        except LMError as exc:
            assert "api_base" in str(exc)
        else:
            raise AssertionError("expected LMError")

    def test_describe_model_reports_key_state(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        text = describe_model(ModelConfig(name="openai/gpt-4o"))
        assert "OPENAI_API_KEY=set" in text
        monkeypatch.delenv("OPENAI_API_KEY")
        assert "MISSING" in describe_model(ModelConfig(name="openai/gpt-4o"))


class TestRedaction:
    def test_credential_keys_are_redacted(self):
        for name in (
            "api_key",
            "apiKey",
            "x_api_key",
            "authorization",
            "secret",
            "client_secret",
            "password",
            "token",
            "access_token",
            "bearer",
        ):
            assert REDACT_PATTERN.search(name), name

    def test_token_counters_are_preserved(self):
        """`token` must not match `prompt_tokens`, or usage data is destroyed."""
        for name in (
            "prompt_tokens",
            "completion_tokens",
            "total_tokens",
            "max_tokens",
            "api_key_env",
            "tokenizer",
        ):
            assert not REDACT_PATTERN.search(name), name


class TestTraceWriter:
    def test_writes_jsonl(self, tmp_path):
        path = tmp_path / "t.jsonl"
        with TraceWriter(path=path) as writer:
            writer.write({"event": "a"})
            writer.write({"event": "b"})
        lines = path.read_text(encoding="utf-8").strip().splitlines()
        assert [json.loads(line)["event"] for line in lines] == ["a", "b"]

    def test_truncates_long_strings(self):
        writer = TraceWriter(max_field_chars=10)
        record = writer.write({"event": "e", "inputs": "x" * 100})
        assert record["inputs"].startswith("x" * 10)
        assert "+90 chars" in record["inputs"]

    def test_redacts_nested_credentials(self):
        writer = TraceWriter()
        record = writer.write({"event": "e", "inputs": {"nested": {"api_key": "sk-secret"}}})
        assert record["inputs"]["nested"]["api_key"] == "<redacted>"

    def test_never_raises_on_unserializable_values(self):
        writer = TraceWriter()
        assert writer.write({"event": "e", "outputs": object()})

    def test_usage_totals_sum_across_calls(self):
        writer = TraceWriter()
        writer.write({"event": "lm_end", "usage": {"prompt_tokens": 10, "completion_tokens": 5}})
        writer.write({"event": "lm_end", "usage": {"prompt_tokens": 7, "completion_tokens": 3}})
        assert writer.usage_totals() == {"prompt_tokens": 17, "completion_tokens": 8}


class TestSpans:
    def test_spans_nest(self):
        with run_context(stage="s"):
            with new_span("outer", "o") as outer:
                assert current_span_id() == outer.span_id
                with new_span("inner", "i") as inner:
                    assert inner.parent_span_id == outer.span_id
                assert current_span_id() == outer.span_id

    def test_run_id_is_ambient(self):
        with run_context(run_id="fixed") as rid:
            assert rid == "fixed"
            with new_span("k", "n") as span:
                assert span.run_id == "fixed"
                assert span.stage == "s" or span.stage is None


class TestCallbackNesting:
    def test_rlm_events_nest_under_the_module(self):
        """The reason spans exist: DSPy's call_id says nothing about parentage."""
        writer = TraceWriter()
        callback = JsonlTraceCallback(writer)
        with run_context(stage="storage_plan"):
            callback.on_module_start("m", type("CppRLM", (), {})(), {})
            callback.on_interpreter_execute_start("i", object(), {"code": "x"})
            callback.on_interpreter_tool_call_start("t", object(), {"name": "read_function"})
            callback.on_interpreter_tool_call_end("t", "int f();")
            callback.on_interpreter_execute_end("i", "done")
            callback.on_lm_start("l", type("LM", (), {"model": "m1"})(), {})
            callback.on_lm_end("l", {"usage": {"prompt_tokens": 3}})
            callback.on_module_end("m", {"storage_plan": "p"})

        by_event = {record["event"]: record for record in writer.records}
        module = by_event["module_start"]
        execute = by_event["interpreter_execute_start"]
        tool = by_event["interpreter_tool_call_start"]
        lm = by_event["lm_start"]

        assert module["parent_span_id"] is None
        assert execute["parent_span_id"] == module["span_id"]
        assert tool["parent_span_id"] == execute["span_id"]
        assert lm["parent_span_id"] == module["span_id"]
        assert by_event["lm_end"]["usage"] == {"prompt_tokens": 3}

    def test_durations_are_recorded(self):
        writer = TraceWriter()
        callback = JsonlTraceCallback(writer)
        with run_context():
            callback.on_module_start("m", object(), {})
            callback.on_module_end("m", None)
        assert writer.records[-1]["duration_ms"] >= 0

    def test_exceptions_are_recorded(self):
        writer = TraceWriter()
        callback = JsonlTraceCallback(writer)
        with run_context():
            callback.on_lm_start("l", type("LM", (), {"model": "m"})(), {})
            callback.on_lm_end("l", None, exception=ValueError("boom"))
        assert "ValueError: boom" in writer.records[-1]["error"]

    def test_unmatched_end_is_tolerated(self):
        writer = TraceWriter()
        callback = JsonlTraceCallback(writer)
        with run_context():
            callback.on_module_end("never_started", None)
        assert writer.records[-1]["event"] == "module_end"


class TestModuleToolNames:
    """A module_start record names the tools that module was built with."""

    def test_a_public_tools_list_is_recorded(self):
        """CppRLM's own shape: a plain list of functions with __name__."""

        def read_function():
            pass

        def write_file():
            pass

        instance = type("CppRLM", (), {"tools": [read_function, write_file]})()
        writer = TraceWriter()
        callback = JsonlTraceCallback(writer)
        with run_context():
            callback.on_module_start("m", instance, {})
        assert writer.records[-1]["tools"] == ["read_function", "write_file"]

    def test_a_private_user_tools_dict_is_recorded(self):
        """dspy.RLM's own shape: a dict[str, Tool] on ``_user_tools``."""
        instance = type(
            "RLM", (), {"_user_tools": {"write_file": object(), "read_function": object()}}
        )()
        writer = TraceWriter()
        callback = JsonlTraceCallback(writer)
        with run_context():
            callback.on_module_start("m", instance, {})
        assert writer.records[-1]["tools"] == ["read_function", "write_file"]

    def test_a_module_without_tools_omits_the_field(self):
        """Plain Predict/ChainOfThought instances carry no tools at all."""
        writer = TraceWriter()
        callback = JsonlTraceCallback(writer)
        with run_context():
            callback.on_module_start("m", object(), {})
        assert "tools" not in writer.records[-1]


class TestIntegrations:
    def test_disabled_by_default(self):
        assert maybe_init_weave(TraceConfig()) is None
        assert maybe_init_wandb(TraceConfig()) is None

    def test_missing_package_degrades_to_none(self, caplog):
        """A missing observability backend must not fail a generation run."""
        assert maybe_init_weave(TraceConfig(enable_weave=True)) is None
        assert maybe_init_wandb(TraceConfig(enable_wandb=True)) is None
