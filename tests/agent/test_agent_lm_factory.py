"""Regression coverage for ``configure_dspy``'s callback wiring.

``configure_dspy`` used to pass the same callback list into both
``dspy.settings.callbacks`` (global) and the LM instance's own ``callbacks``
(``dspy.LM(..., callbacks=...)``). DSPy's dispatch additively combines the
two, so every LM call fired each hook twice on the same object, silently
corrupting the JSONL trace's span nesting. These tests exercise the real
``configure_dspy`` / ``build_lm`` path (no monkeypatching of ``configure_dspy``
itself) to make sure a callback is only ever registered once.
"""

import dspy
import pytest

from agent.config.models import ModelConfig
from agent.llm.lm_factory import build_lm, configure_dspy


@pytest.fixture(autouse=True)
def reset_dspy_callbacks():
    """Clear DSPy's global callback list before and after each test.

    ``dspy.configure`` merges into existing global settings rather than
    replacing them, so a callback registered by one test would otherwise leak
    into the next.
    """
    dspy.settings.configure(callbacks=[])
    yield
    dspy.settings.configure(callbacks=[])


def _active_callback_count(lm, callback) -> int:
    """How many times ``callback`` would fire per call, per DSPy's own dispatch.

    Mirrors ``dspy.utils.callback._get_active_callbacks``: global settings
    callbacks plus the instance's own, added together.
    """
    global_callbacks = list(dspy.settings.get("callbacks", []) or [])
    instance_callbacks = list(getattr(lm, "callbacks", None) or [])
    return global_callbacks.count(callback) + instance_callbacks.count(callback)


class TestConfigureDspyCallbackWiring:
    def test_callback_is_registered_exactly_once(self):
        cfg = ModelConfig(name="ollama_chat/llama3", api_key_env="")
        callback = dspy.utils.callback.BaseCallback()

        lm = configure_dspy(cfg, callbacks=[callback], require_key=False)

        assert _active_callback_count(lm, callback) == 1

    def test_no_callbacks_means_none_registered(self):
        cfg = ModelConfig(name="ollama_chat/llama3", api_key_env="")

        lm = configure_dspy(cfg, require_key=False)

        assert list(dspy.settings.get("callbacks", []) or []) == []
        assert list(getattr(lm, "callbacks", None) or []) == []

    def test_build_lm_does_not_attach_callbacks_itself(self):
        cfg = ModelConfig(name="ollama_chat/llama3", api_key_env="")

        lm = build_lm(cfg, require_key=False)

        assert list(getattr(lm, "callbacks", None) or []) == []
