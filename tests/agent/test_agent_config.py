"""Config loading, field-level inheritance, path resolution and legacy mapping."""

import json
from pathlib import Path

import pytest

from agent.config.loader import LoadedConfig, deep_merge, is_legacy, normalize_legacy
from agent.errors import ConfigError, MissingInputError


class TestDeepMerge:
    def test_merges_nested_mappings(self):
        base = {"cache": {"dir": ".cache", "enabled": True}, "model": {"name": "a"}}
        override = {"cache": {"refresh": True}}
        merged = deep_merge(base, override)
        assert merged["cache"] == {"dir": ".cache", "enabled": True, "refresh": True}
        assert merged["model"] == {"name": "a"}

    def test_replaces_lists_wholesale(self):
        # A stage overriding extra_flags means "use these", not "append these".
        merged = deep_merge({"flags": ["-O2", "-g"]}, {"flags": ["-O3"]})
        assert merged["flags"] == ["-O3"]

    def test_does_not_mutate_inputs(self):
        base = {"cache": {"dir": "a"}}
        deep_merge(base, {"cache": {"dir": "b"}})
        assert base["cache"]["dir"] == "a"


class TestStageInheritance:
    def test_stage_inherits_unset_blocks_from_common(self, loaded_config):
        stage = loaded_config.resolve_stage("storage_plan")
        assert stage.model.name == "openai/gpt-4o-mini"
        assert stage.model.temperature == 0.0

    def test_partial_override_keeps_sibling_fields(self, tmp_path, config_dict):
        """The reason merging happens on raw dicts, not validated models.

        Overriding one field of a block must not reset the block's other fields
        to class defaults.
        """
        config_dict["stages"]["storage_plan"]["cache"] = {"refresh": True}
        cfg = LoadedConfig(config_dict)
        stage = cfg.resolve_stage("storage_plan")
        assert stage.cache.refresh is True
        assert stage.cache.dir.name == ".cache"  # inherited from common
        assert stage.cache.enabled is True

    def test_model_override_is_field_level(self, config_dict):
        config_dict["stages"]["divide"]["model"] = {"name": "deepseek/deepseek-chat"}
        cfg = LoadedConfig(config_dict)
        divide = cfg.resolve_stage("divide")
        assert divide.model.name == "deepseek/deepseek-chat"
        # temperature was only set in common, and survives the override
        assert divide.model.temperature == 0.0

    def test_unknown_keys_are_rejected(self, config_dict):
        config_dict["common"]["typo_here"] = 1
        with pytest.raises(ConfigError):
            LoadedConfig(config_dict)


class TestPathResolution:
    def test_relative_paths_resolve_against_base_dir(self, loaded_config, tmp_path):
        stage = loaded_config.resolve_stage("storage_plan")
        assert stage.artifacts_dir == (tmp_path / "artifacts").resolve()
        assert stage.gen_project_root == (tmp_path / "gen").resolve()

    def test_nested_block_paths_are_resolved(self, loaded_config, tmp_path):
        stage = loaded_config.resolve_stage("storage_plan")
        assert stage.cache.dir.is_absolute()
        assert stage.trace.path == (tmp_path / "trace.jsonl").resolve()

    def test_absolute_paths_are_left_alone(self, config_dict):
        config_dict["common"]["artifacts_dir"] = "/tmp/elsewhere/artifacts"
        cfg = LoadedConfig(config_dict)
        assert cfg.resolve_stage("storage_plan").artifacts_dir == Path("/tmp/elsewhere/artifacts")

    def test_base_dir_is_relative_to_the_config_file(self, tmp_path, config_dict):
        """A relative base_dir makes a config portable between machines."""
        nested = tmp_path / "conf"
        nested.mkdir()
        config_dict["common"]["base_dir"] = "."
        path = nested / "config.json"
        path.write_text(json.dumps(config_dict), encoding="utf-8")
        cfg = LoadedConfig.from_file(path)
        assert cfg.base_dir == nested.resolve()


class TestInputs:
    def test_input_path_returns_existing_file(self, loaded_config):
        stage = loaded_config.resolve_stage("storage_plan")
        assert stage.input_path("schema").exists()

    def test_missing_key_raises_naming_the_config_key(self, loaded_config):
        stage = loaded_config.resolve_stage("storage_plan")
        with pytest.raises(MissingInputError, match="common.inputs.nonexistent"):
            stage.input_path("nonexistent")

    def test_missing_file_on_disk_raises(self, loaded_config):
        stage = loaded_config.resolve_stage("storage_plan")
        stage.inputs["schema"].unlink()
        with pytest.raises(MissingInputError, match="missing on disk"):
            stage.input_path("schema")

    def test_optional_input_returns_none(self, loaded_config):
        stage = loaded_config.resolve_stage("storage_plan")
        assert stage.input_path("nonexistent", required=False) is None


class TestLevels:
    def test_empty_active_levels_means_all(self, loaded_config):
        stage = loaded_config.resolve_stage("storage_plan")
        assert [lvl.name for lvl in stage.resolved_active_levels()] == ["no_hints", "all_hints"]

    def test_active_levels_selects_a_subset(self, config_dict):
        config_dict["stages"]["hppgen"] = {"active_levels": ["all_hints"]}
        cfg = LoadedConfig(config_dict)
        levels = cfg.resolve_stage("hppgen").resolved_active_levels()
        assert [lvl.name for lvl in levels] == ["all_hints"]
        assert levels[0].namespace == "full"

    def test_undeclared_level_raises(self, config_dict):
        config_dict["stages"]["hppgen"] = {"active_levels": ["does_not_exist"]}
        cfg = LoadedConfig(config_dict)
        with pytest.raises(ConfigError, match="not declared in common.levels"):
            cfg.resolve_stage("hppgen").resolved_active_levels()


class TestModelSignature:
    def test_signature_changes_with_anything_affecting_output(self, config_dict):
        base = LoadedConfig(config_dict).resolve_stage("storage_plan").model
        for field, value in [
            ("name", "openai/other"),
            ("temperature", 0.7),
            ("max_tokens", 500),
            ("api_base", "https://example.invalid"),
        ]:
            changed = base.model_copy(update={field: value})
            assert changed.signature() != base.signature(), field

    def test_signature_ignores_fields_that_cannot_change_output(self, config_dict):
        base = LoadedConfig(config_dict).resolve_stage("storage_plan").model
        # num_retries and lm_cache are transport concerns, not output-affecting.
        same = base.model_copy(update={"num_retries": 99, "lm_cache": False})
        assert same.signature() == base.signature()


class TestLegacyNormalization:
    """The three configs pasted into agent.md must keep working."""

    def test_canonical_config_is_not_treated_as_legacy(self, config_dict):
        assert is_legacy(config_dict) is False
        assert normalize_legacy(config_dict) == config_dict

    def test_divide_hppgen_shape(self):
        raw = {
            "common": {
                "schema_path": "../input/schema.txt",
                "storage_plan_path": "../input/storage_plan.txt",
                "levels": [{"name": "no_hints", "namespace": "basic", "file": "b.hpp"}],
            },
            "divide": {
                "policy": "Policy: split inputs into three levels.",
                "output": {"json_path": "../gen/schema_levels.json"},
                "llm": {"model": "openai/gpt-5.3-codex", "temperature": 1, "max_tokens": 12000},
                "cache": {"enabled": True, "dir": ".schema_level_cache"},
            },
            "hppgen": {
                "levels_path": "../gen/schema_levels.json",
                "active_levels": ["no_hints"],
                "model": {"name": "openai/gpt-5.3-codex", "temperature": 1},
                "trace_path": "output/trace.jsonl",
                "trace_stdout": True,
                "use_cache": True,
                "refresh_cache": False,
                "cache_dir": ".cache/storage_layout_hpp",
                "enable_wandb": True,
                "enable_weave": True,
            },
        }
        assert is_legacy(raw) is True
        cfg = LoadedConfig(raw)

        assert cfg.config.common.inputs["schema"] == Path("../input/schema.txt")
        assert cfg.config.common.inputs["storage_plan"] == Path("../input/storage_plan.txt")
        assert [lvl.name for lvl in cfg.config.common.levels] == ["no_hints"]

        divide = cfg.resolve_stage("divide")
        assert divide.model.name == "openai/gpt-5.3-codex"
        assert divide.model.max_tokens == 12000
        assert divide.cache.dir.name == ".schema_level_cache"
        # The inline policy string is preserved, not discarded.
        assert "split inputs into three levels" in divide.params["policy"]
        assert divide.outputs["schema_levels"].name == "schema_levels.json"

        hppgen = cfg.resolve_stage("hppgen")
        assert hppgen.active_levels == ["no_hints"]
        assert hppgen.cache.dir.name == "storage_layout_hpp"
        assert hppgen.trace.stdout is True
        assert hppgen.trace.enable_weave is True
        assert hppgen.trace.enable_wandb is True

    def test_flat_workflow_shape(self):
        raw = {
            "model": "openai/gpt-5.4-mini",
            "compiler": "g++",
            "cpp_standard": "c++20",
            "max_fix_rounds": 2,
            "trace_path": "output/trace.jsonl",
            "trace_stdout": True,
            "use_cache": True,
            "cache_dir": ".cache/storage_layout_hpp",
            "gen_project_root": "/home/mk/gen4/",
            "base_dir": "/home/mk/v4/",
            "schema": "./input/schema.txt",
            "queries_file": "./input/allqueries.txt",
            "build_dir": "/home/mk/gen4/build",
            "task": "Generate a C++ program that runs each query.",
            "gold_command": "uv run ./reference/run_query.py --output {gold_output}",
            "gold_output_dir": "./gold/",
            "output_extension": ".csv",
            "input_dir": "./dataset/sf0.25/",
            "planner_models": ["openai/gpt-5.4-mini"],
            "patcher_models": ["openai/gpt-5.4-mini"],
            "enable_weave": True,
            "weave_project_name": "cpp-codegen",
        }
        cfg = LoadedConfig(raw)
        assert cfg.base_dir == Path("/home/mk/v4")
        assert cfg.config.common.inputs["queries"] == Path("./input/allqueries.txt")
        assert cfg.config.common.gold.mode == "command"
        assert cfg.config.common.gold.extension == ".csv"

        plan = cfg.resolve_stage("storage_plan")
        assert plan.model.name == "openai/gpt-5.4-mini"
        assert plan.compile.cpp_standard == "c++20"
        assert plan.compile.max_fix_rounds == 2
        assert plan.trace.enable_weave is True
        assert plan.trace.weave_project_name == "cpp-codegen"
        # base_dir is absolute, so gen_project_root resolves independently.
        assert plan.gen_project_root == Path("/home/mk/gen4")

        codegen = cfg.resolve_stage("query_codegen")
        assert "Generate a C++ program" in codegen.params["task"]

    def test_legacy_without_any_model_is_rejected_clearly(self):
        with pytest.raises(ConfigError, match="Could not determine a model"):
            LoadedConfig({"schema": "x.txt"})

    def test_all_three_agent_md_configs_load(self):
        """Parse the JSON blobs out of agent.md itself and load each one."""
        agent_md = Path(__file__).resolve().parents[2] / "agent.md"
        if not agent_md.exists():
            pytest.skip("agent.md not present")
        blobs = _extract_json_objects(agent_md.read_text(encoding="utf-8"))
        assert len(blobs) >= 3, f"expected at least 3 configs, found {len(blobs)}"
        for index, raw in enumerate(blobs):
            cfg = LoadedConfig(raw)
            assert cfg.config.common.model.name, f"config {index} has no model"
            for name in cfg.stage_names():
                cfg.resolve_stage(name)


def _extract_json_objects(text: str) -> list[dict]:
    """Pull top-level JSON objects out of a markdown document."""
    out: list[dict] = []
    depth = 0
    start = None
    for index, char in enumerate(text):
        if char == "{":
            if depth == 0:
                start = index
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0 and start is not None:
                chunk = text[start : index + 1]
                if len(chunk) > 200:
                    try:
                        out.append(json.loads(chunk))
                    except json.JSONDecodeError:
                        pass
                start = None
    return out
