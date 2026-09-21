"""The stage runner: ordering, resumption and failure handling.

Ordering is derived from the stages' ``requires``/``produces`` declarations
rather than hardcoded, so adding a stage does not mean editing a list.

Resumption compares each stage's freshly computed input fingerprint against the
one recorded in its existing artifacts. Matching means nothing that could change
the output has changed, so the stage is skipped. That distinction matters for
this workflow specifically: stages are expensive and mostly deterministic given
their inputs, and a run that dies in the optimize loop should not re-pay for
storage planning and code generation.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Iterable, Optional, Sequence, Type

from pydantic import BaseModel, ConfigDict, Field

from agent.config.loader import LoadedConfig
from agent.errors import PipelineError, StageNotImplemented, UnknownStageError
from agent.prompting.registry import DEFAULT_MANIFEST_PATH, PromptRegistry
from agent.stages.base import Artifact, ArtifactStore, RunContext, Stage, StageResult
from agent.stages.divide import DivideStage
from agent.stages.hppgen import HppGenStage
from agent.stages.optimize import OptimizeStage
from agent.stages.query_codegen import QueryCodegenStage
from agent.stages.storage_plan import StoragePlanStage
from agent.trace.callbacks import TraceWriter
from agent.trace.integrations import (
    finish_wandb,
    log_metrics,
    maybe_init_wandb,
    maybe_init_weave,
)
from agent.trace.span import new_id, run_context

# The registry of available stages, in canonical workflow order. Execution order
# comes from the dependency sort, not from this sequence; this is the tie-break
# for stages with no dependency relationship between them.
STAGE_CLASSES: tuple[Type[Stage], ...] = (
    StoragePlanStage,
    DivideStage,
    HppGenStage,
    QueryCodegenStage,
    OptimizeStage,
)

STAGES: dict[str, Type[Stage]] = {cls.name: cls for cls in STAGE_CLASSES}


class RunSummary(BaseModel):
    """The outcome of a pipeline run."""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    results: list[StageResult] = Field(default_factory=list)
    duration_s: float = 0.0
    cache_stats: dict[str, str] = Field(default_factory=dict)
    trace_path: Optional[Path] = None

    @property
    def ok(self) -> bool:
        return all(result.ok for result in self.results)

    @property
    def failed(self) -> list[StageResult]:
        return [r for r in self.results if not r.ok]

    def report(self) -> str:
        """A human-readable run report."""
        lines = [f"run {self.run_id} — {'OK' if self.ok else 'FAILED'} in {self.duration_s:.1f}s"]
        for result in self.results:
            marker = {"ok": "+", "skipped": "=", "disabled": "-", "failed": "x"}.get(
                result.status, "?"
            )
            lines.append(f"  [{marker}] {result.summary()}")
        for stage, stats in sorted(self.cache_stats.items()):
            lines.append(f"  cache[{stage}]: {stats}")
        if self.trace_path:
            lines.append(f"  trace: {self.trace_path}")
        return "\n".join(lines)


def _run_trace_path(configured_path: Optional[Path], run_id: str) -> Optional[Path]:
    """Give each run its own trace file so unrelated runs never mix in one JSONL.

    ``TraceWriter`` appends, so a static configured path (e.g. ``trace.jsonl``)
    would otherwise accumulate events from every run ever made against it, and
    the webview has no way to tell them apart. Inserting the run id before the
    suffix (``trace.jsonl`` -> ``trace_<run_id>.jsonl``) keeps each run's file
    unique while staying deterministic: a resumed run that passes back the same
    ``run_id`` (e.g. the step-by-step CLI mode, one stage per call) maps to the
    same file and correctly keeps appending to it.
    """
    if configured_path is None:
        return None
    return configured_path.with_name(f"{configured_path.stem}_{run_id}{configured_path.suffix}")


def order_stages(
    names: Sequence[str],
    stage_classes: Optional[dict[str, Type[Stage]]] = None,
) -> list[str]:
    """Topologically sort stage names by their artifact dependencies.

    A stage depends on another when it ``requires`` an artifact the other
    ``produces``. Ties are broken by declaration order in ``stage_classes`` so
    runs are reproducible.

    A requirement produced by a stage *outside* the selection is not treated as
    a dependency: it is satisfied from artifacts already on disk. That is what
    makes ``--stages optimize`` runnable on its own.
    """
    classes = stage_classes if stage_classes is not None else STAGES
    unknown = [name for name in names if name not in classes]
    if unknown:
        raise UnknownStageError(unknown[0], classes.keys())

    selected = list(dict.fromkeys(names))
    producer: dict[str, str] = {}
    for name in selected:
        for key in classes[name].produces:
            producer[key] = name

    canonical = {name: index for index, name in enumerate(classes)}
    remaining = set(selected)
    ordered: list[str] = []

    while remaining:
        ready = [
            name
            for name in remaining
            if not {producer[key] for key in classes[name].requires if key in producer} & remaining
        ]
        if not ready:
            raise PipelineError(f"Cyclic stage dependencies among: {', '.join(sorted(remaining))}")
        ready.sort(key=lambda name: canonical.get(name, len(canonical)))
        ordered.append(ready[0])
        remaining.discard(ready[0])

    return ordered


class Pipeline:
    """Runs a selection of stages against one configuration."""

    def __init__(
        self,
        config: LoadedConfig,
        registry: Optional[PromptRegistry] = None,
        manifest_path: str | Path = DEFAULT_MANIFEST_PATH,
        require_api_key: bool = True,
        stage_classes: Optional[dict[str, Type[Stage]]] = None,
    ):
        self.config = config
        self.registry = registry or PromptRegistry.from_manifest(manifest_path)
        self.require_api_key = require_api_key
        # Overridable so tests can substitute fake stages without touching the
        # module-level registry.
        self.stage_classes = dict(stage_classes) if stage_classes else dict(STAGES)

    def plan(
        self,
        only: Optional[Iterable[str]] = None,
        start_from: Optional[str] = None,
    ) -> list[str]:
        """The stage order this run would use, without running anything."""
        names = list(only) if only else (self.config.stage_names() or list(self.stage_classes))
        ordered = order_stages(names, self.stage_classes)
        if start_from:
            if start_from not in ordered:
                raise UnknownStageError(start_from, ordered)
            ordered = ordered[ordered.index(start_from) :]
        return ordered

    def describe(
        self, only: Optional[Iterable[str]] = None, start_from: Optional[str] = None
    ) -> str:
        """A dry-run description: order, dependencies and skip decisions."""
        names = self.plan(only=only, start_from=start_from)
        store = ArtifactStore(self.config.resolve_stage(names[0]).artifacts_dir)
        lines = [f"{len(names)} stage(s) in order:"]
        for index, name in enumerate(names, start=1):
            cls = self.stage_classes[name]
            requires = ", ".join(cls.requires) or "-"
            produces = ", ".join(cls.produces) or "-"
            present = store.has(*cls.produces) if cls.produces else False
            state = "artifacts present" if present else "would run"
            lines.append(
                f"  {index}. {name:16s} requires[{requires}] produces[{produces}] ({state})"
            )
        return "\n".join(lines)

    def run(
        self,
        only: Optional[Iterable[str]] = None,
        start_from: Optional[str] = None,
        force: bool = False,
        run_id: Optional[str] = None,
        stop_on_error: bool = True,
    ) -> RunSummary:
        """Execute the selected stages.

        Args:
            only: Restrict to these stage names.
            start_from: Begin at this stage, skipping earlier ones.
            force: Re-run stages even when their artifacts are current.
            run_id: Reuse an id, e.g. to append to an existing trace.
            stop_on_error: Halt at the first failure. Turning this off runs the
                independent remaining stages, which is useful for a survey run
                but not for a dependent pipeline.

        Returns:
            A :class:`RunSummary`. Stage failures are captured in it rather than
            raised, so the caller always gets a report of what did happen.
        """
        names = self.plan(only=only, start_from=start_from)
        first_cfg = self.config.resolve_stage(names[0])
        rid = run_id or new_id()
        trace_path = _run_trace_path(first_cfg.trace.path, rid)

        store = ArtifactStore(first_cfg.artifacts_dir)
        writer = TraceWriter(
            path=trace_path,
            stdout=first_cfg.trace.stdout,
            max_field_chars=first_cfg.trace.max_field_chars,
        )

        weave = maybe_init_weave(first_cfg.trace)
        wandb = maybe_init_wandb(
            first_cfg.trace,
            run_name=rid,
            config={"stages": names, "model": first_cfg.model.name},
        )

        summary = RunSummary(run_id=rid, trace_path=trace_path)
        started = time.perf_counter()
        ctx = RunContext(
            config=self.config,
            registry=self.registry,
            store=store,
            writer=writer,
            run_id=rid,
            require_api_key=self.require_api_key,
        )

        try:
            with run_context(run_id=rid):
                writer.write({"event": "run_start", "kind": "run", "run_id": rid, "outputs": names})
                for index, name in enumerate(names):
                    result = self._run_stage(ctx, name, force=force)
                    summary.results.append(result)
                    log_metrics(
                        wandb, {f"{name}/{k}": v for k, v in result.metrics.items()}, step=index
                    )
                    if not result.ok and stop_on_error:
                        writer.write(
                            {
                                "event": "run_halted",
                                "kind": "run",
                                "run_id": rid,
                                "name": name,
                                "error": result.error,
                            }
                        )
                        break
                writer.write({"event": "run_end", "kind": "run", "run_id": rid})
        finally:
            summary.duration_s = time.perf_counter() - started
            summary.cache_stats = ctx.cache_stats()
            writer.close()
            finish_wandb(wandb)
            del weave  # initialized for its import side effects only

        return summary

    def _run_stage(self, ctx: RunContext, name: str, force: bool) -> StageResult:
        """Run one stage, handling disabled/skip/failure paths uniformly."""
        cls = self.stage_classes[name]
        started = time.perf_counter()

        try:
            stage = cls(ctx)
        except Exception as exc:
            return StageResult(
                stage=name,
                status="failed",
                error=f"{type(exc).__name__}: {exc}",
                duration_s=time.perf_counter() - started,
            )

        if not stage.cfg.enabled:
            return StageResult(
                stage=name, status="disabled", duration_s=time.perf_counter() - started
            )

        try:
            inputs = ctx.store.collect(cls.requires)
        except PipelineError as exc:
            return StageResult(
                stage=name,
                status="failed",
                error=str(exc),
                duration_s=time.perf_counter() - started,
            )

        if not force and self._is_current(stage, inputs):
            existing = {key: ctx.store.get(key) for key in cls.produces}
            return StageResult(
                stage=name,
                status="skipped",
                artifacts={k: v for k, v in existing.items() if v is not None},
                metrics={"reason": "artifacts up to date"},
                duration_s=time.perf_counter() - started,
            )

        ctx.writer.write(
            {"event": "stage_start", "kind": "stage", "name": name, "run_id": ctx.run_id}
        )
        try:
            result = stage.run(inputs)
        except StageNotImplemented as exc:
            result = StageResult(
                stage=name,
                status="failed",
                error=str(exc).splitlines()[0],
                duration_s=time.perf_counter() - started,
            )
        except Exception as exc:
            result = StageResult(
                stage=name,
                status="failed",
                error=f"{type(exc).__name__}: {exc}",
                duration_s=time.perf_counter() - started,
            )
        ctx.writer.write(
            {
                "event": "stage_end",
                "kind": "stage",
                "name": name,
                "run_id": ctx.run_id,
                "outputs": result.status,
                "error": result.error,
            }
        )
        return result

    @staticmethod
    def _is_current(stage: Stage, inputs: dict[str, Artifact]) -> bool:
        """Whether a stage's outputs already reflect its current inputs.

        Requires every produced artifact to exist *and* to carry a matching
        input fingerprint. Existence alone is not enough: an artifact from
        before a schema edit is present but stale, and silently reusing it is
        the worst failure mode a resumable pipeline can have.
        """
        produces = type(stage).produces
        if not produces or not stage.store.has(*produces):
            return False
        try:
            expected = stage.input_fingerprint(inputs)
        except Exception:
            # Cannot compute a fingerprint (a missing input file, say): re-run
            # and let the stage report the real problem.
            return False
        for key in produces:
            artifact = stage.store.get(key)
            if artifact is None or artifact.input_fingerprint != expected:
                return False
        return True


def run_pipeline(
    config_path: str | Path,
    only: Optional[Iterable[str]] = None,
    start_from: Optional[str] = None,
    force: bool = False,
    manifest_path: str | Path = DEFAULT_MANIFEST_PATH,
    require_api_key: bool = True,
) -> RunSummary:
    """Load a config and run the pipeline. The convenience entry point."""
    config = LoadedConfig.from_file(config_path)
    pipeline = Pipeline(config, manifest_path=manifest_path, require_api_key=require_api_key)
    return pipeline.run(only=only, start_from=start_from, force=force)
