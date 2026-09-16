"""Reference ("gold") query results: where they come from and how they are made.

Gold results are the ground truth the codegen loop is fixed against, so the
guarantees here matter more than the convenience:

- **Generation is idempotent and opt-in to overwrite.** ``gold.overwrite`` stays
  off by default. Regenerating on every run is slow, and worse, it is a
  correctness hazard: if the dataset moved or changed, silently rewriting gold
  turns a wrong engine into a passing one.
- **A missing gold file is a failure, never an empty expectation.** Comparing
  against an absent file would otherwise read as "no rows expected", which every
  broken query trivially satisfies.
- **The command template is split before substitution** by
  :func:`agent.stages.execution.render_command`, so a query containing spaces
  or quotes stays one argv element.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any, Iterable, Optional

from pydantic import BaseModel, ConfigDict, Field

from agent.config.models import GoldConfig
from agent.errors import AgentError
from agent.stages.execution import render_command
from agent.stages.parsing import Query


class GoldError(AgentError):
    """Gold results could not be produced or found."""


class GoldFile(BaseModel):
    """One query's reference output."""

    model_config = ConfigDict(extra="forbid")

    query_id: str
    path: Path
    exists: bool = False
    generated: bool = False
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.exists and self.error is None


class GoldSet(BaseModel):
    """Gold files for a whole workload."""

    model_config = ConfigDict(extra="forbid")

    mode: str
    files: dict[str, GoldFile] = Field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return bool(self.files) and all(file.ok for file in self.files.values())

    def missing(self) -> list[str]:
        return sorted(qid for qid, file in self.files.items() if not file.ok)

    def summary(self) -> str:
        generated = sum(1 for f in self.files.values() if f.generated)
        reused = sum(1 for f in self.files.values() if f.ok and not f.generated)
        broken = self.missing()
        text = f"gold[{self.mode}]: {generated} generated, {reused} reused"
        if broken:
            text += f", {len(broken)} unavailable ({', '.join(broken[:5])})"
        return text


class GoldProvider:
    """Produces or locates the reference output for each query.

    Args:
        cfg: The resolved ``common.gold`` block.
        timeout_s: Per-query timeout for ``mode="command"``.
    """

    def __init__(self, cfg: GoldConfig, timeout_s: int = 300):
        self.cfg = cfg
        self.timeout_s = timeout_s

    # --- addressing -------------------------------------------------------

    def directory(self) -> Path:
        """Where gold files live, or a clear error when unconfigured."""
        if self.cfg.dir is None:
            raise GoldError(
                "Gold results are required but common.gold.dir is not set. "
                "Point it at the directory holding (or to hold) the reference output."
            )
        return Path(self.cfg.dir)

    def path_for(self, query: Query) -> Path:
        return self.directory() / f"{query.slug}{self.cfg.extension}"

    # --- production -------------------------------------------------------

    def ensure(self, queries: Iterable[Query]) -> GoldSet:
        """Make sure every query has a gold file, generating where configured.

        Never raises for a single query's failure: the error is recorded against
        that query so the stage can report "3 of 22 queries have no gold" rather
        than dying on the first one.
        """
        result = GoldSet(mode=self.cfg.mode)
        queries = list(queries)
        if not queries:
            return result

        connection: Any = None
        if self.cfg.mode == "duckdb":
            connection = self._duckdb_connection()

        try:
            for query in queries:
                result.files[query.id] = self._ensure_one(query, connection)
        finally:
            if connection is not None:
                connection.close()
        return result

    def _ensure_one(self, query: Query, connection: Any) -> GoldFile:
        path = self.path_for(query)
        file = GoldFile(query_id=query.id, path=path, exists=path.exists())

        if file.exists and not self.cfg.overwrite:
            return file
        if self.cfg.mode == "none":
            if not file.exists:
                file.error = (
                    f"common.gold.mode is 'none', so {path} must already exist. "
                    f"Generate it, or set gold.mode to 'duckdb' or 'command'."
                )
            return file

        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            if self.cfg.mode == "duckdb":
                self._run_duckdb(connection, query, path)
            else:
                self._run_command(query, path)
        except Exception as exc:
            file.error = f"{type(exc).__name__}: {exc}"
            file.exists = path.exists()
            return file

        file.exists = path.exists()
        file.generated = file.exists
        if not file.exists:
            file.error = f"gold generation reported success but wrote no file at {path}"
        return file

    # --- duckdb -----------------------------------------------------------

    def _duckdb_connection(self) -> Any:
        """Open the gold database, registering the dataset as views.

        The views are what make ``dataset_dir`` meaningful: a fresh database
        would otherwise have no tables for the workload to query. Parquet file
        stems become view names, which is the convention the ingestion side of
        the generated engine uses too.
        """
        try:
            import duckdb
        except ImportError as exc:
            from agent.errors import DependencyMissingError

            raise DependencyMissingError(
                "The 'duckdb' package (needed for common.gold.mode='duckdb')",
                'Install the agent extra: uv pip install -e ".[dev,agent]"',
            ) from exc

        target = self.cfg.duckdb_path
        if target is None:
            connection = duckdb.connect(":memory:")
        else:
            Path(target).parent.mkdir(parents=True, exist_ok=True)
            connection = duckdb.connect(str(target))

        if self.cfg.install_spatial:
            try:
                connection.execute("INSTALL spatial; LOAD spatial;")
            except Exception as exc:  # pragma: no cover - needs network
                raise GoldError(
                    f"common.gold.install_spatial is set but installing the DuckDB spatial "
                    f"extension failed: {exc}"
                ) from exc

        dataset = self.cfg.dataset_dir
        if dataset is not None:
            directory = Path(dataset)
            if not directory.is_dir():
                raise GoldError(f"common.gold.dataset_dir does not exist: {directory}")
            files = sorted(directory.rglob("*.parquet"))
            if not files:
                raise GoldError(f"No .parquet files under common.gold.dataset_dir: {directory}")
            for file in files:
                name = file.stem.replace("-", "_")
                connection.execute(
                    f'CREATE OR REPLACE VIEW "{name}" AS '
                    f"SELECT * FROM read_parquet({str(file)!r})"
                )
        return connection

    def _run_duckdb(self, connection: Any, query: Query, path: Path) -> None:
        """Write one query's result to a CSV with a header row."""
        if connection is None:  # pragma: no cover - guarded by ensure()
            raise GoldError("No DuckDB connection was opened.")
        statement = query.text.strip().rstrip(";")
        delimiter = "\t" if self.cfg.extension in (".tsv", ".tab") else ","
        connection.execute(
            f"COPY ({statement}) TO {str(path)!r} " f"(FORMAT CSV, HEADER, DELIMITER {delimiter!r})"
        )

    # --- external command -------------------------------------------------

    def _run_command(self, query: Query, path: Path) -> None:
        if not self.cfg.command:
            raise GoldError(
                "common.gold.mode is 'command' but common.gold.command is empty. "
                "Set it to a shell template using {query_text}, {query_id}, "
                "{gold_output} and {dataset_dir}."
            )
        argv = render_command(
            self.cfg.command,
            query_text=query.text,
            query_id=query.id,
            gold_output=str(path),
            dataset_dir=str(self.cfg.dataset_dir or ""),
        )
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=self.timeout_s)
        if proc.returncode != 0:
            tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-10:]
            raise GoldError(
                f"gold command failed (exit {proc.returncode}) for {query.id}: " + " / ".join(tail)
            )
        if not path.exists() and proc.stdout:
            # A reference script that prints to stdout instead of honouring
            # {gold_output} is common enough to accommodate.
            path.write_text(proc.stdout, encoding="utf-8")
