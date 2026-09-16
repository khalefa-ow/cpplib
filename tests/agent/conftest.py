"""Fixtures for the agent package tests.

Everything here runs without an API key: the LLM is DSPy's DummyLM, and the
C++ workspace is a real on-disk tree so the cpplib integration is exercised
for real rather than mocked.
"""

import json
import shutil
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
PROMPTS_DIR = REPO_ROOT / "prompts"


@pytest.fixture
def prompts_dir(tmp_path):
    """A copy of the real prompts directory, safe to mutate."""
    target = tmp_path / "prompts"
    shutil.copytree(PROMPTS_DIR, target)
    return target


@pytest.fixture
def manifest_path(tmp_path, prompts_dir):
    """A freshly built manifest over the copied prompts."""
    from agent.prompting.build_manifest import build_manifest

    path = tmp_path / "manifest.json"
    build_manifest(prompts_dir, path, strict=True)
    return path


@pytest.fixture
def registry(manifest_path):
    from agent.prompting.registry import PromptRegistry

    return PromptRegistry.from_manifest(manifest_path)


@pytest.fixture
def cpp_tree(tmp_path):
    """A small C++ project, including a build/ dir that must not be indexed."""
    root = tmp_path / "project"
    src = root / "src"
    src.mkdir(parents=True)
    (src / "engine.cpp").write_text(
        "#include <vector>\n"
        "#include <string>\n"
        "\n"
        "namespace queries {\n"
        "\n"
        "int add(int a, int b) {\n"
        "    return a + b;\n"
        "}\n"
        "\n"
        "// a decoy mentioning add( inside a comment\n"
        "int quickadd(int a) {\n"
        "    return add(a, 1);\n"
        "}\n"
        "\n"
        "class Table {\n"
        "public:\n"
        "    std::vector<int> ids;\n"
        "    std::string name;\n"
        "};\n"
        "\n"
        "}  // namespace queries\n",
        encoding="utf-8",
    )
    build = root / "build"
    build.mkdir()
    (build / "garbage.cpp").write_text("!!! not valid c++ !!!\n", encoding="utf-8")
    return root


@pytest.fixture
def workspace(cpp_tree):
    from agent.config.models import CompileConfig
    from agent.rlm.workspace import CppWorkspace

    return CppWorkspace(cpp_tree, compile_config=CompileConfig(cpp_standard="c++20"))


@pytest.fixture
def project_inputs(tmp_path):
    """Minimal schema/queries/statistics inputs."""
    directory = tmp_path / "input"
    directory.mkdir()
    (directory / "schema.txt").write_text(
        "CREATE TABLE t (id INTEGER PRIMARY KEY, name VARCHAR(20));\n", encoding="utf-8"
    )
    (directory / "queries.txt").write_text("SELECT name FROM t WHERE id = 1;\n", encoding="utf-8")
    (directory / "stats.txt").write_text("t: 1000 rows\n", encoding="utf-8")
    return directory


@pytest.fixture
def config_dict(tmp_path, project_inputs):
    """A canonical-schema config pointing at the temp inputs."""
    return {
        "common": {
            "base_dir": str(tmp_path),
            "inputs": {
                "schema": str(project_inputs / "schema.txt"),
                "queries": str(project_inputs / "queries.txt"),
                "statistics": str(project_inputs / "stats.txt"),
            },
            "artifacts_dir": "artifacts",
            "gen_project_root": "gen",
            "levels": [
                {"name": "no_hints", "namespace": "basic", "file": "basic.hpp"},
                {"name": "all_hints", "namespace": "full", "file": "full.hpp"},
            ],
            "model": {"name": "openai/gpt-4o-mini", "temperature": 0.0, "api_key_env": ""},
            "cache": {"dir": ".cache"},
            "trace": {"path": "trace.jsonl"},
        },
        "stages": {
            "storage_plan": {"prompt_ids": ["storage_plan_policy"]},
            "divide": {"prompt_ids": ["divide_policy"]},
        },
    }


@pytest.fixture
def config_file(tmp_path, config_dict):
    path = tmp_path / "config.json"
    path.write_text(json.dumps(config_dict, indent=2), encoding="utf-8")
    return path


@pytest.fixture
def loaded_config(config_file):
    from agent.config.loader import LoadedConfig

    return LoadedConfig.from_file(config_file)
