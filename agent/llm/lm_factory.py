"""Build ``dspy.LM`` instances from config, and configure DSPy globally.

Model strings are LiteLLM ``provider/model`` identifiers, so switching between
OpenAI and DeepSeek is a config edit rather than a code change.

DSPy is imported inside the functions that need it. That keeps ``agent.cli
doctor`` and the config/prompt layers usable when the ``agent`` extra is not
installed, so a missing dependency reports itself instead of crashing on import.
"""

from __future__ import annotations

import os
from typing import Any, Optional, Sequence

from agent.config.models import ModelConfig
from agent.errors import DependencyMissingError, LMError, MissingApiKeyError

# Per-provider conventions, used only to fill fields the config leaves unset.
# provider prefix -> (api key env var, default api_base)
PROVIDER_DEFAULTS: dict[str, tuple[str, Optional[str]]] = {
    "openai": ("OPENAI_API_KEY", None),
    "deepseek": ("DEEPSEEK_API_KEY", "https://api.deepseek.com"),
    "anthropic": ("ANTHROPIC_API_KEY", None),
    "azure": ("AZURE_API_KEY", None),
    "openrouter": ("OPENROUTER_API_KEY", "https://openrouter.ai/api/v1"),
    "together_ai": ("TOGETHERAI_API_KEY", None),
    "gemini": ("GEMINI_API_KEY", None),
    # Local servers need no key.
    "ollama": ("", "http://localhost:11434"),
    "ollama_chat": ("", "http://localhost:11434"),
}

# Models served over an OpenAI-compatible endpoint that is not OpenAI's own.
# Using the `openai/` prefix for these requires an explicit api_base, otherwise
# LiteLLM would dutifully send the request to api.openai.com.
_NON_OPENAI_MODEL_HINTS = ("deepseek", "qwen", "kimi", "glm", "moonshot")


def _require_dspy() -> Any:
    """Import dspy, or raise an error that says how to install it."""
    try:
        import dspy
    except ImportError as exc:  # pragma: no cover - depends on env
        raise DependencyMissingError(
            "The 'dspy' package",
            'Install the agent extra: uv pip install -e ".[dev,agent]"',
        ) from exc
    return dspy


def provider_of(model_name: str) -> str:
    """The LiteLLM provider prefix of a model string, or '' if unprefixed."""
    return model_name.split("/", 1)[0] if "/" in model_name else ""


def resolve_api_key_env(cfg: ModelConfig) -> Optional[str]:
    """Which env var holds this model's key.

    An explicit ``api_key_env`` always wins. Otherwise the provider prefix picks
    the conventional variable. Returns None when the provider needs no key.
    """
    if cfg.api_key_env is not None:
        return cfg.api_key_env or None
    env_var, _ = PROVIDER_DEFAULTS.get(provider_of(cfg.name), ("OPENAI_API_KEY", None))
    return env_var or None


def resolve_api_base(cfg: ModelConfig) -> Optional[str]:
    """The endpoint to call, filling in a provider default when unset."""
    if cfg.api_base:
        return cfg.api_base
    _, api_base = PROVIDER_DEFAULTS.get(provider_of(cfg.name), (None, None))
    return api_base


def validate_model_config(cfg: ModelConfig, require_key: bool = True) -> None:
    """Check a model config is usable before any tokens are spent.

    Catches the two mistakes that otherwise surface as a confusing 401 or as
    requests silently going to the wrong vendor.
    """
    provider = provider_of(cfg.name)
    model_tail = cfg.name.split("/", 1)[-1].lower()

    if provider == "openai" and not cfg.api_base:
        if any(hint in model_tail for hint in _NON_OPENAI_MODEL_HINTS):
            raise LMError(
                f"Model '{cfg.name}' uses the 'openai/' prefix but does not look like an "
                f"OpenAI model. Set model.api_base to the real endpoint, or use the "
                f"native provider prefix (e.g. 'deepseek/{model_tail}')."
            )

    if not require_key:
        return
    env_var = resolve_api_key_env(cfg)
    if env_var and not os.environ.get(env_var):
        raise MissingApiKeyError(env_var, cfg.name)


def build_lm(
    cfg: ModelConfig, callbacks: Optional[Sequence[Any]] = None, require_key: bool = True
) -> Any:
    """Construct a ``dspy.LM`` from a :class:`ModelConfig`.

    ``api_base`` and ``api_key`` are passed through ``dspy.LM``'s ``**kwargs``,
    which is how DSPy 3.3 forwards provider options to LiteLLM. ``adapter`` and
    ``track_usage`` are not LM options — they belong to
    :func:`configure_dspy`.
    """
    dspy = _require_dspy()
    validate_model_config(cfg, require_key=require_key)

    kwargs: dict[str, Any] = dict(cfg.extra)
    api_base = resolve_api_base(cfg)
    if api_base:
        kwargs["api_base"] = api_base
    env_var = resolve_api_key_env(cfg)
    if env_var:
        key = os.environ.get(env_var)
        if key:
            kwargs["api_key"] = key

    return dspy.LM(
        model=cfg.name,
        model_type=cfg.model_type,
        temperature=cfg.temperature,
        max_tokens=cfg.max_tokens,
        cache=cfg.lm_cache,
        num_retries=cfg.num_retries,
        callbacks=list(callbacks) if callbacks else None,
        **kwargs,
    )


def build_adapter(cfg: ModelConfig) -> Optional[Any]:
    """Map ``model.adapter`` onto a DSPy adapter instance."""
    if cfg.adapter is None:
        return None
    dspy = _require_dspy()
    if cfg.adapter == "json":
        return dspy.JSONAdapter()
    return dspy.ChatAdapter()


def configure_dspy(
    cfg: ModelConfig,
    callbacks: Optional[Sequence[Any]] = None,
    require_key: bool = True,
) -> Any:
    """Configure DSPy's global settings for a stage and return the LM.

    The single place that calls ``dspy.configure``. ``callbacks`` are registered
    globally rather than only on the LM so that module-, tool- and
    interpreter-level events are traced too, not just completions.
    """
    dspy = _require_dspy()
    lm = build_lm(cfg, callbacks=callbacks, require_key=require_key)
    settings: dict[str, Any] = {"lm": lm, "track_usage": cfg.track_usage}
    if callbacks:
        settings["callbacks"] = list(callbacks)
    adapter = build_adapter(cfg)
    if adapter is not None:
        settings["adapter"] = adapter
    dspy.configure(**settings)
    return lm


def describe_model(cfg: ModelConfig) -> str:
    """One-line human-readable summary, for ``doctor`` and log output."""
    env_var = resolve_api_key_env(cfg)
    key_state = "no key needed"
    if env_var:
        key_state = f"{env_var}={'set' if os.environ.get(env_var) else 'MISSING'}"
    base = resolve_api_base(cfg) or "provider default"
    return f"{cfg.name} (temp={cfg.temperature}, max_tokens={cfg.max_tokens}, {base}, {key_state})"
