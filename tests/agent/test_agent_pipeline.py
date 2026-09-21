"""Artifact store, stage ordering, resumption, and storage_plan end to end.

The storage_plan tests use DSPy's ``DummyLM``, so the whole path — config,
prompt rendering, predictor selection, caching, artifact writing and tracing —
is exercised for real; only the provider round-trip is stubbed.
"""

import json
from typing import Mapping

import pytest

from agent.errors import PipelineError, UnknownStageError
from agent.pipeline import STAGES, Pipeline, order_stages
from agent.stages.base import Artifact, ArtifactStore, Stage, StageResult


class TestArtifactStore:
    def test_put_and_read_text(self, tmp_path):
        store = ArtifactStore(tmp_path / "artifacts")
        artifact = store.put_text("s", "k", "hello", meta={"a": 1})
        assert artifact.read_text() == "hello"
        assert artifact.meta["a"] == 1
        assert artifact.content_hash

    def test_put_and_read_json(self, tmp_path):
        store = ArtifactStore(tmp_path / "artifacts")
        artifact = store.put_json("s", "k", {"levels": {"no_hints": "text"}})
        assert artifact.read_json()["levels"]["no_hints"] == "text"

    def test_explicit_target_writes_outside_the_store(self, tmp_path):
        """Used when a config names an output path inside the C++ project."""
        store = ArtifactStore(tmp_path / "artifacts")
        target = tmp_path / "gen" / "storage_layout.hpp"
        artifact = store.put_text("hppgen", "hdr", "#pragma once\n", target=target)
        assert artifact.path == target
        assert target.read_text(encoding="utf-8") == "#pragma once\n"

    def test_index_survives_a_reload(self, tmp_path):
        first = ArtifactStore(tmp_path / "artifacts")
        first.put_text("s", "k", "value")
        second = ArtifactStore(tmp_path / "artifacts")
        assert second.get("k") is not None
        assert second.get("k").read_text() == "value"

    def test_deleted_file_reports_as_absent(self, tmp_path):
        store = ArtifactStore(tmp_path / "artifacts")
        artifact = store.put_text("s", "k", "v")
        artifact.path.unlink()
        assert store.get("k") is None
        assert store.has("k") is False

    def test_corrupt_index_does_not_block(self, tmp_path):
        store = ArtifactStore(tmp_path / "artifacts")
        store.put_text("s", "k", "v")
        store.index_path.write_text("{not json", encoding="utf-8")
        reloaded = ArtifactStore(tmp_path / "artifacts")
        assert reloaded.get("k") is None  # forgotten, but no crash
        assert reloaded.put_text("s", "k", "again").read_text() == "again"

    def test_collect_names_the_missing_artifacts(self, tmp_path):
        store = ArtifactStore(tmp_path / "artifacts")
        with pytest.raises(PipelineError, match="query_sources"):
            store.collect(("query_sources", "correctness_report"))

    def test_index_write_is_atomic(self, tmp_path):
        store = ArtifactStore(tmp_path / "artifacts")
        store.put_text("s", "k", "v")
        assert list((tmp_path / "artifacts").glob("*.tmp")) == []


class TestOrdering:
    def test_full_workflow_order(self):
        assert order_stages(list(STAGES)) == [
            "storage_plan",
            "divide",
            "hppgen",
            "query_codegen",
            "optimize",
        ]

    def test_order_is_independent_of_input_order(self):
        scrambled = ["optimize", "hppgen", "storage_plan", "query_codegen", "divide"]
        assert order_stages(scrambled) == order_stages(list(STAGES))

    def test_a_single_stage_can_run_alone(self):
        """Its upstream requirements come from artifacts already on disk."""
        assert order_stages(["optimize"]) == ["optimize"]

    def test_partial_selection_keeps_relative_order(self):
        assert order_stages(["query_codegen", "divide"]) == ["divide", "query_codegen"]

    def test_duplicates_collapse(self):
        assert order_stages(["divide", "divide"]) == ["divide"]

    def test_unknown_stage_lists_the_known_ones(self):
        with pytest.raises(UnknownStageError, match="storage_plan"):
            order_stages(["nonexistent"])

    def test_cycles_are_detected(self):
        class A(Stage):
            name, requires, produces = "a", ("y",), ("x",)

            def run(self, inputs):
                raise NotImplementedError

        class B(Stage):
            name, requires, produces = "b", ("x",), ("y",)

            def run(self, inputs):
                raise NotImplementedError

        with pytest.raises(PipelineError, match="Cyclic"):
            order_stages(["a", "b"], {"a": A, "b": B})


# --- fake stages for pipeline behaviour -----------------------------------


class _Producer(Stage):
    name = "producer"
    requires = ()
    produces = ("thing",)
    runs: list[str] = []

    def run(self, inputs: Mapping[str, Artifact]) -> StageResult:
        type(self).runs.append(self.name)
        artifact = self.store.put_text(
            self.name,
            "thing",
            "produced",
            meta={"input_fingerprint": self.input_fingerprint(inputs)},
        )
        return self.make_result(artifacts={"thing": artifact}, metrics={"n": 1})


class _Consumer(Stage):
    name = "consumer"
    requires = ("thing",)
    produces = ("result",)
    runs: list[str] = []

    def run(self, inputs: Mapping[str, Artifact]) -> StageResult:
        type(self).runs.append(self.name)
        assert inputs["thing"].read_text() == "produced"
        artifact = self.store.put_text(
            self.name,
            "result",
            "consumed",
            meta={"input_fingerprint": self.input_fingerprint(inputs)},
        )
        return self.make_result(artifacts={"result": artifact})


class _Exploder(Stage):
    """Fails while producing the artifact _Consumer requires.

    Producing "thing" is deliberate: it makes _Consumer genuinely dependent on
    this stage, which is what the halt-on-failure test needs to exercise.
    """

    name = "exploder"
    requires = ()
    produces = ("thing",)

    def run(self, inputs: Mapping[str, Artifact]) -> StageResult:
        raise RuntimeError("stage blew up")


@pytest.fixture
def fake_stages():
    _Producer.runs = []
    _Consumer.runs = []
    return {"producer": _Producer, "consumer": _Consumer, "exploder": _Exploder}


@pytest.fixture
def fake_config(config_dict):
    from agent.config.loader import LoadedConfig

    config_dict["stages"] = {
        "producer": {"enabled": True},
        "consumer": {"enabled": True},
    }
    return LoadedConfig(config_dict)


class TestPipelineExecution:
    def test_runs_stages_in_dependency_order(self, fake_config, fake_stages, manifest_path):
        pipeline = Pipeline(fake_config, manifest_path=manifest_path, stage_classes=fake_stages)
        summary = pipeline.run()
        assert summary.ok, summary.report()
        assert [r.stage for r in summary.results] == ["producer", "consumer"]
        assert all(r.status == "ok" for r in summary.results)

    def test_second_run_skips_current_stages(self, fake_config, fake_stages, manifest_path):
        pipeline = Pipeline(fake_config, manifest_path=manifest_path, stage_classes=fake_stages)
        pipeline.run()
        assert _Producer.runs == ["producer"]

        summary = pipeline.run()
        assert [r.status for r in summary.results] == ["skipped", "skipped"]
        assert _Producer.runs == ["producer"]  # not re-run

    def test_force_reruns_everything(self, fake_config, fake_stages, manifest_path):
        pipeline = Pipeline(fake_config, manifest_path=manifest_path, stage_classes=fake_stages)
        pipeline.run()
        summary = pipeline.run(force=True)
        assert [r.status for r in summary.results] == ["ok", "ok"]
        assert _Producer.runs == ["producer", "producer"]

    def test_changing_an_input_file_invalidates_the_skip(
        self, fake_config, fake_stages, manifest_path
    ):
        """Existence alone must not count as current — a stale artifact is worse.

        This is the failure mode a resumable pipeline has to avoid: reusing an
        artifact produced before the schema changed.
        """
        pipeline = Pipeline(fake_config, manifest_path=manifest_path, stage_classes=fake_stages)
        pipeline.run()
        assert _Producer.runs == ["producer"]

        schema = fake_config.resolve_stage("producer").inputs["schema"]
        schema.write_text("CREATE TABLE changed (id INTEGER);\n", encoding="utf-8")

        summary = pipeline.run()
        assert [r.status for r in summary.results] == ["ok", "ok"]
        assert _Producer.runs == ["producer", "producer"]

    def test_stage_can_be_disabled_in_config(self, config_dict, fake_stages, manifest_path):
        from agent.config.loader import LoadedConfig

        config_dict["stages"] = {"producer": {"enabled": False}}
        pipeline = Pipeline(
            LoadedConfig(config_dict), manifest_path=manifest_path, stage_classes=fake_stages
        )
        summary = pipeline.run()
        assert [r.status for r in summary.results] == ["disabled"]

    def test_exception_becomes_a_failed_result_not_a_raise(
        self, config_dict, fake_stages, manifest_path
    ):
        from agent.config.loader import LoadedConfig

        config_dict["stages"] = {"exploder": {}}
        pipeline = Pipeline(
            LoadedConfig(config_dict), manifest_path=manifest_path, stage_classes=fake_stages
        )
        summary = pipeline.run()
        assert summary.ok is False
        assert "stage blew up" in summary.results[0].error

    def test_failure_halts_dependent_stages(self, config_dict, fake_stages, manifest_path):
        from agent.config.loader import LoadedConfig

        config_dict["stages"] = {"exploder": {}, "consumer": {}}
        pipeline = Pipeline(
            LoadedConfig(config_dict), manifest_path=manifest_path, stage_classes=fake_stages
        )
        summary = pipeline.run()
        assert [r.stage for r in summary.results] == ["exploder"]

    def test_keep_going_continues_past_a_failure(self, config_dict, fake_stages, manifest_path):
        from agent.config.loader import LoadedConfig

        # producer does not depend on exploder, so it can still run.
        config_dict["stages"] = {"exploder": {}, "producer": {}}
        pipeline = Pipeline(
            LoadedConfig(config_dict), manifest_path=manifest_path, stage_classes=fake_stages
        )
        summary = pipeline.run(stop_on_error=False)
        assert len(summary.results) == 2

    def test_missing_upstream_artifact_is_a_clear_error(
        self, config_dict, fake_stages, manifest_path
    ):
        from agent.config.loader import LoadedConfig

        config_dict["stages"] = {"consumer": {}}
        pipeline = Pipeline(
            LoadedConfig(config_dict), manifest_path=manifest_path, stage_classes=fake_stages
        )
        summary = pipeline.run()
        assert summary.ok is False
        assert "Missing required artifact(s): thing" in summary.results[0].error

    def test_only_and_start_from_filters(self, fake_config, fake_stages, manifest_path):
        pipeline = Pipeline(fake_config, manifest_path=manifest_path, stage_classes=fake_stages)
        assert pipeline.plan(only=["producer"]) == ["producer"]
        assert pipeline.plan(start_from="consumer") == ["consumer"]
        with pytest.raises(UnknownStageError):
            pipeline.plan(start_from="nope")

    def test_trace_file_is_written(self, fake_config, fake_stages, manifest_path):
        pipeline = Pipeline(fake_config, manifest_path=manifest_path, stage_classes=fake_stages)
        summary = pipeline.run()
        trace = summary.trace_path
        events = [json.loads(line)["event"] for line in trace.read_text().strip().splitlines()]
        assert "run_start" in events and "run_end" in events
        assert events.count("stage_start") == 2

    def test_each_run_gets_its_own_trace_file(self, fake_config, fake_stages, manifest_path):
        pipeline = Pipeline(fake_config, manifest_path=manifest_path, stage_classes=fake_stages)
        configured = fake_config.resolve_stage("producer").trace.path

        first = pipeline.run(force=True)
        second = pipeline.run(force=True)

        assert first.trace_path != second.trace_path
        assert first.run_id in first.trace_path.name
        assert second.run_id in second.trace_path.name
        assert not configured.exists()
        first_events = [json.loads(line) for line in first.trace_path.read_text().splitlines()]
        second_events = [json.loads(line) for line in second.trace_path.read_text().splitlines()]
        assert {e["run_id"] for e in first_events} == {first.run_id}
        assert {e["run_id"] for e in second_events} == {second.run_id}

    def test_summary_report_is_readable(self, fake_config, fake_stages, manifest_path):
        pipeline = Pipeline(fake_config, manifest_path=manifest_path, stage_classes=fake_stages)
        report = pipeline.run().report()
        assert "producer" in report and "consumer" in report and "OK" in report

    def test_describe_is_a_dry_run(self, fake_config, fake_stages, manifest_path):
        pipeline = Pipeline(fake_config, manifest_path=manifest_path, stage_classes=fake_stages)
        text = pipeline.describe()
        assert "producer" in text and "would run" in text
        assert _Producer.runs == []


class TestStageContracts:
    """Every stage declares the artifact contract the pipeline orders it by."""

    @pytest.mark.parametrize(
        "name,requires,produces",
        [
            ("storage_plan", (), ("storage_plan", "storage_plan_meta")),
            ("divide", ("storage_plan",), ("schema_levels",)),
            ("hppgen", ("schema_levels",), ("storage_layout_headers",)),
            (
                "query_codegen",
                ("schema_levels", "storage_layout_headers"),
                ("query_sources", "correctness_report"),
            ),
            ("optimize", ("query_sources", "correctness_report"), ("optimization_report",)),
        ],
    )
    def test_contract_is_declared(self, name, requires, produces):
        cls = STAGES[name]
        assert cls.requires == requires
        assert cls.produces == produces
        assert cls.__doc__ and "Requires:" in cls.__doc__ and "Produces:" in cls.__doc__

    def test_default_prompt_ids_exist_in_the_manifest(self, registry):
        for name, cls in STAGES.items():
            for prompt_id in cls.default_prompt_ids:
                assert registry.get(prompt_id), f"{name} references missing prompt {prompt_id}"

    def test_every_produced_artifact_has_exactly_one_producer(self):
        """Two stages producing one key would make the ordering ambiguous."""
        producers = {}
        for name, cls in STAGES.items():
            for key in cls.produces:
                assert (
                    key not in producers
                ), f"{key} produced by both {producers.get(key)} and {name}"
                producers[key] = name

    def test_every_requirement_is_produced_by_some_stage(self):
        produced = {key for cls in STAGES.values() for key in cls.produces}
        for name, cls in STAGES.items():
            for key in cls.requires:
                assert key in produced, f"{name} requires {key}, which nothing produces"


# --- storage_plan, end to end with a fake LM ------------------------------


PLAN_TEXT = (
    "## Physical layout\n"
    "Store table t column-major: a contiguous int32 array for id and an "
    "offset+data pair for name.\n"
    "## Access paths\n"
    "id is the primary key and the only predicate column, so keep it sorted.\n"
)


@pytest.fixture
def dummy_lm():
    """A DummyLM returning one canned storage plan."""
    pytest.importorskip("dspy")
    from dspy.utils.dummies import DummyLM

    return DummyLM(
        [{"reasoning": "fake reasoning", "storage_plan": PLAN_TEXT, "rationale": "Q1 needs id."}]
        * 8
    )


@pytest.fixture
def patched_configure(monkeypatch, dummy_lm):
    """Route the stage's dspy.configure at a DummyLM instead of a provider.

    Patching the stage's imported name keeps the production call path intact:
    the stage still calls configure_dspy, still registers the trace callbacks,
    still selects a predictor.
    """
    import dspy

    calls = []

    def fake_configure(model_cfg, callbacks=None, require_key=True):
        calls.append(model_cfg)
        settings = {"lm": dummy_lm, "track_usage": model_cfg.track_usage}
        if callbacks:
            settings["callbacks"] = list(callbacks)
        dspy.configure(**settings)
        return dummy_lm

    monkeypatch.setattr("agent.stages.storage_plan.configure_dspy", fake_configure)
    return calls


class TestStoragePlanStage:
    def test_produces_a_plan_artifact(self, loaded_config, manifest_path, patched_configure):
        pipeline = Pipeline(loaded_config, manifest_path=manifest_path)
        summary = pipeline.run(only=["storage_plan"])
        assert summary.ok, summary.report()

        result = summary.results[0]
        assert set(result.artifacts) == {"storage_plan", "storage_plan_meta"}
        # Compared stripped: DSPy's adapter trims trailing whitespace off a
        # parsed output field.
        assert result.artifacts["storage_plan"].read_text().strip() == PLAN_TEXT.strip()

        meta = result.artifacts["storage_plan_meta"].read_json()
        assert meta["rationale"] == "Q1 needs id."
        assert meta["model"] == "openai/gpt-4o-mini"
        assert meta["prompt_ids"] == ["storage_plan_policy"]

    def test_uses_chain_of_thought_for_small_inputs(
        self, loaded_config, manifest_path, patched_configure
    ):
        """Below the threshold, RLM's REPL overhead is not worth paying."""
        summary = Pipeline(loaded_config, manifest_path=manifest_path).run(only=["storage_plan"])
        assert "ChainOfThought" in summary.results[0].metrics["predictor"]

    def test_second_run_hits_the_cache_instead_of_the_model(
        self, loaded_config, manifest_path, patched_configure
    ):
        pipeline = Pipeline(loaded_config, manifest_path=manifest_path)
        first = pipeline.run(only=["storage_plan"])
        assert first.results[0].metrics["cache_hit"] is False
        assert len(patched_configure) == 1

        # --force bypasses the artifact skip but must still hit the disk cache.
        second = pipeline.run(only=["storage_plan"], force=True)
        assert second.results[0].metrics["cache_hit"] is True
        assert len(patched_configure) == 1  # the model was never called again

    def test_editing_the_prompt_invalidates_the_cache(
        self, loaded_config, manifest_path, patched_configure
    ):
        import json

        from agent.prompting.build_manifest import rebuild_manifest

        Pipeline(loaded_config, manifest_path=manifest_path).run(only=["storage_plan"])
        assert len(patched_configure) == 1

        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
        raw["entries"]["storage_plan_policy"]["text"] += "\nExtra rule.\n"
        manifest_path.write_text(json.dumps(raw), encoding="utf-8")
        rebuild_manifest(manifest_path, strict=True)

        summary = Pipeline(loaded_config, manifest_path=manifest_path).run(
            only=["storage_plan"], force=True
        )
        assert summary.results[0].metrics["cache_hit"] is False
        assert len(patched_configure) == 2

    def test_missing_schema_input_fails_clearly(
        self, loaded_config, manifest_path, patched_configure
    ):
        loaded_config.resolve_stage("storage_plan").inputs["schema"].unlink()
        summary = Pipeline(loaded_config, manifest_path=manifest_path).run(only=["storage_plan"])
        assert summary.ok is False
        assert "schema" in summary.results[0].error

    def test_trace_records_the_predictor_choice_and_lm_calls(
        self, loaded_config, manifest_path, patched_configure
    ):
        pipeline = Pipeline(loaded_config, manifest_path=manifest_path)
        summary = pipeline.run(only=["storage_plan"])
        trace = summary.trace_path
        records = [json.loads(line) for line in trace.read_text().strip().splitlines()]
        events = {record["event"] for record in records}
        assert "predictor_selected" in events
        assert "lm_start" in events and "lm_end" in events
        assert "module_start" in events

    def test_inline_policy_overrides_the_prompt_file(
        self, config_dict, manifest_path, patched_configure
    ):
        from agent.config.loader import LoadedConfig

        config_dict["stages"]["storage_plan"]["params"] = {"policy": "INLINE POLICY"}
        config = LoadedConfig(config_dict)
        summary = Pipeline(config, manifest_path=manifest_path).run(only=["storage_plan"])
        assert summary.ok, summary.report()
        meta = summary.results[0].artifacts["storage_plan_meta"].read_json()
        assert meta["prompt_ids"] == ["<inline>"]

    def test_statistics_are_optional(self, config_dict, manifest_path, patched_configure):
        from agent.config.loader import LoadedConfig

        del config_dict["common"]["inputs"]["statistics"]
        config = LoadedConfig(config_dict)
        summary = Pipeline(config, manifest_path=manifest_path).run(only=["storage_plan"])
        assert summary.ok, summary.report()

    def test_empty_model_output_is_reported_as_a_failure(
        self, loaded_config, manifest_path, monkeypatch
    ):
        import dspy
        from dspy.utils.dummies import DummyLM

        empty = DummyLM([{"reasoning": "", "storage_plan": "  ", "rationale": ""}] * 4)

        def fake_configure(model_cfg, callbacks=None, require_key=True):
            dspy.configure(lm=empty, callbacks=list(callbacks) if callbacks else None)
            return empty

        monkeypatch.setattr("agent.stages.storage_plan.configure_dspy", fake_configure)
        summary = Pipeline(loaded_config, manifest_path=manifest_path).run(only=["storage_plan"])
        assert summary.ok is False
        assert "empty storage plan" in summary.results[0].error

    def test_the_plan_reaches_divide_as_an_input(
        self, loaded_config, manifest_path, patched_configure, monkeypatch
    ):
        """The handoff: what storage_plan wrote is what divide reads."""
        seen = {}

        def capture(signature, payload, outputs, **kwargs):
            seen.update(payload)
            return {
                "schema_levels": json.dumps(
                    {
                        "levels": {"no_hints": "facts", "all_hints": "facts and hints"},
                        "data_structure_descriptions": {"no_hints": "d", "all_hints": "d"},
                    }
                ),
                "predictor": "Fake",
                "usage": {},
            }

        monkeypatch.setattr("agent.stages.divide.invoke", capture)
        summary = Pipeline(loaded_config, manifest_path=manifest_path).run(
            only=["storage_plan", "divide"]
        )
        assert summary.ok, summary.report()
        assert seen["storage_plan"].strip() == PLAN_TEXT.strip()
        assert seen["level_names"] == "no_hints, all_hints"
