"""Load, normalize, merge and path-resolve the agent configuration.

Two things happen here that are worth knowing about:

1. **Field-level inheritance.** Stage overrides are merged into ``common`` on the
   *raw dicts*, before pydantic validation. Merging after validation would mean a
   stage that sets only ``cache.refresh`` silently discards ``common``'s
   ``cache.dir``, because the stage's ``CacheConfig`` would already have been
   filled with class defaults.

2. **Legacy normalization.** ``agent.md`` contains three differently-shaped
   configs. :func:`normalize_legacy` maps all of them onto the canonical schema
   so existing files keep working.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Iterable, Mapping

from pydantic import BaseModel

from agent.config.models import (
    AgentConfig,
    CacheConfig,
    CompileConfig,
    GoldConfig,
    ModelConfig,
    ResolvedStageConfig,
    RLMConfig,
    TraceConfig,
)
from agent.errors import ConfigError

# Blocks that a stage inherits from `common` field by field.
_INHERITED_BLOCKS = ("model", "cache", "trace", "rlm", "compile", "gold")

_BLOCK_MODELS: dict[str, type[BaseModel]] = {
    "model": ModelConfig,
    "cache": CacheConfig,
    "trace": TraceConfig,
    "rlm": RLMConfig,
    "compile": CompileConfig,
    "gold": GoldConfig,
}


def deep_merge(base: Mapping[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
    """Recursively merge ``override`` onto ``base``, returning a new dict.

    Nested mappings are merged; every other value (including lists) is replaced
    wholesale. Lists are replaced rather than concatenated because a stage
    overriding ``extra_flags`` or ``active_levels`` means "use these", not "add
    these".
    """
    result = dict(copy.deepcopy(dict(base)))
    for key, value in override.items():
        existing = result.get(key)
        if isinstance(existing, Mapping) and isinstance(value, Mapping):
            result[key] = deep_merge(existing, value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def _resolve_path(base_dir: Path, value: Any) -> Path:
    """Resolve one path against ``base_dir``, leaving absolute paths alone."""
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = base_dir / path
    # normpath-style cleanup without requiring the path to exist
    return Path(path).resolve()


class LoadedConfig:
    """A parsed configuration document plus its raw form.

    The raw dict is retained because stage resolution merges raw values (see the
    module docstring).
    """

    def __init__(self, raw: Mapping[str, Any], path: Path | None = None):
        self.path = Path(path).resolve() if path is not None else None
        self.raw: dict[str, Any] = normalize_legacy(raw)
        try:
            self.config = AgentConfig.model_validate(self.raw)
        except Exception as exc:  # pydantic ValidationError
            where = f" in {self.path}" if self.path else ""
            raise ConfigError(f"Invalid configuration{where}:\n{exc}") from exc

        # base_dir itself is resolved against the config file's directory, so a
        # relative base_dir means "relative to the config", which is what makes
        # a config portable between machines.
        anchor = self.path.parent if self.path else Path.cwd()
        self.base_dir = _resolve_path(anchor, self.config.common.base_dir)

    @classmethod
    def from_file(cls, path: str | Path) -> "LoadedConfig":
        """Load a JSON configuration document from disk."""
        config_path = Path(path).expanduser().resolve()
        if not config_path.exists():
            raise ConfigError(f"Config file not found: {config_path}")
        try:
            raw = json.loads(config_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ConfigError(f"Config file {config_path} is not valid JSON: {exc}") from exc
        if not isinstance(raw, dict):
            raise ConfigError(f"Config file {config_path} must contain a JSON object.")
        return cls(raw, path=config_path)

    def stage_names(self) -> list[str]:
        """Stage names declared in the config, in document order."""
        return list(self.config.stages.keys())

    def resolve_stage(self, name: str) -> ResolvedStageConfig:
        """Merge ``common`` with one stage's overrides and resolve every path."""
        raw_common = dict(self.raw.get("common") or {})
        raw_stage = dict((self.raw.get("stages") or {}).get(name) or {})

        merged_blocks: dict[str, Any] = {}
        for block in _INHERITED_BLOCKS:
            base = raw_common.get(block) or {}
            override = raw_stage.get(block) or {}
            merged = deep_merge(base, override)
            if not merged and block != "model":
                merged_blocks[block] = _BLOCK_MODELS[block]()
                continue
            try:
                merged_blocks[block] = _BLOCK_MODELS[block].model_validate(merged)
            except Exception as exc:
                raise ConfigError(
                    f"Invalid '{block}' block for stage '{name}' after merging with common:\n{exc}"
                ) from exc

        common = self.config.common
        stage = self.config.stages.get(name)

        resolved = ResolvedStageConfig(
            name=name,
            enabled=stage.enabled if stage else True,
            base_dir=self.base_dir,
            inputs={k: _resolve_path(self.base_dir, v) for k, v in common.inputs.items()},
            artifacts_dir=_resolve_path(self.base_dir, common.artifacts_dir),
            gen_project_root=(
                _resolve_path(self.base_dir, common.gen_project_root)
                if common.gen_project_root
                else None
            ),
            out_dir=_resolve_path(self.base_dir, common.out_dir) if common.out_dir else None,
            levels=list(common.levels),
            active_levels=list(stage.active_levels) if stage else [],
            prompt_ids=list(stage.prompt_ids) if stage else [],
            prompt_vars=dict(stage.prompt_vars) if stage else {},
            params=dict(stage.params) if stage else {},
            outputs=(
                {k: _resolve_path(self.base_dir, v) for k, v in stage.outputs.items()}
                if stage
                else {}
            ),
            **merged_blocks,
        )
        return self._resolve_block_paths(resolved)

    def _resolve_block_paths(self, cfg: ResolvedStageConfig) -> ResolvedStageConfig:
        """Resolve the paths nested inside the inherited blocks.

        Done after construction so that path resolution lives in exactly one
        place regardless of which block a path came from.
        """
        base = cfg.base_dir

        cfg.cache.dir = _resolve_path(base, cfg.cache.dir)
        if cfg.trace.path is not None:
            cfg.trace.path = _resolve_path(base, cfg.trace.path)

        cfg.compile.include_dirs = [_resolve_path(base, p) for p in cfg.compile.include_dirs]
        if cfg.compile.cmake_dir is not None:
            cfg.compile.cmake_dir = _resolve_path(base, cfg.compile.cmake_dir)
        if cfg.compile.build_dir is not None:
            cfg.compile.build_dir = _resolve_path(base, cfg.compile.build_dir)

        for field in ("duckdb_path", "dir", "dataset_dir"):
            value = getattr(cfg.gold, field)
            if value is not None:
                setattr(cfg.gold, field, _resolve_path(base, value))

        return cfg


# --------------------------------------------------------------------------
# Legacy config normalization
# --------------------------------------------------------------------------

# Flat keys in the third agent.md config that name project inputs.
_LEGACY_INPUT_KEYS = {
    "schema": "schema",
    "schema_path": "schema",
    "storage_plan": "storage_plan",
    "storage_plan_path": "storage_plan",
    "queries_file": "queries",
    "queries_path": "queries",
    "statistics": "statistics",
    "statistics_path": "statistics",
}

# Flat trace/observability keys → TraceConfig fields.
_LEGACY_TRACE_KEYS = {
    "trace_path": "path",
    "trace_stdout": "stdout",
    "enable_weave": "enable_weave",
    "weave_project_name": "weave_project_name",
    "enable_wandb": "enable_wandb",
    "wandb_project": "wandb_project",
}

# Flat cache keys → CacheConfig fields.
_LEGACY_CACHE_KEYS = {
    "use_cache": "enabled",
    "cache_dir": "dir",
    "refresh_cache": "refresh",
}

# Flat compile keys → CompileConfig fields.
_LEGACY_COMPILE_KEYS = {
    "compiler": "compiler",
    "cpp_standard": "cpp_standard",
    "build_dir": "build_dir",
    "max_fix_rounds": "max_fix_rounds",
}

# Flat gold keys → GoldConfig fields.
_LEGACY_GOLD_KEYS = {
    "gold_dir": "dir",
    "gold_output_dir": "dir",
    "gold_extension": "extension",
    "output_extension": "extension",
    "gold_duckdb_path": "duckdb_path",
    "gold_command": "command",
    "gold_overwrite": "overwrite",
    "gold_install_spatial": "install_spatial",
    "dataset_dir": "dataset_dir",
    "input_dir": "dataset_dir",
}

_KNOWN_STAGE_NAMES = {"storage_plan", "divide", "hppgen", "query_codegen", "optimize"}


def _model_from_legacy(value: Any) -> dict[str, Any] | None:
    """Coerce the several shapes agent.md uses for a model into ModelConfig."""
    if value is None:
        return None
    if isinstance(value, str):
        return {"name": value}
    if isinstance(value, list):
        # planner_models / patcher_models are lists; the first entry wins and
        # the rest are ignored (there is no ensembling in this pipeline).
        return _model_from_legacy(value[0]) if value else None
    if isinstance(value, Mapping):
        raw = dict(value)
        out: dict[str, Any] = {}
        if "name" in raw:
            out["name"] = raw.pop("name")
        elif "model" in raw:
            out["name"] = raw.pop("model")
        for key in (
            "model_type",
            "temperature",
            "max_tokens",
            "num_retries",
            "api_key_env",
            "api_base",
            "adapter",
            "track_usage",
        ):
            if key in raw:
                out[key] = raw.pop(key)
        if "lm_cache" in raw:
            out["lm_cache"] = raw.pop("lm_cache")
        if raw:
            out["extra"] = raw
        return out or None
    raise ConfigError(f"Cannot interpret {value!r} as a model specification.")


def _lift(src: Mapping[str, Any], mapping: Mapping[str, str]) -> dict[str, Any]:
    """Pull flat keys out of ``src`` into a nested block dict."""
    return {mapping[k]: src[k] for k in mapping if k in src and src[k] is not None}


def is_legacy(raw: Mapping[str, Any]) -> bool:
    """True when the document is one of the agent.md shapes, not the canonical one.

    The canonical schema always has a ``common`` block containing ``model``. The
    legacy shapes either have no ``common`` at all, or a ``common`` that holds
    only paths and levels.
    """
    if "stages" in raw:
        return False
    common = raw.get("common")
    if isinstance(common, Mapping) and "model" in common:
        return False
    return True


def normalize_legacy(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Map any of the ``agent.md`` config shapes onto the canonical schema.

    Already-canonical documents are returned unchanged (deep-copied). The
    mapping applied to legacy documents:

    ==========================================  ================================
    legacy                                      canonical
    ==========================================  ================================
    ``common.schema_path``                       ``common.inputs.schema``
    ``common.storage_plan_path``                 ``common.inputs.storage_plan``
    ``queries_file`` / ``queries_path``          ``common.inputs.queries``
    ``divide.llm``                               ``stages.divide.model``
    ``divide.policy``                            ``stages.divide.params.policy``
    ``divide.output.json_path``                  ``stages.divide.outputs.schema_levels``
    ``<stage>.use_cache|cache_dir|refresh_cache````stages.<stage>.cache.*``
    ``<stage>.trace_path|trace_stdout``          ``stages.<stage>.trace.*``
    ``<stage>.model`` / ``<stage>.llm``          ``stages.<stage>.model``
    ``planner_models`` (flat)                    ``stages.storage_plan.model``
    ``patcher_models`` (flat)                    ``stages.query_codegen.model``
    ``gold_*`` / ``input_dir`` (flat)            ``common.gold.*``
    ``compiler|cpp_standard|build_dir`` (flat)   ``common.compile.*``
    ``task`` (flat)                              ``common.inputs`` untouched;
                                                 lands in ``stages.query_codegen.params.task``
    ==========================================  ================================
    """
    data: dict[str, Any] = copy.deepcopy(dict(raw))
    if not is_legacy(data):
        return data

    legacy_common = dict(data.get("common") or {})
    common: dict[str, Any] = {}
    stages: dict[str, dict[str, Any]] = {}

    # --- paths and levels -------------------------------------------------
    inputs: dict[str, Any] = {}
    for source in (legacy_common, data):
        for legacy_key, canonical_key in _LEGACY_INPUT_KEYS.items():
            if source.get(legacy_key):
                inputs.setdefault(canonical_key, source[legacy_key])
    if inputs:
        common["inputs"] = inputs

    if legacy_common.get("levels"):
        common["levels"] = legacy_common["levels"]

    for legacy_key, canonical_key in (
        ("base_dir", "base_dir"),
        ("gen_project_root", "gen_project_root"),
        ("out_dir", "out_dir"),
        ("actual_output_dir", "out_dir"),
    ):
        if data.get(legacy_key):
            common.setdefault(canonical_key, data[legacy_key])

    # --- shared blocks lifted from flat keys ------------------------------
    gold = _lift(data, _LEGACY_GOLD_KEYS)
    if gold:
        if "command" in gold:
            gold["mode"] = "command"
        elif data.get("gold_generate_with_duckdb") or "duckdb_path" in gold:
            gold["mode"] = "duckdb"
        common["gold"] = gold

    compile_block = _lift(data, _LEGACY_COMPILE_KEYS)
    if compile_block:
        common["compile"] = compile_block

    trace_block = _lift(data, _LEGACY_TRACE_KEYS)
    if trace_block:
        common["trace"] = trace_block

    cache_block = _lift(data, _LEGACY_CACHE_KEYS)
    if cache_block:
        common["cache"] = cache_block

    # --- the model: required by the canonical schema ----------------------
    flat_model = _model_from_legacy(data.get("model"))
    if flat_model is None:
        flat_model = _model_from_legacy(data.get("planner_models"))
    if flat_model is None:
        # Fall back to the first per-stage model we can find, so a config that
        # only declares models per stage still validates.
        for value in data.values():
            if isinstance(value, Mapping):
                candidate = _model_from_legacy(value.get("model") or value.get("llm"))
                if candidate:
                    flat_model = candidate
                    break
    if flat_model is None:
        raise ConfigError(
            "Could not determine a model from the legacy config. Add a top-level "
            '"model" key, or use the canonical schema with common.model.'
        )
    common["model"] = flat_model

    # --- per-stage sections ----------------------------------------------
    for key, value in data.items():
        if key in ("common", "stages") or not isinstance(value, Mapping):
            continue
        if key not in _KNOWN_STAGE_NAMES and key not in ("divide", "hppgen", "query_codegen"):
            continue
        section = dict(value)
        stage: dict[str, Any] = {}

        model = _model_from_legacy(section.pop("model", None) or section.pop("llm", None))
        if model:
            stage["model"] = model

        cache = section.pop("cache", None)
        cache_fields = _lift(section, _LEGACY_CACHE_KEYS)
        if isinstance(cache, Mapping):
            cache_fields = deep_merge(dict(cache), cache_fields)
        if cache_fields:
            stage["cache"] = cache_fields

        trace_fields = _lift(section, _LEGACY_TRACE_KEYS)
        if trace_fields:
            stage["trace"] = trace_fields

        compile_fields = _lift(section, _LEGACY_COMPILE_KEYS)
        if compile_fields:
            stage["compile"] = compile_fields

        if section.get("active_levels"):
            stage["active_levels"] = section["active_levels"]

        output = section.pop("output", None)
        if isinstance(output, Mapping) and output.get("json_path"):
            stage["outputs"] = {"schema_levels": output["json_path"]}

        # Everything left over is stage-specific and kept verbatim in params so
        # no information from the original config is lost.
        leftovers = {
            k: v
            for k, v in section.items()
            if k
            not in set(_LEGACY_CACHE_KEYS)
            | set(_LEGACY_TRACE_KEYS)
            | set(_LEGACY_COMPILE_KEYS)
            | {"active_levels"}
        }
        if leftovers:
            stage["params"] = leftovers

        stages[key] = stage

    # Flat planner/patcher model lists map onto the stages that use them.
    planner = _model_from_legacy(data.get("planner_models"))
    if planner:
        stages.setdefault("storage_plan", {}).setdefault("model", planner)
    patcher = _model_from_legacy(data.get("patcher_models"))
    if patcher:
        stages.setdefault("query_codegen", {}).setdefault("model", patcher)

    if data.get("task"):
        stages.setdefault("query_codegen", {}).setdefault("params", {})["task"] = data["task"]

    return {"common": common, "stages": stages}


def load_config(path: str | Path) -> LoadedConfig:
    """Convenience wrapper around :meth:`LoadedConfig.from_file`."""
    return LoadedConfig.from_file(path)


def resolve_all(
    cfg: LoadedConfig, names: Iterable[str] | None = None
) -> dict[str, ResolvedStageConfig]:
    """Resolve several stages at once, defaulting to every declared stage."""
    selected = list(names) if names is not None else cfg.stage_names()
    return {name: cfg.resolve_stage(name) for name in selected}
