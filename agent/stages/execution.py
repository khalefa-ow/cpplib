"""Running a built query binary, and timing it well enough to compare rounds.

The generated engine is driven through one configured command
(``params.run_command``), not through a hardcoded convention, because the shape
of the harness is a property of the project being generated, not of this
package.

Two measurement decisions carry the weight here:

- **Median of N, never a single sample.** One run of a query is dominated by
  page faults, frequency scaling and whatever else the machine is doing. An
  optimize round that keeps or reverts a change based on a single sample keeps
  noise and reverts improvements.
- **Prefer the engine's own reported runtime to wall clock.** Wall clock
  includes process start, data load and teardown, which are the same in every
  round and so shrink the apparent effect of a real improvement. When
  ``params.runtime_pattern`` matches the output, that number is used instead.
"""

from __future__ import annotations

import re
import shlex
import statistics
import subprocess
import time
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from agent.errors import AgentError
from agent.stages.parsing import Query


class ExecutionError(AgentError):
    """A query could not be executed."""


def render_command(template: str, drop_empty: bool = False, **values: str) -> list[str]:
    """Split a shell template into argv, then substitute placeholders.

    Order matters: substituting first would let a value containing spaces,
    quotes or newlines become several arguments, so the split happens on the
    template and every value stays exactly one argv element whatever is in it.

    Args:
        drop_empty: Remove tokens that were a single placeholder and rendered
            empty. Without this an unused optional flag would be passed to the
            process as a literal empty argument, which most argument parsers
            reject.
    """
    try:
        tokens = shlex.split(template)
    except ValueError as exc:
        raise ExecutionError(
            f"Command template is not valid shell syntax: {template!r} ({exc})"
        ) from exc
    if not tokens:
        raise ExecutionError("Command template is empty.")
    rendered: list[str] = []
    for token in tokens:
        try:
            value = token.format(**values)
        except KeyError as exc:
            known = ", ".join(sorted(values))
            raise ExecutionError(
                f"Command template references unknown placeholder {exc} in {token!r}. "
                f"Available: {known}."
            ) from exc
        if drop_empty and not value.strip() and _is_single_placeholder(token):
            continue
        rendered.append(value)
    return rendered


def _is_single_placeholder(token: str) -> bool:
    return bool(re.fullmatch(r"\{[A-Za-z_][A-Za-z0-9_]*\}", token))


class RunOutcome(BaseModel):
    """One execution of one query."""

    model_config = ConfigDict(extra="forbid")

    query_id: str
    ok: bool
    command: list[str] = Field(default_factory=list)
    returncode: Optional[int] = None
    output: str = ""
    stderr: str = ""
    wall_s: float = 0.0
    reported_s: Optional[float] = None
    timed_out: bool = False
    output_path: Optional[Path] = None

    @property
    def runtime_s(self) -> float:
        """The number to compare across rounds."""
        return self.reported_s if self.reported_s is not None else self.wall_s

    def brief(self, max_lines: int = 20) -> str:
        if self.ok:
            return f"OK in {self.runtime_s:.4f}s ({len(self.output.splitlines())} output line(s))"
        if self.timed_out:
            return f"TIMED OUT after {self.wall_s:.1f}s: {' '.join(self.command)}"
        tail = (self.stderr or self.output).strip().splitlines()[-max_lines:]
        return f"FAILED (exit {self.returncode})\n" + "\n".join("  " + line for line in tail)


class Measurement(BaseModel):
    """Repeated timings of one query."""

    model_config = ConfigDict(extra="forbid")

    query_id: str
    samples: list[float] = Field(default_factory=list)
    ok: bool = False
    error: Optional[str] = None

    @property
    def median_s(self) -> Optional[float]:
        return statistics.median(self.samples) if self.samples else None

    @property
    def best_s(self) -> Optional[float]:
        return min(self.samples) if self.samples else None

    def brief(self) -> str:
        if not self.ok:
            return f"{self.query_id}: measurement failed ({self.error})"
        spread = ""
        if len(self.samples) > 1:
            spread = f" (min {min(self.samples):.4f}, max {max(self.samples):.4f})"
        return (
            f"{self.query_id}: median {self.median_s:.4f}s over {len(self.samples)} run(s)" + spread
        )


class QueryRunner:
    """Executes queries through a configured command template.

    Args:
        template: ``params.run_command``. Placeholders: ``{query_id}``,
            ``{query_text}``, ``{output}``, ``{project_root}``, ``{build_dir}``,
            ``{dataset_dir}``, ``{sf}`` and ``{trace}``.
        project_root: Working directory for the command.
        output_dir: Where ``{output}`` files are written.
        trace_flag: Substituted for ``{trace}`` when tracing is requested, and
            replaced by nothing otherwise.
        runtime_pattern: Regex whose first group is the engine's own reported
            runtime in seconds. Matched against stdout and stderr.
    """

    def __init__(
        self,
        template: Optional[str],
        project_root: str | Path,
        output_dir: Optional[str | Path] = None,
        build_dir: Optional[str | Path] = None,
        dataset_dir: Optional[str | Path] = None,
        sf: str = "",
        trace_flag: str = "",
        runtime_pattern: Optional[str] = None,
        timeout_s: int = 300,
    ):
        self.template = (template or "").strip()
        self.project_root = Path(project_root)
        self.output_dir = Path(output_dir) if output_dir else self.project_root / "out"
        self.build_dir = Path(build_dir) if build_dir else self.project_root / "build"
        self.dataset_dir = Path(dataset_dir) if dataset_dir else None
        self.sf = sf
        self.trace_flag = trace_flag
        self.timeout_s = timeout_s
        self.runtime_pattern = re.compile(runtime_pattern) if runtime_pattern else None

    def available(self) -> bool:
        """Whether a run command is configured at all.

        A stage that cannot execute must say so rather than report success, so
        this is checked explicitly instead of defaulting to some guessed binary.
        """
        return bool(self.template)

    def unavailable_reason(self) -> str:
        return (
            "No run command is configured, so generated queries cannot be executed or "
            "checked against gold. Set stages.<stage>.params.run_command, e.g. "
            '"./build/engine --query {query_id} --out {output}".'
        )

    def output_path_for(self, query: Query) -> Path:
        return self.output_dir / f"{query.slug}.out"

    def run(self, query: Query, trace: bool = False) -> RunOutcome:
        """Execute one query once.

        Output is read from the ``{output}`` file when the template names one,
        and from stdout otherwise, so both harness conventions work.
        """
        if not self.available():
            return RunOutcome(query_id=query.id, ok=False, stderr=self.unavailable_reason())

        wants_file = "{output}" in self.template
        output_path = self.output_path_for(query)
        if wants_file:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.unlink(missing_ok=True)

        argv = render_command(
            self.template,
            drop_empty=True,
            query_id=query.id,
            query_text=query.text,
            output=str(output_path),
            project_root=str(self.project_root),
            build_dir=str(self.build_dir),
            dataset_dir=str(self.dataset_dir or ""),
            sf=self.sf,
            trace=self.trace_flag if trace else "",
        )

        started = time.perf_counter()
        try:
            proc = subprocess.run(
                argv,
                cwd=str(self.project_root),
                capture_output=True,
                text=True,
                timeout=self.timeout_s,
            )
        except subprocess.TimeoutExpired:
            return RunOutcome(
                query_id=query.id,
                ok=False,
                command=argv,
                timed_out=True,
                wall_s=time.perf_counter() - started,
                stderr=f"Timed out after {self.timeout_s}s.",
            )
        except OSError as exc:
            return RunOutcome(
                query_id=query.id,
                ok=False,
                command=argv,
                stderr=f"Could not start the run command ({exc}). Is the project built?",
                wall_s=time.perf_counter() - started,
            )

        wall = time.perf_counter() - started
        output = proc.stdout or ""
        if wants_file and output_path.exists():
            output = output_path.read_text(encoding="utf-8", errors="replace")

        return RunOutcome(
            query_id=query.id,
            ok=proc.returncode == 0,
            command=argv,
            returncode=proc.returncode,
            output=output,
            stderr=proc.stderr or "",
            wall_s=wall,
            reported_s=self._reported_runtime(proc.stdout, proc.stderr),
            output_path=output_path if wants_file and output_path.exists() else None,
        )

    def measure(self, query: Query, repeats: int = 3, trace: bool = False) -> Measurement:
        """Time a query ``repeats`` times, discarding nothing but failures.

        Timing runs go through the same path as correctness runs so a change
        that breaks execution cannot be measured as an improvement.
        """
        measurement = Measurement(query_id=query.id)
        for _ in range(max(1, repeats)):
            outcome = self.run(query, trace=trace)
            if not outcome.ok:
                measurement.error = outcome.brief()
                measurement.ok = False
                return measurement
            measurement.samples.append(outcome.runtime_s)
        measurement.ok = True
        return measurement

    def _reported_runtime(self, stdout: Optional[str], stderr: Optional[str]) -> Optional[float]:
        if self.runtime_pattern is None:
            return None
        for text in (stdout or "", stderr or ""):
            match = self.runtime_pattern.search(text)
            if match and match.groups():
                try:
                    return float(match.group(1))
                except ValueError:
                    continue
        return None
