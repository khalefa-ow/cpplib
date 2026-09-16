"""LLM construction and caching."""

from agent.llm.cache import CacheEntry, CacheKey, CacheStats, DiskCache, canonical_hash
from agent.llm.lm_factory import (
    PROVIDER_DEFAULTS,
    build_lm,
    configure_dspy,
    describe_model,
    provider_of,
    resolve_api_base,
    resolve_api_key_env,
    validate_model_config,
)

__all__ = [
    "PROVIDER_DEFAULTS",
    "CacheEntry",
    "CacheKey",
    "CacheStats",
    "DiskCache",
    "build_lm",
    "canonical_hash",
    "configure_dspy",
    "describe_model",
    "provider_of",
    "resolve_api_base",
    "resolve_api_key_env",
    "validate_model_config",
]
