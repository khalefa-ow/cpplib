"""Stage contracts, artifact persistence and the per-run context.

Artifacts are the unit of resumability. Each stage declares the artifact keys it
``requires`` and ``produces``; the pipeline orders stages from that, and skips
one whose outputs already exist with an unchanged input fingerprint. A run that
dies in stage four therefore resumes at stage four rather than re-paying for the
first three.
"""

from __future__ import annotations

import hashlib
import json
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, ClassVar, Mapping, Optional

from pydantic import BaseModel, ConfigDict, Field

from agent.config.loader import LoadedConfig
from agent.config.models import ResolvedStageConfig
from agent.errors import PipelineError, StageNotImplemented
from agent.llm.cache import DiskCache, canonical_hash
from agent.prompting.registry import PromptRegistry
from agent.rlm.workspace import CppWorkspace
from agent.trace.callbacks import TraceWriter


class Artifact(BaseModel):
    """A stage output stored on disk."""

    model_config = ConfigDict(extra="forbid")

    stage: str
    key: str
    path: Path
    content_hash: str
    created_at: float = Field(default_factory=time.time)
    # Carries `input_fingerprint`, token usage, cache status and anything else a
    # later stage or a human needs to judge whether to trust this artifact.
    meta: dict[str, Any] = Field(default_factory=dict)

    def read_text(self) -> str:
        return Path(self.path).read_text(encoding="utf-8")

    def read_json(self) -> Any:
        return json.loads(self.read_text())

    @property
    def input_fingerprint(self) -> Optional[str]:
        value = self.meta.get("input_fingerprint")
        return str(value) if value is not None else None


class ArtifactStore:
    """Writes artifacts under a root directory and indexes them in JSON.

    The index is rewritten on every put so an interrupted run still leaves a
    usable index for the artifacts it did finish.
    """

    INDEX_NAME = "artifacts.json"

    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.index_path = self.root / self.INDEX_NAME
        self.artifacts: dict[str, Artifact] = {}
        self._load_index()

    def _load_index(self) -> None:
        if not self.index_path.exists():
            return
        try:
            raw = json.loads(self.index_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            # A corrupt index must not block a run; artifacts on disk are still
            # there and will be re-registered as stages produce them.
            return
        for key, payload in (raw.get("artifacts") or {}).items():
            try:
                self.artifacts[key] = Artifact.model_validate(payload)
            except Exception:
                continue

    def _save_index(self) -> None:
        payload = {
            "updated_at": time.time(),
            "artifacts": {k: v.model_dump(mode="json") for k, v in self.artifacts.items()},
        }
        tmp = self.index_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        tmp.replace(self.index_path)

    # --- writing ----------------------------------------------------------

    def put_text(
        self,
        stage: str,
        key: str,
        text: str,
        meta: Optional[dict[str, Any]] = None,
        filename: Optional[str] = None,
        target: Optional[Path] = None,
    ) -> Artifact:
        """Store text as an artifact.

        Args:
            target: Write here instead of inside the artifacts root. Used when a
                config names an explicit output path, e.g. a generated ``.hpp``
                that has to land in the C++ project tree.
        """
        path = Path(target) if target else self.root / stage / (filename or f"{key}.txt")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        artifact = Artifact(
            stage=stage,
            key=key,
            path=path,
            content_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
            meta=meta or {},
        )
        self.artifacts[key] = artifact
        self._save_index()
        return artifact

    def put_json(
        self,
        stage: str,
        key: str,
        obj: Any,
        meta: Optional[dict[str, Any]] = None,
        filename: Optional[str] = None,
        target: Optional[Path] = None,
    ) -> Artifact:
        """Store a JSON-serializable object as an artifact."""
        text = json.dumps(obj, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
        return self.put_text(
            stage,
            key,
            text,
            meta=meta,
            filename=filename or f"{key}.json",
            target=target,
        )

    # --- reading ----------------------------------------------------------

    def get(self, key: str) -> Optional[Artifact]:
        """An artifact by key, or None if absent or its file has been deleted."""
        artifact = self.artifacts.get(key)
        if artifact is None:
            return None
        if not Path(artifact.path).exists():
            return None
        return artifact

    def has(self, *keys: str) -> bool:
        """True when every named artifact is present on disk."""
        return all(self.get(key) is not None for key in keys)

    def collect(self, keys: tuple[str, ...]) -> dict[str, Artifact]:
        """Gather the named artifacts, raising if one is missing.

        Called by the pipeline just before a stage runs, so a missing upstream
        output is reported as a pipeline error rather than surfacing as an
        AttributeError inside the stage.
        """
        out: dict[str, Artifact] = {}
        missing: list[str] = []
        for key in keys:
            artifact = self.get(key)
            if artifact is None:
                missing.append(key)
            else:
                out[key] = artifact
        if missing:
            raise PipelineError(
                f"Missing required artifact(s): {', '.join(missing)}. "
                f"Run the stage(s) that produce them first, or remove --stages filtering."
            )
        return out


class StageResult(BaseModel):
    """What a stage reports back to the pipeline."""

    model_config = ConfigDict(extra="forbid")

    stage: str
    status: str = "ok"  # "ok" | "skipped" | "failed" | "disabled"
    artifacts: dict[str, Artifact] = Field(default_factory=dict)
    metrics: dict[str, Any] = Field(default_factory=dict)
    error: Optional[str] = None
    duration_s: float = 0.0

    @property
    def ok(self) -> bool:
        return self.status in ("ok", "skipped", "disabled")

    def summary(self) -> str:
        bits = [f"{self.stage}: {self.status} in {self.duration_s:.1f}s"]
        if self.artifacts:
            bits.append(f"artifacts={', '.join(sorted(self.artifacts))}")
        if self.metrics:
            rendered = ", ".join(f"{k}={v}" for k, v in sorted(self.metrics.items()))
            bits.append(rendered)
        if self.error:
            bits.append(f"error={self.error}")
        return " | ".join(bits)


class RunContext:
    """Everything a stage needs, assembled once per run.

    Holds the config, the prompt registry, the artifact store and the trace
    writer, and lazily builds per-stage caches and the C++ workspace so an
    analysis-only run never constructs a workspace (whose cpplib index is the
    expensive part).
    """

    def __init__(
        self,
        config: LoadedConfig,
        registry: PromptRegistry,
        store: ArtifactStore,
        writer: TraceWriter,
        run_id: str,
        require_api_key: bool = True,
    ):
        self.config = config
        self.registry = registry
        self.store = store
        self.writer = writer
        self.run_id = run_id
        self.require_api_key = require_api_key
        self._caches: dict[str, DiskCache] = {}
        self._workspaces: dict[str, CppWorkspace] = {}

    def stage_config(self, name: str) -> ResolvedStageConfig:
        """The merged, path-resolved config for one stage."""
        return self.config.resolve_stage(name)

    def cache_for(self, cfg: ResolvedStageConfig) -> DiskCache:
        """The disk cache for a stage, created on first use."""
        if cfg.name not in self._caches:
            self._caches[cfg.name] = DiskCache.from_config(cfg.cache, stage=cfg.name)
        return self._caches[cfg.name]

    def workspace_for(self, cfg: ResolvedStageConfig) -> CppWorkspace:
        """The C++ workspace for a stage, created on first use.

        Requires ``common.gen_project_root``: a stage that touches C++ has to be
        told where the project lives.
        """
        if cfg.gen_project_root is None:
            raise PipelineError(
                f"Stage '{cfg.name}' needs a C++ workspace, but common.gen_project_root "
                f"is not set in the config."
            )
        key = str(cfg.gen_project_root)
        if key not in self._workspaces:
            self._workspaces[key] = CppWorkspace(
                root=cfg.gen_project_root,
                cmake_dir=cfg.compile.cmake_dir or cfg.gen_project_root,
                compile_config=cfg.compile,
            )
        return self._workspaces[key]

    def cache_stats(self) -> dict[str, str]:
        """Per-stage cache counters, for the run summary."""
        return {name: cache.stats.summary() for name, cache in self._caches.items()}


class Stage(ABC):
    """Base class for a workflow stage.

    Subclasses declare their artifact contract as class attributes and implement
    :meth:`run`. ``requires``/``produces`` are what the pipeline uses to order
    stages and to decide whether a stage can be skipped, so they must name every
    artifact the stage actually reads and writes.
    """

    name: ClassVar[str] = ""
    requires: ClassVar[tuple[str, ...]] = ()
    produces: ClassVar[tuple[str, ...]] = ()
    description: ClassVar[str] = ""
    # Prompt ids this stage uses by default; a config's `prompt_ids` overrides.
    default_prompt_ids: ClassVar[tuple[str, ...]] = ()

    def __init__(self, ctx: RunContext):
        self.ctx = ctx
        self.cfg = ctx.stage_config(self.name)
        self.cache = ctx.cache_for(self.cfg)
        self.registry = ctx.registry
        self.store = ctx.store
        self.writer = ctx.writer

    @abstractmethod
    def run(self, inputs: Mapping[str, Artifact]) -> StageResult:
        """Do the stage's work.

        Args:
            inputs: The artifacts named in ``requires``, already verified to
                exist.

        Returns:
            A :class:`StageResult` whose ``artifacts`` covers ``produces``.
        """

    # --- helpers for subclasses ------------------------------------------

    def prompt_ids(self) -> tuple[str, ...]:
        """Configured prompt ids, falling back to the stage's defaults."""
        return tuple(self.cfg.prompt_ids) or self.default_prompt_ids

    def input_fingerprint(self, inputs: Mapping[str, Artifact], extra: Any = None) -> str:
        """A hash of everything that should force this stage to re-run.

        Covers upstream artifact content, the prompt files, the model identity
        and the stage's own params. Config knobs that cannot change the output
        (a trace path, a cache directory) are excluded on purpose, so moving a
        log file does not invalidate a day of generation.
        """
        payload = {
            "stage": self.name,
            "upstream": {key: artifact.content_hash for key, artifact in sorted(inputs.items())},
            "prompts": self.registry.fingerprint(*self.prompt_ids()) if self.prompt_ids() else "",
            "model": self.cfg.model.signature(),
            "params": self.cfg.params,
            "active_levels": self.cfg.active_levels,
            "inputs": _hash_input_files(self.cfg.inputs),
            "extra": extra,
        }
        return canonical_hash(payload)

    def not_implemented(self) -> StageNotImplemented:
        """Raise-ready error carrying this stage's documented contract."""
        return StageNotImplemented(self.name, self.__class__.__doc__ or self.description)

    def make_result(
        self,
        artifacts: Optional[dict[str, Artifact]] = None,
        metrics: Optional[dict[str, Any]] = None,
        status: str = "ok",
        error: Optional[str] = None,
        duration_s: float = 0.0,
    ) -> StageResult:
        """Build a :class:`StageResult` for this stage."""
        return StageResult(
            stage=self.name,
            status=status,
            artifacts=artifacts or {},
            metrics=metrics or {},
            error=error,
            duration_s=duration_s,
        )


def _hash_input_files(inputs: Mapping[str, Path]) -> dict[str, str]:
    """Content hashes of the configured input files that exist.

    Editing ``schema.txt`` must re-run the stages that read it, so the file's
    content - not just its path - belongs in the fingerprint.
    """
    out: dict[str, str] = {}
    for key, path in sorted(inputs.items()):
        try:
            data = Path(path).read_bytes()
        except OSError:
            out[key] = "<missing>"
            continue
        out[key] = hashlib.sha256(data).hexdigest()
    return out
