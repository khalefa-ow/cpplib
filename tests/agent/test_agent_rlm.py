"""CppRLM construction, preflight and predictor routing.

Construction is tested without any API key: building the module and its tool
list does not call a provider. The one test that actually drives the RLM sandbox
is gated behind an explicit env var, because it costs tokens.
"""

import os
import shutil

import pytest

from agent.config.models import ModelConfig, RLMConfig
from agent.errors import DependencyMissingError

dspy = pytest.importorskip("dspy")

needs_deno = pytest.mark.skipif(shutil.which("deno") is None, reason="deno not installed")


class TestPreflight:
    def test_passes_when_prerequisites_are_present(self):
        from agent.rlm.cpp_module import deno_available, preflight

        if deno_available():
            preflight()
        else:
            with pytest.raises(DependencyMissingError):
                preflight()

    def test_missing_deno_names_the_fix(self, monkeypatch):
        """DSPy's own error for this surfaces as an opaque protocol failure."""
        from agent.rlm import cpp_module

        monkeypatch.setattr(cpp_module.shutil, "which", lambda name: None)
        with pytest.raises(DependencyMissingError, match="deno.land"):
            cpp_module.preflight(require_deno=True)

    def test_deno_check_can_be_skipped(self, monkeypatch):
        from agent.rlm import cpp_module

        monkeypatch.setattr(cpp_module.shutil, "which", lambda name: None)
        cpp_module.preflight(require_deno=False)


@needs_deno
class TestCppRLMConstruction:
    def test_builds_with_the_workspace_tool_set(self, workspace):
        from agent.rlm.cpp_module import CppRLM
        from agent.rlm.cpp_tools import tool_names

        module = CppRLM(
            "db_schema, question -> answer",
            workspace=workspace,
            rlm_config=RLMConfig(max_iterations=3, max_llm_calls=5),
        )
        names = tool_names(module.tools)
        assert "read_function" in names
        assert "compile_file" in names
        assert isinstance(module, dspy.Module)

    def test_budget_reaches_the_underlying_rlm(self, workspace):
        """DSPy 3.3 names the parameter max_iters, not max_iterations."""
        from agent.rlm.cpp_module import CppRLM

        module = CppRLM(
            "db_schema -> answer",
            workspace=workspace,
            rlm_config=RLMConfig(max_iterations=7, max_llm_calls=11, max_output_chars=1234),
        )
        assert module.rlm.max_iters == 7
        assert module.rlm.max_llm_calls == 11
        assert module.rlm.max_output_chars == 1234

    def test_read_only_mode_exposes_no_mutation_tools(self, workspace):
        from agent.rlm.cpp_module import CppRLM
        from agent.rlm.cpp_tools import tool_names

        module = CppRLM(
            "db_schema -> answer",
            workspace=workspace,
            rlm_config=RLMConfig(),
            allow_writes=False,
            allow_build=False,
        )
        names = tool_names(module.tools)
        assert "write_file" not in names
        assert "apply_patch" not in names
        assert "read_function" in names

    def test_works_without_a_workspace(self):
        """A reasoning-only stage gets no code tools at all."""
        from agent.rlm.cpp_module import CppRLM

        module = CppRLM("db_schema -> answer", workspace=None, rlm_config=RLMConfig())
        assert module.tools == []

    def test_extra_tools_are_appended(self, workspace):
        from agent.rlm.cpp_module import CppRLM
        from agent.rlm.cpp_tools import tool_names

        def lookup_stat(name: str) -> str:
            """Return a dataset statistic by name."""
            return f"{name}=1"

        module = CppRLM(
            "db_schema -> answer",
            workspace=workspace,
            rlm_config=RLMConfig(),
            extra_tools=[lookup_stat],
        )
        assert "lookup_stat" in tool_names(module.tools)

    def test_describe_summarizes_budget_and_tools(self, workspace):
        from agent.rlm.cpp_module import CppRLM

        text = CppRLM(
            "db_schema -> answer", workspace=workspace, rlm_config=RLMConfig(max_iterations=4)
        ).describe()
        assert "max_iters=4" in text
        assert "read_function" in text


class TestPredictorRouting:
    def test_small_payload_uses_chain_of_thought(self):
        from agent.rlm.cpp_module import build_predictor

        module, reason = build_predictor(
            "db_schema -> answer",
            payload_chars=1_000,
            rlm_config=RLMConfig(threshold_chars=100_000),
        )
        assert isinstance(module, dspy.ChainOfThought)
        assert "ChainOfThought" in reason

    @needs_deno
    def test_large_payload_uses_rlm(self):
        from agent.rlm.cpp_module import CppRLM, build_predictor

        module, reason = build_predictor(
            "db_schema -> answer",
            payload_chars=200_000,
            rlm_config=RLMConfig(threshold_chars=100_000),
        )
        assert isinstance(module, CppRLM)
        assert "RLM" in reason and "200000" in reason

    @needs_deno
    def test_a_workspace_forces_rlm_regardless_of_size(self, workspace):
        """Code tools are only reachable through the RLM sandbox."""
        from agent.rlm.cpp_module import CppRLM, build_predictor

        module, reason = build_predictor(
            "db_schema -> answer",
            payload_chars=10,
            rlm_config=RLMConfig(threshold_chars=100_000),
            workspace=workspace,
        )
        assert isinstance(module, CppRLM)
        assert "workspace" in reason

    def test_predict_instead_of_chain_of_thought(self):
        from agent.rlm.cpp_module import build_predictor

        module, _ = build_predictor(
            "db_schema -> answer",
            payload_chars=10,
            rlm_config=RLMConfig(),
            use_chain_of_thought=False,
        )
        assert isinstance(module, dspy.Predict)
        assert not isinstance(module, dspy.ChainOfThought)


class TestSubLm:
    def test_none_when_unconfigured(self):
        from agent.rlm.cpp_module import build_sub_lm

        assert build_sub_lm(None) is None

    def test_built_from_config(self):
        from agent.rlm.cpp_module import build_sub_lm

        lm = build_sub_lm(ModelConfig(name="openai/gpt-4o-mini", api_key_env=""), require_key=False)
        assert lm is not None
        assert lm.model == "openai/gpt-4o-mini"


@pytest.mark.skipif(
    not os.environ.get("AGENT_LIVE_RLM"),
    reason="set AGENT_LIVE_RLM=1 and a working API key to run the live RLM smoke test",
)
@needs_deno
class TestLiveRLM:
    def test_rlm_can_read_code_through_the_tools(self, workspace):
        """End-to-end through the real sandbox. Costs tokens; opt-in only."""
        from agent.llm.lm_factory import configure_dspy
        from agent.rlm.cpp_module import CppRLM

        configure_dspy(ModelConfig(name="openai/gpt-4o-mini", temperature=0.0))
        module = CppRLM(
            "question -> answer",
            workspace=workspace,
            rlm_config=RLMConfig(max_iterations=6, max_llm_calls=12),
            allow_writes=False,
        )
        result = module(question="What does the function named add return? Use the tools.")
        assert "a + b" in str(result.answer).lower() or "sum" in str(result.answer).lower()
