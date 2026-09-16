"""The four code stages, end to end and offline.

No API key is used anywhere: the model is either DSPy's ``DummyLM`` (so the real
path — signature construction, adapter, callbacks, caching, artifact writing —
is exercised and only the provider round trip is stubbed) or a queued fake
``invoke`` where a test needs to control what each successive call returns.

The compiler and the "engine" are real: ``hppgen`` really runs g++ over the
headers it generates, and ``query_codegen`` really compiles and executes the
source it produced, so a loop that claims success has actually produced code
that builds and runs.
"""

import json

import pytest

from agent.config.loader import LoadedConfig
from agent.pipeline import Pipeline
from agent.stages.base import ArtifactStore

# --- fakes ----------------------------------------------------------------


@pytest.fixture
def dummy_model(monkeypatch):
    """Point every new stage at a DummyLM through the real configure path."""

    def install(answers):
        import dspy
        from dspy.utils.dummies import DummyLM

        lm = DummyLM(list(answers))
        calls = []

        def fake_configure(model_cfg, callbacks=None, require_key=True):
            calls.append(model_cfg)
            settings = {"lm": lm, "track_usage": model_cfg.track_usage}
            if callbacks:
                settings["callbacks"] = list(callbacks)
            dspy.configure(**settings)
            return lm

        monkeypatch.setattr("agent.stages.predict.configure_dspy", fake_configure)
        return calls

    return install


@pytest.fixture
def queued_invoke(monkeypatch):
    """Replace a stage's ``invoke`` with a queue of canned answers.

    Used where a test drives several model calls with different content — a
    broken header then a fixed one, say. DummyLM could do it, but the queue
    makes the *order* of calls explicit, which is what those tests assert on.
    """

    def install(module, answers):
        queue = list(answers)
        calls = []

        def fake_invoke(signature, payload, outputs, **kwargs):
            calls.append(dict(payload))
            value = queue.pop(0) if queue else {}
            result = {name: value.get(name, "") for name in outputs}
            result["predictor"] = "Queued"
            result["usage"] = {}
            return result

        monkeypatch.setattr(f"agent.stages.{module}.invoke", fake_invoke)
        return calls

    return install


# --- fixtures -------------------------------------------------------------


GOOD_HEADER = """#pragma once
#include <cstdint>
#include <vector>

namespace %(ns)s {

struct Customer {
    std::vector<int32_t> c_custkey;
    std::vector<int32_t> c_name_offsets;
    std::vector<char> c_name_data;
};

struct Database {
    Customer customer;
};

}  // namespace %(ns)s
"""

BROKEN_HEADER = """#pragma once
namespace %(ns)s {
struct Customer { std::vector<int> ids }  // missing semicolon and include
}
"""

LEVELS_JSON = {
    "levels": {
        "no_hints": "Table customer(c_custkey INTEGER PRIMARY KEY, c_name VARCHAR(25)).",
        "all_hints": "Table customer; store c_custkey as a sorted int32 array.",
    },
    "data_structure_descriptions": {
        "no_hints": "Column arrays per table.",
        "all_hints": "Column arrays with a sorted key array.",
    },
}


@pytest.fixture
def levels_artifact(config_dict):
    """A ``schema_levels`` artifact on disk, as divide would have written it."""

    def install(payload=None):
        store = ArtifactStore(LoadedConfig(config_dict).resolve_stage("hppgen").artifacts_dir)
        return store.put_json("divide", "schema_levels", payload or LEVELS_JSON)

    return install


def _config(config_dict, stages):
    config_dict["stages"] = stages
    return LoadedConfig(config_dict)


def _run(config, manifest_path, stage, force=False):
    return Pipeline(config, manifest_path=manifest_path).run(only=[stage], force=force)


# --- divide ---------------------------------------------------------------


class TestDivideStage:
    @pytest.fixture
    def plan_artifact(self, config_dict):
        store = ArtifactStore(LoadedConfig(config_dict).resolve_stage("divide").artifacts_dir)
        return store.put_text("storage_plan", "storage_plan", "Store customer column-major.")

    def test_produces_one_entry_per_declared_level(
        self, config_dict, manifest_path, plan_artifact, dummy_model
    ):
        dummy_model([{"reasoning": "r", "schema_levels": json.dumps(LEVELS_JSON)}] * 4)
        summary = _run(_config(config_dict, {"divide": {}}), manifest_path, "divide")
        assert summary.ok, summary.report()

        split = summary.results[0].artifacts["schema_levels"].read_json()
        assert sorted(split["levels"]) == ["all_hints", "no_hints"]
        assert sorted(split["data_structure_descriptions"]) == ["all_hints", "no_hints"]
        assert summary.results[0].metrics["levels"] == 2

    def test_the_level_names_reach_the_prompt(
        self, config_dict, manifest_path, plan_artifact, queued_invoke
    ):
        calls = queued_invoke("divide", [{"schema_levels": json.dumps(LEVELS_JSON)}])
        _run(_config(config_dict, {"divide": {}}), manifest_path, "divide")
        assert calls[0]["level_names"] == "no_hints, all_hints"
        assert "no_hints, all_hints" in calls[0]["policy"]

    def test_fenced_json_is_accepted(
        self, config_dict, manifest_path, plan_artifact, queued_invoke
    ):
        queued_invoke("divide", [{"schema_levels": f"```json\n{json.dumps(LEVELS_JSON)}\n```"}])
        summary = _run(_config(config_dict, {"divide": {}}), manifest_path, "divide")
        assert summary.ok, summary.report()

    def test_a_missing_level_fails_here_rather_than_in_hppgen(
        self, config_dict, manifest_path, plan_artifact, queued_invoke
    ):
        queued_invoke(
            "divide", [{"schema_levels": json.dumps({"levels": {"no_hints": "only one"}})}]
        )
        summary = _run(_config(config_dict, {"divide": {}}), manifest_path, "divide")
        assert not summary.ok
        assert "missing level(s): all_hints" in summary.results[0].error

    def test_an_empty_level_is_rejected(
        self, config_dict, manifest_path, plan_artifact, queued_invoke
    ):
        payload = {"levels": {"no_hints": "text", "all_hints": "   "}}
        queued_invoke("divide", [{"schema_levels": json.dumps(payload)}])
        summary = _run(_config(config_dict, {"divide": {}}), manifest_path, "divide")
        assert not summary.ok
        assert "Level text is empty for: all_hints" in summary.results[0].error

    def test_unparseable_output_fails_clearly(
        self, config_dict, manifest_path, plan_artifact, queued_invoke
    ):
        queued_invoke("divide", [{"schema_levels": "I'd rather not"}])
        summary = _run(_config(config_dict, {"divide": {}}), manifest_path, "divide")
        assert not summary.ok
        assert "no JSON object" in summary.results[0].error

    def test_a_malformed_split_is_not_cached(
        self, config_dict, manifest_path, plan_artifact, queued_invoke
    ):
        """Caching a broken answer would make every retry return it."""
        calls = queued_invoke(
            "divide",
            [
                {"schema_levels": "nonsense"},
                {"schema_levels": json.dumps(LEVELS_JSON)},
            ],
        )
        config = _config(config_dict, {"divide": {}})
        assert not _run(config, manifest_path, "divide").ok
        assert _run(config, manifest_path, "divide").ok
        assert len(calls) == 2

    def test_a_second_run_hits_the_cache(
        self, config_dict, manifest_path, plan_artifact, queued_invoke
    ):
        calls = queued_invoke("divide", [{"schema_levels": json.dumps(LEVELS_JSON)}])
        config = _config(config_dict, {"divide": {}})
        first = Pipeline(config, manifest_path=manifest_path).run(only=["divide"])
        assert first.results[0].metrics["cache_hit"] is False

        second = Pipeline(config, manifest_path=manifest_path).run(only=["divide"], force=True)
        assert second.results[0].metrics["cache_hit"] is True
        assert len(calls) == 1

    def test_an_inline_policy_overrides_the_prompt_file(
        self, config_dict, manifest_path, plan_artifact, queued_invoke
    ):
        calls = queued_invoke("divide", [{"schema_levels": json.dumps(LEVELS_JSON)}])
        config = _config(
            config_dict, {"divide": {"params": {"policy": "Split it: ${level_names}"}}}
        )
        summary = _run(config, manifest_path, "divide")
        assert summary.ok, summary.report()
        assert calls[0]["policy"] == "Split it: no_hints, all_hints"
        meta = summary.results[0].artifacts["schema_levels"].meta
        assert meta["prompt_ids"] == ["<inline>"]

    def test_no_declared_levels_is_a_config_error(
        self, config_dict, manifest_path, plan_artifact, queued_invoke
    ):
        queued_invoke("divide", [])
        config_dict["common"]["levels"] = []
        summary = _run(_config(config_dict, {"divide": {}}), manifest_path, "divide")
        assert not summary.ok
        assert "common.levels" in summary.results[0].error


# --- hppgen ---------------------------------------------------------------


class TestHppGenStage:
    def test_generates_and_compiles_a_header_per_active_level(
        self, config_dict, manifest_path, levels_artifact, dummy_model
    ):
        levels_artifact()
        dummy_model(
            [
                {"reasoning": "r", "header_code": GOOD_HEADER % {"ns": "basic"}, "notes": "n"},
                {"reasoning": "r", "header_code": GOOD_HEADER % {"ns": "full"}, "notes": "n"},
            ]
        )
        summary = _run(_config(config_dict, {"hppgen": {}}), manifest_path, "hppgen")
        assert summary.ok, summary.report()

        doc = summary.results[0].artifacts["storage_layout_headers"].read_json()
        assert sorted(doc["headers"]) == ["all_hints", "no_hints"]
        from pathlib import Path

        for level, path in doc["headers"].items():
            assert Path(path).exists(), level
            assert "#pragma once" in Path(path).read_text()
        assert doc["detail"]["no_hints"]["verified"] is True
        assert doc["detail"]["no_hints"]["repair_rounds"] == 0
        assert summary.results[0].metrics["rounds[no_hints]"] == 0

    def test_only_the_active_level_is_generated(
        self, config_dict, manifest_path, levels_artifact, queued_invoke
    ):
        levels_artifact()
        calls = queued_invoke("hppgen", [{"header_code": GOOD_HEADER % {"ns": "basic"}}])
        config = _config(config_dict, {"hppgen": {"active_levels": ["no_hints"]}})
        summary = _run(config, manifest_path, "hppgen")
        assert summary.ok, summary.report()
        assert len(calls) == 1
        assert calls[0]["level_name"] == "no_hints"
        assert calls[0]["namespace"] == "basic"
        doc = summary.results[0].artifacts["storage_layout_headers"].read_json()
        assert list(doc["headers"]) == ["no_hints"]

    def test_a_broken_header_is_repaired_and_the_round_is_counted(
        self, config_dict, manifest_path, levels_artifact, queued_invoke
    ):
        levels_artifact()
        calls = queued_invoke(
            "hppgen",
            [
                {"header_code": BROKEN_HEADER % {"ns": "basic"}},
                {"fixed_code": GOOD_HEADER % {"ns": "basic"}, "explanation": "added the include"},
            ],
        )
        config = _config(config_dict, {"hppgen": {"active_levels": ["no_hints"]}})
        summary = _run(config, manifest_path, "hppgen")
        assert summary.ok, summary.report()

        doc = summary.results[0].artifacts["storage_layout_headers"].read_json()
        assert doc["detail"]["no_hints"]["repair_rounds"] == 1
        assert doc["detail"]["no_hints"]["verified"] is True
        # The repair call saw the real diagnostics, not a generic message.
        assert "error:" in calls[1]["report"]

    def test_the_fix_budget_is_enforced_and_the_artifact_still_written(
        self, config_dict, manifest_path, levels_artifact, queued_invoke
    ):
        levels_artifact()
        queued_invoke(
            "hppgen",
            [
                {"header_code": BROKEN_HEADER % {"ns": "basic"}},
                {"fixed_code": BROKEN_HEADER % {"ns": "basic"} + "// try 1\n"},
                {"fixed_code": BROKEN_HEADER % {"ns": "basic"} + "// try 2\n"},
                {"fixed_code": GOOD_HEADER % {"ns": "basic"}},
            ],
        )
        config = _config(
            config_dict,
            {"hppgen": {"active_levels": ["no_hints"], "compile": {"max_fix_rounds": 2}}},
        )
        summary = _run(config, manifest_path, "hppgen")
        assert not summary.ok
        assert "did not compile" in summary.results[0].error

        doc = summary.results[0].artifacts["storage_layout_headers"].read_json()
        assert doc["detail"]["no_hints"]["verified"] is False
        assert doc["detail"]["no_hints"]["stopped"] == "budget exhausted"
        assert doc["detail"]["no_hints"]["repair_rounds"] == 2

    def test_a_model_that_repeats_itself_stops_the_loop_early(
        self, config_dict, manifest_path, levels_artifact, queued_invoke
    ):
        levels_artifact()
        broken = BROKEN_HEADER % {"ns": "basic"}
        calls = queued_invoke(
            "hppgen", [{"header_code": broken}, {"fixed_code": broken}, {"fixed_code": broken}]
        )
        config = _config(
            config_dict,
            {"hppgen": {"active_levels": ["no_hints"], "compile": {"max_fix_rounds": 5}}},
        )
        summary = _run(config, manifest_path, "hppgen")
        assert not summary.ok
        doc = summary.results[0].artifacts["storage_layout_headers"].read_json()
        assert doc["detail"]["no_hints"]["stopped"] == "no progress"
        assert len(calls) == 2  # generate, one repair, then stop

    def test_only_a_verified_header_is_cached(
        self, config_dict, manifest_path, levels_artifact, queued_invoke
    ):
        """A re-run must retry a broken header, not serve it from disk."""
        levels_artifact()
        broken = BROKEN_HEADER % {"ns": "basic"}
        calls = queued_invoke(
            "hppgen",
            [
                {"header_code": broken},
                {"fixed_code": broken},
                {"header_code": GOOD_HEADER % {"ns": "basic"}},
            ],
        )
        config = _config(
            config_dict,
            {"hppgen": {"active_levels": ["no_hints"], "compile": {"max_fix_rounds": 1}}},
        )
        assert not _run(config, manifest_path, "hppgen").ok
        before = len(calls)

        summary = _run(config, manifest_path, "hppgen")
        assert summary.ok, summary.report()
        assert len(calls) > before  # the model was asked again

        # --force bypasses the artifact skip, so this exercises the disk cache:
        # the verified header comes back without another model call.
        after = len(calls)
        cached = _run(config, manifest_path, "hppgen", force=True)
        assert cached.ok
        doc = cached.results[0].artifacts["storage_layout_headers"].read_json()
        assert doc["detail"]["no_hints"]["cache_hit"] is True
        assert len(calls) == after

    def test_a_level_missing_from_the_split_is_reported(
        self, config_dict, manifest_path, levels_artifact, queued_invoke
    ):
        levels_artifact({"levels": {"no_hints": "text"}, "data_structure_descriptions": {}})
        queued_invoke("hppgen", [])
        summary = _run(_config(config_dict, {"hppgen": {}}), manifest_path, "hppgen")
        assert not summary.ok
        assert "no text for level(s): all_hints" in summary.results[0].error

    def test_an_undeclared_active_level_is_rejected(
        self, config_dict, manifest_path, levels_artifact, queued_invoke
    ):
        levels_artifact()
        queued_invoke("hppgen", [])
        config = _config(config_dict, {"hppgen": {"active_levels": ["nope"]}})
        summary = _run(config, manifest_path, "hppgen")
        assert not summary.ok
        assert "not declared in common.levels" in summary.results[0].error

    def test_a_missing_gen_project_root_is_a_clear_error(
        self, config_dict, manifest_path, levels_artifact, queued_invoke
    ):
        levels_artifact()
        queued_invoke("hppgen", [])
        del config_dict["common"]["gen_project_root"]
        summary = _run(_config(config_dict, {"hppgen": {}}), manifest_path, "hppgen")
        assert not summary.ok
        assert "gen_project_root" in summary.results[0].error


# --- query_codegen --------------------------------------------------------


# A real program: the stage syntax-checks it, then the run command compiles and
# executes it, so "matched" means code that actually builds and runs.
def _engine_source(rows: str) -> str:
    lines = "".join(f'    std::cout << "{row}\\n";\n' for row in rows.split("|"))
    return f"#include <iostream>\n\nint main() {{\n{lines}    return 0;\n}}\n"


BROKEN_SOURCE = "#include <iostream>\nint main() { std::cout << unclosed\n"

# Compiles the generated source and captures its output. {query_id} keeps it
# general; {output} makes the runner read the file rather than stdout.
RUN_COMMAND = (
    "sh -c 'g++ -std=c++20 -o engine_{query_id} src/queries/{query_id}.cpp "
    "&& ./engine_{query_id} > {output}'"
)


@pytest.fixture
def codegen_setup(config_dict, tmp_path):
    """Header artifact, gold results and a real run command."""

    def install(gold_rows="name,id\nAlice,1\n", write_gold=True):
        config = LoadedConfig(config_dict)
        cfg = config.resolve_stage("query_codegen")

        header = cfg.gen_project_root / "basic.hpp"
        header.parent.mkdir(parents=True, exist_ok=True)
        header.write_text(GOOD_HEADER % {"ns": "basic"}, encoding="utf-8")

        store = ArtifactStore(cfg.artifacts_dir)
        store.put_json("divide", "schema_levels", LEVELS_JSON)
        store.put_json(
            "hppgen",
            "storage_layout_headers",
            {"headers": {"no_hints": str(header)}, "detail": {}},
        )

        gold_dir = tmp_path / "gold"
        gold_dir.mkdir(exist_ok=True)
        if write_gold:
            (gold_dir / "q1.csv").write_text(gold_rows, encoding="utf-8")

        config_dict["common"]["gold"] = {"mode": "none", "dir": str(gold_dir)}
        return gold_dir

    return install


def _codegen_stage(params=None, **overrides):
    stage = {"active_levels": ["no_hints"], "params": {"run_command": RUN_COMMAND}}
    stage["params"].update(params or {})
    stage.update(overrides)
    return {"query_codegen": stage}


class TestQueryCodegenStage:
    def test_generated_code_compiles_runs_and_matches_gold(
        self, config_dict, manifest_path, codegen_setup, dummy_model
    ):
        codegen_setup()
        dummy_model(
            [{"reasoning": "r", "source_code": _engine_source("Alice,1"), "notes": "scan"}] * 4
        )
        summary = _run(_config(config_dict, _codegen_stage()), manifest_path, "query_codegen")
        assert summary.ok, summary.report()

        result = summary.results[0]
        report = result.artifacts["correctness_report"].read_json()
        assert report["verified"] is True
        row = report["queries"]["q1"]
        assert row["status"] == "matched"
        assert row["compiled"] and row["ran"] and row["matched"]
        assert row["compile_rounds"] == 0 and row["correctness_rounds"] == 0

        from pathlib import Path

        sources = result.artifacts["query_sources"].read_json()["sources"]
        assert Path(sources["q1"]).exists()
        assert result.metrics["verified"] is True

    def test_the_generator_receives_the_header_and_the_level_text(
        self, config_dict, manifest_path, codegen_setup, queued_invoke
    ):
        codegen_setup()
        calls = queued_invoke("query_codegen", [{"source_code": _engine_source("Alice,1")}])
        _run(_config(config_dict, _codegen_stage()), manifest_path, "query_codegen")
        payload = calls[0]
        assert "#pragma once" in payload["storage_header"]
        assert payload["namespace"] == "basic"
        assert payload["query_id"] == "q1"
        assert "SELECT name FROM t" in payload["query_sql"]
        assert LEVELS_JSON["levels"]["no_hints"] in payload["level_text"]
        assert "basic::Database" in payload["task"]  # the entry signature was filled in

    def test_a_compile_error_is_repaired_within_the_compile_budget(
        self, config_dict, manifest_path, codegen_setup, queued_invoke
    ):
        codegen_setup()
        calls = queued_invoke(
            "query_codegen",
            [
                {"source_code": BROKEN_SOURCE},
                {"fixed_code": _engine_source("Alice,1"), "explanation": "closed the statement"},
            ],
        )
        summary = _run(_config(config_dict, _codegen_stage()), manifest_path, "query_codegen")
        assert summary.ok, summary.report()

        row = summary.results[0].artifacts["correctness_report"].read_json()["queries"]["q1"]
        assert row["status"] == "matched"
        assert row["compile_rounds"] == 1
        assert row["correctness_rounds"] == 0
        assert "error:" in calls[1]["report"]

    def test_a_wrong_result_is_repaired_within_the_correctness_budget(
        self, config_dict, manifest_path, codegen_setup, queued_invoke
    ):
        codegen_setup()
        calls = queued_invoke(
            "query_codegen",
            [
                {"source_code": _engine_source("Bob,7")},
                {"fixed_code": _engine_source("Alice,1"), "explanation": "fixed the predicate"},
            ],
        )
        summary = _run(_config(config_dict, _codegen_stage()), manifest_path, "query_codegen")
        assert summary.ok, summary.report()

        row = summary.results[0].artifacts["correctness_report"].read_json()["queries"]["q1"]
        assert row["status"] == "matched"
        assert row["compile_rounds"] == 0
        assert row["correctness_rounds"] == 1
        # The repair saw a locatable difference, not just "wrong".
        assert "first difference at row 1" in calls[1]["report"]

    def test_the_two_budgets_are_independent(
        self, config_dict, manifest_path, codegen_setup, queued_invoke
    ):
        """A compile error must not eat the rounds a mismatch needs."""
        codegen_setup()
        queued_invoke(
            "query_codegen",
            [
                {"source_code": BROKEN_SOURCE},
                {"fixed_code": _engine_source("Bob,7")},
                {"fixed_code": _engine_source("Alice,1")},
            ],
        )
        config = _config(
            config_dict,
            _codegen_stage(params={"max_correctness_rounds": 1}, compile={"max_fix_rounds": 1}),
        )
        summary = _run(config, manifest_path, "query_codegen")
        assert summary.ok, summary.report()
        row = summary.results[0].artifacts["correctness_report"].read_json()["queries"]["q1"]
        assert (row["compile_rounds"], row["correctness_rounds"]) == (1, 1)

    def test_an_unfixable_mismatch_is_marked_not_omitted(
        self, config_dict, manifest_path, codegen_setup, queued_invoke
    ):
        codegen_setup()
        queued_invoke(
            "query_codegen",
            [
                {"source_code": _engine_source("Bob,7")},
                {"fixed_code": _engine_source("Carol,8")},
                {"fixed_code": _engine_source("Dave,9")},
            ],
        )
        config = _config(config_dict, _codegen_stage(params={"max_correctness_rounds": 2}))
        summary = _run(config, manifest_path, "query_codegen")
        assert not summary.ok
        assert "did not match gold" in summary.results[0].error

        report = summary.results[0].artifacts["correctness_report"].read_json()
        assert report["verified"] is False
        row = report["queries"]["q1"]
        assert row["status"] == "mismatch"
        assert row["correctness_rounds"] == 2
        assert row["mismatch"]["row"] == 1
        assert row["mismatch"]["expected"] == "Alice"

    def test_an_unfixable_compile_error_is_marked(
        self, config_dict, manifest_path, codegen_setup, queued_invoke
    ):
        codegen_setup()
        queued_invoke(
            "query_codegen",
            [
                {"source_code": BROKEN_SOURCE},
                {"fixed_code": BROKEN_SOURCE + "// nope\n"},
            ],
        )
        config = _config(config_dict, _codegen_stage(compile={"max_fix_rounds": 1}))
        summary = _run(config, manifest_path, "query_codegen")
        assert not summary.ok
        assert "did not build" in summary.results[0].error
        row = summary.results[0].artifacts["correctness_report"].read_json()["queries"]["q1"]
        assert row["status"] == "compile_failed"
        assert row["compiled"] is False

    def test_without_a_run_command_correctness_is_not_claimed(
        self, config_dict, manifest_path, codegen_setup, queued_invoke
    ):
        """The stage delivered code that compiles and says nothing more."""
        codegen_setup()
        queued_invoke("query_codegen", [{"source_code": _engine_source("Alice,1")}])
        config = _config(config_dict, {"query_codegen": {"active_levels": ["no_hints"]}})
        summary = _run(config, manifest_path, "query_codegen")
        assert summary.ok, summary.report()

        result = summary.results[0]
        assert result.metrics["verified"] is False
        assert result.metrics["unverified"] == 1
        row = result.artifacts["correctness_report"].read_json()["queries"]["q1"]
        assert row["status"] == "unverified"
        assert row["compiled"] is True and row["matched"] is False
        assert "run_command" in row["report"]

    def test_missing_gold_is_reported_per_query(
        self, config_dict, manifest_path, codegen_setup, queued_invoke
    ):
        codegen_setup(write_gold=False)
        queued_invoke("query_codegen", [{"source_code": _engine_source("Alice,1")}])
        summary = _run(_config(config_dict, _codegen_stage()), manifest_path, "query_codegen")
        assert summary.ok, summary.report()
        row = summary.results[0].artifacts["correctness_report"].read_json()["queries"]["q1"]
        assert row["status"] == "no_gold"
        assert "must already exist" in row["report"]

    def test_the_build_is_skipped_when_the_tree_is_not_a_cmake_project(
        self, config_dict, manifest_path, codegen_setup, queued_invoke
    ):
        """Otherwise every query fails on a configuration error, not on its code."""
        codegen_setup()
        queued_invoke("query_codegen", [{"source_code": _engine_source("Alice,1")}])
        summary = _run(_config(config_dict, _codegen_stage()), manifest_path, "query_codegen")
        assert summary.ok, summary.report()
        row = summary.results[0].artifacts["correctness_report"].read_json()["queries"]["q1"]
        assert row["built"] is False and row["status"] == "matched"

    def test_only_a_matched_query_is_cached(
        self, config_dict, manifest_path, codegen_setup, queued_invoke
    ):
        codegen_setup()
        calls = queued_invoke(
            "query_codegen",
            [
                {"source_code": _engine_source("Bob,7")},
                {"source_code": _engine_source("Alice,1")},
            ],
        )
        config = _config(config_dict, _codegen_stage(params={"max_correctness_rounds": 0}))
        assert not _run(config, manifest_path, "query_codegen").ok
        assert len(calls) == 1

        assert _run(config, manifest_path, "query_codegen").ok
        assert len(calls) == 2

        after = len(calls)
        again = _run(config, manifest_path, "query_codegen", force=True)
        assert again.ok
        row = again.results[0].artifacts["correctness_report"].read_json()["queries"]["q1"]
        assert row["cache_hit"] is True
        assert len(calls) == after

    def test_more_than_one_active_level_is_rejected_with_instructions(
        self, config_dict, manifest_path, codegen_setup, queued_invoke
    ):
        codegen_setup()
        queued_invoke("query_codegen", [])
        config = _config(
            config_dict, {"query_codegen": {"active_levels": ["no_hints", "all_hints"]}}
        )
        summary = _run(config, manifest_path, "query_codegen")
        assert not summary.ok
        assert "one level at a time" in summary.results[0].error
        assert "once per level" in summary.results[0].error

    def test_a_missing_header_for_the_level_says_to_run_hppgen(
        self, config_dict, manifest_path, codegen_setup, queued_invoke
    ):
        codegen_setup()
        store = ArtifactStore(
            LoadedConfig(config_dict).resolve_stage("query_codegen").artifacts_dir
        )
        store.put_json("hppgen", "storage_layout_headers", {"headers": {}, "detail": {}})
        queued_invoke("query_codegen", [])
        summary = _run(_config(config_dict, _codegen_stage()), manifest_path, "query_codegen")
        assert not summary.ok
        assert "Run hppgen" in summary.results[0].error

    def test_every_query_in_the_workload_is_reported(
        self, config_dict, manifest_path, codegen_setup, queued_invoke, project_inputs
    ):
        (project_inputs / "queries.txt").write_text(
            "-- Q1: first\nSELECT 1;\n-- Q2: second\nSELECT 2;\n", encoding="utf-8"
        )
        gold_dir = codegen_setup()
        (gold_dir / "q2.csv").write_text("name,id\nAlice,1\n", encoding="utf-8")
        queued_invoke(
            "query_codegen",
            [
                {"source_code": _engine_source("Alice,1")},
                {"source_code": _engine_source("Alice,1")},
            ],
        )
        summary = _run(_config(config_dict, _codegen_stage()), manifest_path, "query_codegen")
        assert summary.ok, summary.report()
        report = summary.results[0].artifacts["correctness_report"].read_json()
        assert sorted(report["queries"]) == ["q1", "q2"]
        assert report["counts"] == {"matched": 2}


# --- optimize -------------------------------------------------------------


# A stand-in engine whose runtime is whatever its source declares, so a round's
# keep-or-revert decision is exercised deterministically rather than by racing
# the machine. Line 1 is the reported runtime; the rest is the query output.
def _timed_engine(runtime: str, rows: str = "Alice,1") -> str:
    return f"// RUNTIME {runtime}\n" + "\n".join(rows.split("|")) + "\n"


OPTIMIZE_RUN_COMMAND = (
    "sh -c 'tail -n +2 src/queries/{query_id}.cpp > {output}; "
    "head -1 src/queries/{query_id}.cpp'"
)


@pytest.fixture
def optimize_setup(config_dict, tmp_path):
    """A verified baseline: sources, a passing correctness report and gold."""

    def install(runtime="1.0", matched=True):
        config = LoadedConfig(config_dict)
        cfg = config.resolve_stage("optimize")

        source = cfg.gen_project_root / "src" / "queries" / "q1.cpp"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text(_timed_engine(runtime), encoding="utf-8")

        gold_dir = tmp_path / "gold"
        gold_dir.mkdir(exist_ok=True)
        (gold_dir / "q1.csv").write_text("name,id\nAlice,1\n", encoding="utf-8")
        config_dict["common"]["gold"] = {"mode": "none", "dir": str(gold_dir)}

        store = ArtifactStore(cfg.artifacts_dir)
        store.put_json(
            "query_codegen",
            "query_sources",
            {"level": "no_hints", "namespace": "basic", "sources": {"q1": str(source)}},
        )
        store.put_json(
            "query_codegen",
            "correctness_report",
            {
                "level": "no_hints",
                "verified": matched,
                "run_command": OPTIMIZE_RUN_COMMAND,
                "queries": {
                    "q1": {
                        "status": "matched" if matched else "mismatch",
                        "matched": matched,
                    }
                },
            },
        )
        return source

    return install


def _optimize_stage(**params):
    base = {
        "strategy": "trace",
        "runtime_pattern": r"RUNTIME ([0-9.]+)",
        "repeat_runs": 1,
        "target_runtime": 0.01,
        "sf": "0.25",
    }
    base.update(params)
    return {"optimize": {"params": base}}


class TestOptimizeStage:
    def test_a_measured_win_is_kept(
        self, config_dict, manifest_path, optimize_setup, queued_invoke
    ):
        source = optimize_setup(runtime="1.0")
        queued_invoke("optimize", [{"patch": _timed_engine("0.4"), "hints": "vectorized the scan"}])
        config = _config(config_dict, _optimize_stage(max_rounds=1))
        summary = _run(config, manifest_path, "optimize")
        assert summary.ok, summary.report()

        report = summary.results[0].artifacts["optimization_report"].read_json()
        assert report["baseline_s"] == 1.0
        assert report["rounds"][0]["kept"] is True
        assert report["rounds"][0]["after_s"] == 0.4
        assert report["rounds"][0]["improvement"] == 0.6
        assert report["rounds"][0]["hints"] == "vectorized the scan"
        assert report["final_s"] == 0.4
        assert report["speedup"] == 2.5
        assert "RUNTIME 0.4" in source.read_text()
        assert summary.results[0].metrics["kept"] == 1

    def test_a_marginal_round_is_reverted_and_the_file_restored(
        self, config_dict, manifest_path, optimize_setup, queued_invoke
    ):
        """The prompts only ask the model to revert; the snapshot enforces it."""
        source = optimize_setup(runtime="1.0")
        queued_invoke("optimize", [{"patch": _timed_engine("0.99"), "hints": "renamed a variable"}])
        config = _config(config_dict, _optimize_stage(max_rounds=1, min_improvement=0.05))
        summary = _run(config, manifest_path, "optimize")
        assert summary.ok, summary.report()

        report = summary.results[0].artifacts["optimization_report"].read_json()
        entry = report["rounds"][0]
        assert entry["kept"] is False
        assert "below the 5% floor" in entry["outcome"]
        assert report["final_s"] == 1.0
        assert "RUNTIME 1.0" in source.read_text()  # restored

    def test_a_correctness_regression_is_reverted(
        self, config_dict, manifest_path, optimize_setup, queued_invoke
    ):
        """Faster and wrong is not an improvement."""
        source = optimize_setup(runtime="1.0")
        queued_invoke(
            "optimize", [{"patch": _timed_engine("0.1", rows="Bob,7"), "hints": "skipped a check"}]
        )
        config = _config(config_dict, _optimize_stage(max_rounds=1))
        summary = _run(config, manifest_path, "optimize")
        assert summary.ok, summary.report()

        entry = summary.results[0].artifacts["optimization_report"].read_json()["rounds"][0]
        assert entry["kept"] is False
        assert "no longer matches gold" in entry["outcome"]
        assert "Alice,1" in source.read_text()

    def test_a_patch_that_does_not_apply_is_reverted(
        self, config_dict, manifest_path, optimize_setup, queued_invoke
    ):
        source = optimize_setup(runtime="1.0")
        queued_invoke(
            "optimize",
            [
                {
                    "patch": "*** Begin Patch\n*** Delete File: ../escape.cpp\n*** End Patch",
                    "hints": "nope",
                }
            ],
        )
        config = _config(config_dict, _optimize_stage(max_rounds=1))
        summary = _run(config, manifest_path, "optimize")
        assert summary.ok, summary.report()

        entry = summary.results[0].artifacts["optimization_report"].read_json()["rounds"][0]
        assert entry["kept"] is False
        assert "patch did not apply" in entry["outcome"]
        assert "RUNTIME 1.0" in source.read_text()

    def test_two_consecutive_weak_rounds_stop_the_loop(
        self, config_dict, manifest_path, optimize_setup, queued_invoke
    ):
        optimize_setup(runtime="1.0")
        calls = queued_invoke(
            "optimize",
            [
                {"patch": _timed_engine("0.99"), "hints": "a"},
                {"patch": _timed_engine("0.98"), "hints": "b"},
                {"patch": _timed_engine("0.10"), "hints": "c"},
            ],
        )
        config = _config(config_dict, _optimize_stage(max_rounds=5))
        summary = _run(config, manifest_path, "optimize")
        report = summary.results[0].artifacts["optimization_report"].read_json()
        assert len(report["rounds"]) == 2
        assert "two consecutive rounds" in report["stopped"]
        assert len(calls) == 2

    def test_reaching_the_target_stops_the_loop(
        self, config_dict, manifest_path, optimize_setup, queued_invoke
    ):
        optimize_setup(runtime="1.0")
        calls = queued_invoke(
            "optimize",
            [
                {"patch": _timed_engine("0.2"), "hints": "a"},
                {"patch": _timed_engine("0.1"), "hints": "b"},
            ],
        )
        config = _config(config_dict, _optimize_stage(max_rounds=5, target_runtime=0.5))
        summary = _run(config, manifest_path, "optimize")
        report = summary.results[0].artifacts["optimization_report"].read_json()
        assert len(report["rounds"]) == 1
        assert "target" in report["stopped"]
        assert len(calls) == 1

    def test_the_measurements_and_history_reach_the_prompt(
        self, config_dict, manifest_path, optimize_setup, queued_invoke
    ):
        optimize_setup(runtime="1.0")
        calls = queued_invoke(
            "optimize",
            [
                {"patch": _timed_engine("0.99"), "hints": "first attempt"},
                {"patch": _timed_engine("0.98"), "hints": "second attempt"},
            ],
        )
        config = _config(config_dict, _optimize_stage(max_rounds=2))
        _run(config, manifest_path, "optimize")

        assert "baseline total: 1.0000s" in calls[0]["measurements"]
        assert "this is the first" in calls[0]["measurements"]
        # The reflection step: round 1's outcome is in round 2's prompt.
        assert "round 1 (q1)" in calls[1]["measurements"]
        assert "first attempt" in calls[1]["measurements"]
        assert "0.2500" in calls[0]["instruction"] or "sf 0.25" in calls[0]["instruction"]
        assert "q1" in calls[0]["instruction"]

    def test_it_refuses_to_optimize_unverified_code(
        self, config_dict, manifest_path, optimize_setup, queued_invoke
    ):
        optimize_setup(matched=False)
        calls = queued_invoke("optimize", [])
        summary = _run(_config(config_dict, _optimize_stage()), manifest_path, "optimize")
        assert not summary.ok
        error = summary.results[0].error
        assert "Refusing to optimize code that is not verified correct" in error
        assert "q1: mismatch" in error
        assert calls == []

    def test_an_empty_correctness_report_is_refused(
        self, config_dict, manifest_path, optimize_setup, queued_invoke
    ):
        optimize_setup()
        store = ArtifactStore(LoadedConfig(config_dict).resolve_stage("optimize").artifacts_dir)
        store.put_json(
            "query_codegen",
            "correctness_report",
            {"verified": False, "run_command": OPTIMIZE_RUN_COMMAND, "queries": {}},
        )
        queued_invoke("optimize", [])
        summary = _run(_config(config_dict, _optimize_stage()), manifest_path, "optimize")
        assert not summary.ok
        assert "no per-query results" in summary.results[0].error

    def test_the_run_command_is_inherited_from_the_correctness_report(
        self, config_dict, manifest_path, optimize_setup, queued_invoke
    ):
        """So it need not be repeated in two stages' params."""
        optimize_setup(runtime="1.0")
        queued_invoke("optimize", [{"patch": _timed_engine("0.4"), "hints": "h"}])
        params = _optimize_stage(max_rounds=1)
        params["optimize"]["params"].pop("run_command", None)
        summary = _run(_config(config_dict, params), manifest_path, "optimize")
        assert summary.ok, summary.report()

    def test_without_any_run_command_it_will_not_guess(
        self, config_dict, manifest_path, optimize_setup, queued_invoke
    ):
        optimize_setup()
        store = ArtifactStore(LoadedConfig(config_dict).resolve_stage("optimize").artifacts_dir)
        store.put_json(
            "query_codegen",
            "correctness_report",
            {
                "verified": True,
                "run_command": "",
                "queries": {"q1": {"status": "matched", "matched": True}},
            },
        )
        queued_invoke("optimize", [])
        summary = _run(_config(config_dict, _optimize_stage()), manifest_path, "optimize")
        assert not summary.ok
        assert "needs a run command" in summary.results[0].error

    def test_an_unknown_strategy_lists_the_available_ones(
        self, config_dict, manifest_path, optimize_setup, queued_invoke
    ):
        optimize_setup()
        queued_invoke("optimize", [])
        config = _config(config_dict, _optimize_stage(strategy="vibes"))
        summary = _run(config, manifest_path, "optimize")
        assert not summary.ok
        assert "Unknown optimize strategy" in summary.results[0].error
        assert "expert_knowledge" in summary.results[0].error

    @pytest.mark.parametrize(
        "strategy", ["trace", "expert_knowledge", "human_reference", "sample_plan"]
    )
    def test_every_strategy_renders_its_prompt(
        self, config_dict, manifest_path, optimize_setup, queued_invoke, strategy
    ):
        """Strict rendering means a missing placeholder is a hard error."""
        optimize_setup(runtime="1.0")
        calls = queued_invoke("optimize", [{"patch": _timed_engine("0.4"), "hints": "h"}])
        config = _config(config_dict, _optimize_stage(strategy=strategy, max_rounds=1))
        summary = _run(config, manifest_path, "optimize")
        assert summary.ok, summary.report()
        instruction = calls[0]["instruction"]
        assert "Hard constraints" in instruction  # the constraints fragment composed in
        assert "${" not in instruction

    def test_a_baseline_that_cannot_be_measured_is_reported(
        self, config_dict, manifest_path, optimize_setup, queued_invoke
    ):
        optimize_setup()
        queued_invoke("optimize", [])
        config = _config(config_dict, _optimize_stage(run_command="false"))
        summary = _run(config, manifest_path, "optimize")
        assert not summary.ok
        assert "baseline could not be measured" in summary.results[0].error


# --- the whole pipeline ---------------------------------------------------


# One engine, used by both code stages: rows on stdout for the correctness
# comparison, its runtime on stderr for the optimizer to measure. The run
# command recompiles on every invocation, so an optimization round's edit
# really does change what is measured.
def _pipeline_engine(runtime: str) -> str:
    return (
        "#include <iostream>\n"
        "\n"
        "int main() {\n"
        f'    std::cerr << "RUNTIME {runtime}\\n";\n'
        '    std::cout << "Alice,1\\n";\n'
        "    return 0;\n"
        "}\n"
    )


PIPELINE_RUN_COMMAND = (
    "sh -c 'g++ -std=c++20 -o engine_{query_id} src/queries/{query_id}.cpp "
    "&& ./engine_{query_id} > {output}'"
)


class TestWholePipeline:
    """All five stages in one run, with only the provider stubbed.

    The point is the wiring: each stage's artifact has to be exactly what the
    next one reads. Everything else is real — g++ compiles the header and the
    query, the engine is executed, its output is compared against a gold file,
    and the optimizer measures a genuine change in the engine's runtime.
    """

    @pytest.fixture
    def full_config(self, config_dict, tmp_path):
        gold_dir = tmp_path / "gold"
        gold_dir.mkdir()
        (gold_dir / "q1.csv").write_text("name,id\nAlice,1\n", encoding="utf-8")
        config_dict["common"]["gold"] = {"mode": "none", "dir": str(gold_dir)}
        config_dict["stages"] = {
            "storage_plan": {"prompt_ids": ["storage_plan_policy"]},
            "divide": {},
            "hppgen": {"active_levels": ["no_hints"]},
            "query_codegen": {
                "active_levels": ["no_hints"],
                "params": {"run_command": PIPELINE_RUN_COMMAND},
            },
            "optimize": {
                "params": {
                    "strategy": "trace",
                    "runtime_pattern": r"RUNTIME ([0-9.]+)",
                    "repeat_runs": 1,
                    "max_rounds": 1,
                    "target_runtime": 0.01,
                    "sf": "0.25",
                }
            },
        }
        return LoadedConfig(config_dict)

    def test_five_stages_hand_their_artifacts_along(
        self, full_config, manifest_path, monkeypatch, dummy_model
    ):
        import dspy
        from dspy.utils.dummies import DummyLM

        # storage_plan keeps its own configure seam; the rest share predict's.
        plan_lm = DummyLM([{"reasoning": "r", "storage_plan": "PLAN", "rationale": "why"}] * 4)

        def configure_plan(model_cfg, callbacks=None, require_key=True):
            dspy.configure(lm=plan_lm, callbacks=list(callbacks) if callbacks else None)
            return plan_lm

        monkeypatch.setattr("agent.stages.storage_plan.configure_dspy", configure_plan)

        # In call order: divide, hppgen, query_codegen, optimize.
        dummy_model(
            [
                {"reasoning": "r", "schema_levels": json.dumps(LEVELS_JSON)},
                {"reasoning": "r", "header_code": GOOD_HEADER % {"ns": "basic"}, "notes": "n"},
                {"reasoning": "r", "source_code": _pipeline_engine("1.0"), "notes": "n"},
                {"reasoning": "r", "patch": _pipeline_engine("0.2"), "hints": "tightened the loop"},
            ]
        )

        summary = Pipeline(full_config, manifest_path=manifest_path).run()
        assert summary.ok, summary.report()
        assert [r.stage for r in summary.results] == [
            "storage_plan",
            "divide",
            "hppgen",
            "query_codegen",
            "optimize",
        ]
        assert all(r.status == "ok" for r in summary.results), summary.report()

        artifacts = {key: value for r in summary.results for key, value in r.artifacts.items()}
        assert set(artifacts) == {
            "storage_plan",
            "storage_plan_meta",
            "schema_levels",
            "storage_layout_headers",
            "query_sources",
            "correctness_report",
            "optimization_report",
        }

        # The query was really compiled, run and checked against gold.
        correctness = artifacts["correctness_report"].read_json()
        assert correctness["verified"] is True
        assert correctness["queries"]["q1"]["status"] == "matched"

        # And the optimizer measured a real improvement and kept it.
        optimization = artifacts["optimization_report"].read_json()
        assert optimization["baseline_s"] == 1.0
        assert optimization["rounds"][0]["kept"] is True
        assert optimization["final_s"] == 0.2

    def test_a_second_run_skips_every_stage(self, full_config, manifest_path, monkeypatch):
        """Resumption: nothing that could change the output has changed."""
        calls = []

        def fake_invoke(signature, payload, outputs, **kwargs):
            calls.append(outputs)
            answer = {
                "storage_plan": "PLAN",
                "rationale": "why",
                "schema_levels": json.dumps(LEVELS_JSON),
                "header_code": GOOD_HEADER % {"ns": "basic"},
                "source_code": _pipeline_engine("1.0"),
                "patch": _pipeline_engine("0.2"),
                "hints": "h",
                "notes": "n",
            }
            result = {name: answer.get(name, "") for name in outputs}
            result["predictor"] = "Queued"
            result["usage"] = {}
            return result

        for module in ("divide", "hppgen", "query_codegen", "optimize"):
            monkeypatch.setattr(f"agent.stages.{module}.invoke", fake_invoke)
        monkeypatch.setattr(
            "agent.stages.storage_plan.StoragePlanStage._invoke",
            lambda self, payload, chars: {
                "storage_plan": "PLAN",
                "rationale": "why",
                "predictor": "Queued",
                "usage": {},
            },
        )

        pipeline = Pipeline(full_config, manifest_path=manifest_path)
        assert pipeline.run().ok
        before = len(calls)

        second = pipeline.run()
        assert second.ok, second.report()
        assert [r.status for r in second.results] == ["skipped"] * 5
        assert len(calls) == before
