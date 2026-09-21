"""Pydantic models for the agent workflow configuration.

One canonical schema. The three example configs pasted into ``agent.md`` are
mapped onto it by :func:`agent.config.loader.normalize_legacy`.

Nothing here is specific to a particular C++ project: all paths come from the
config and are resolved against ``common.base_dir``, so the same package can be
pointed at any schema/queries/dataset layout.
"""

from pathlib import Path
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class _Base(BaseModel):
    """Shared model settings: reject unknown keys so typos fail loudly."""

    model_config = ConfigDict(extra="forbid")


class ModelConfig(_Base):
    """A language model to call, expressed as a LiteLLM provider string.

    ``name`` uses the ``provider/model`` form DSPy passes through to LiteLLM,
    e.g. ``openai/gpt-5.3-codex`` or ``deepseek/deepseek-chat``.
    """

    name: str
    model_type: Literal["chat", "text", "responses"] = "chat"
    temperature: Optional[float] = 1.0
    max_tokens: Optional[int] = None
    num_retries: int = 3
    # Left unset, the provider prefix picks the conventional variable
    # (see PROVIDER_DEFAULTS in agent.llm.lm_factory).
    api_key_env: Optional[str] = None
    api_base: Optional[str] = None
    adapter: Optional[Literal["chat", "json"]] = None
    track_usage: bool = True
    # DSPy's own inner LM cache. Independent of our DiskCache, which memoizes
    # whole module invocations rather than individual completions.
    lm_cache: bool = True
    # Passed straight through to dspy.LM(**kwargs) for provider-specific knobs.
    extra: dict[str, Any] = Field(default_factory=dict)

    def signature(self) -> str:
        """A stable identity for cache keying.

        Any change here must invalidate cached completions, so every field that
        can alter the model's output is included and nothing else is.
        """
        return "|".join(
            [
                self.name,
                self.model_type,
                f"temp={self.temperature}",
                f"max_tokens={self.max_tokens}",
                f"adapter={self.adapter}",
                f"api_base={self.api_base}",
                f"extra={sorted(self.extra.items())}",
            ]
        )


class CacheConfig(_Base):
    """Content-addressed disk cache for LLM invocations."""

    enabled: bool = True
    dir: Path = Path(".cache")
    # Recompute even on a hit, then overwrite. Use to refresh a stale answer
    # without throwing the whole cache away.
    refresh: bool = False


class TraceConfig(_Base):
    """JSONL tracing plus optional third-party observability backends."""

    path: Optional[Path] = None
    stdout: bool = False
    # Long prompts and whole C++ files would otherwise dominate the trace file.
    max_field_chars: int = 4000
    enable_weave: bool = False
    weave_project_name: Optional[str] = None
    enable_wandb: bool = False
    wandb_project: Optional[str] = None


class RLMConfig(_Base):
    """Budget and routing for ``dspy.RLM``.

    A single RLM call can issue dozens of sub-LM calls, so the caps here are
    cost controls, not just safety limits.
    """

    # NOTE: forwarded to dspy.RLM as `max_iters` (the DSPy 3.3 kwarg name).
    max_iterations: int = 20
    max_llm_calls: int = 50
    max_output_chars: int = 100_000
    verbose: bool = False
    # Cheaper model for the RLM's internal recursive calls. Falls back to the
    # stage's main model when unset.
    sub_model: Optional[ModelConfig] = None
    # Below this combined input size, RLM's REPL overhead is not worth it and
    # the stage uses a plain predictor instead.
    threshold_chars: int = 100_000
    # When true, a successful write_file/replace_function/apply_patch/delete_file
    # tool call triggers a full build_project() automatically, so the model gets
    # build feedback without having to remember to compile. Off by default so it
    # never doubles up with a stage's own explicit compile loop (query_codegen).
    auto_build_after_write: bool = False


class CompileConfig(_Base):
    """How to compile and build generated C++."""

    compiler: str = "g++"
    cpp_standard: str = "c++20"
    include_dirs: list[Path] = Field(default_factory=list)
    extra_flags: list[str] = Field(default_factory=list)
    cmake_dir: Optional[Path] = None
    build_dir: Optional[Path] = None
    timeout_s: int = 300
    max_fix_rounds: int = 2
    # cpplib's CMakeValidator does `rm -rf <cmake_dir>/build` when cleaning, so
    # this stays opt-in.
    clean_build: bool = False


class LevelConfig(_Base):
    """One hint level: how much of the storage plan reaches the generator."""

    name: str
    namespace: str
    file: str


class GoldConfig(_Base):
    """Where reference ("gold") query results come from."""

    mode: Literal["duckdb", "command", "none"] = "none"
    duckdb_path: Optional[Path] = None
    # Shell template for mode="command", e.g.
    # "uv run ./reference/run_query.py --query-text {query_text} --output {gold_output}"
    command: Optional[str] = None
    dir: Optional[Path] = None
    extension: str = ".csv"
    dataset_dir: Optional[Path] = None
    overwrite: bool = False
    install_spatial: bool = False


class CommonConfig(_Base):
    """Defaults shared by every stage, plus the project's paths."""

    base_dir: Path = Path(".")
    # Arbitrary named inputs. The built-in stages look for "schema", "queries"
    # and optionally "statistics", but any key may be added and referenced from
    # a stage's prompt_vars.
    inputs: dict[str, Path] = Field(default_factory=dict)
    artifacts_dir: Path = Path("artifacts")
    gen_project_root: Optional[Path] = None
    out_dir: Optional[Path] = None
    levels: list[LevelConfig] = Field(default_factory=list)

    model: ModelConfig
    cache: CacheConfig = Field(default_factory=CacheConfig)
    trace: TraceConfig = Field(default_factory=TraceConfig)
    rlm: RLMConfig = Field(default_factory=RLMConfig)
    compile: CompileConfig = Field(default_factory=CompileConfig)
    gold: GoldConfig = Field(default_factory=GoldConfig)


class StageConfig(_Base):
    """Per-stage settings. Omitted blocks inherit from ``common``.

    Inheritance is field-level and happens on the raw dict before validation
    (see :func:`agent.config.loader.deep_merge`), so overriding one field of a
    block keeps the rest of that block's values from ``common``.
    """

    enabled: bool = True
    prompt_ids: list[str] = Field(default_factory=list)
    prompt_vars: dict[str, Any] = Field(default_factory=dict)
    active_levels: list[str] = Field(default_factory=list)

    model: Optional[ModelConfig] = None
    cache: Optional[CacheConfig] = None
    trace: Optional[TraceConfig] = None
    rlm: Optional[RLMConfig] = None
    compile: Optional[CompileConfig] = None
    gold: Optional[GoldConfig] = None

    params: dict[str, Any] = Field(default_factory=dict)
    outputs: dict[str, Path] = Field(default_factory=dict)


class AgentConfig(_Base):
    """The whole configuration document."""

    common: CommonConfig
    stages: dict[str, StageConfig] = Field(default_factory=dict)


class ResolvedStageConfig(_Base):
    """A stage's fully merged, path-resolved configuration.

    Produced by :meth:`agent.config.loader.LoadedConfig.resolve_stage`. Every
    path here is absolute; every inherited block is complete.
    """

    name: str
    enabled: bool = True
    base_dir: Path
    inputs: dict[str, Path] = Field(default_factory=dict)
    artifacts_dir: Path
    gen_project_root: Optional[Path] = None
    out_dir: Optional[Path] = None
    levels: list[LevelConfig] = Field(default_factory=list)
    active_levels: list[str] = Field(default_factory=list)
    prompt_ids: list[str] = Field(default_factory=list)
    prompt_vars: dict[str, Any] = Field(default_factory=dict)
    params: dict[str, Any] = Field(default_factory=dict)
    outputs: dict[str, Path] = Field(default_factory=dict)

    model: ModelConfig
    cache: CacheConfig
    trace: TraceConfig
    rlm: RLMConfig
    compile: CompileConfig
    gold: GoldConfig

    def input_path(self, key: str, required: bool = True) -> Optional[Path]:
        """Return a configured input path, verifying it exists on disk.

        Raises :class:`MissingInputError` when ``required`` and the key is
        absent or the file is missing, so a mis-pointed config fails before any
        tokens are spent.
        """
        from agent.errors import MissingInputError

        path = self.inputs.get(key)
        if path is None:
            if required:
                raise MissingInputError(key)
            return None
        if not path.exists():
            if required:
                raise MissingInputError(key, path, reason="missing on disk")
            return None
        return path

    def level(self, name: str) -> LevelConfig:
        """Look up one declared hint level by name."""
        for level in self.levels:
            if level.name == name:
                return level
        from agent.errors import ConfigError

        declared = ", ".join(level.name for level in self.levels) or "<none>"
        raise ConfigError(
            f"Level '{name}' is not declared in common.levels. Declared levels: {declared}."
        )

    def resolved_active_levels(self) -> list[LevelConfig]:
        """The levels this stage should act on.

        An empty ``active_levels`` means every declared level, which matches how
        the example configs treat an omitted list.
        """
        if not self.active_levels:
            return list(self.levels)
        return [self.level(name) for name in self.active_levels]

    def sub_model(self) -> ModelConfig:
        """The model for RLM inner calls, defaulting to the stage's main model."""
        return self.rlm.sub_model or self.model
