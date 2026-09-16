"""Content-addressed disk cache for LLM invocations.

This caches at the **module-invocation** level: a rendered prompt plus its
inputs maps to the parsed outputs of a whole DSPy call. That is the unit a stage
re-runs, and it is what makes a re-run free rather than merely cheaper.

Why not rely on DSPy's own cache alone:

- The key is ours, so it is inspectable and stable across DSPy upgrades. A DSPy
  version bump does not silently invalidate a month of cached work.
- The prompt fingerprint is part of the key, so editing a prompt file
  invalidates exactly the entries that depended on it.
- The on-disk form is readable JSON, so a cached answer can be inspected and
  hand-corrected.

DSPy's own LM cache is left enabled underneath (``model.lm_cache``) and covers
what this cannot see: the individual completions inside a single RLM invocation,
including its recursive sub-calls. This cache stores the RLM call as one unit.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Callable, Literal, Optional, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")

# Bump when the on-disk entry layout changes in a way that makes old entries
# unreadable. Part of every key, so a bump invalidates the whole cache.
CACHE_SCHEMA_VERSION = 1


def canonical_hash(payload: Any) -> str:
    """Stable hash of an arbitrary JSON-serializable payload.

    ``sort_keys`` makes dict ordering irrelevant, so two logically identical
    inputs hash the same regardless of construction order. Non-serializable
    values fall back to ``repr``, which is stable enough for cache keying and
    never raises mid-run.
    """
    text = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=repr)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class CacheKey(BaseModel):
    """Everything that can change an answer, and nothing that cannot."""

    model_config = ConfigDict(extra="forbid")

    stage: str
    kind: Literal["lm", "stage", "tool"] = "lm"
    # From PromptRegistry.fingerprint(): changes when any prompt file changes.
    prompt_fingerprint: str = ""
    # canonical_hash() of the call's inputs.
    payload_hash: str = ""
    # ModelConfig.signature(): name, temperature, max_tokens, adapter, api_base.
    model_signature: str = ""
    schema_version: int = CACHE_SCHEMA_VERSION

    def digest(self) -> str:
        """The cache address for this key."""
        parts = [
            str(self.schema_version),
            self.stage,
            self.kind,
            self.prompt_fingerprint,
            self.payload_hash,
            self.model_signature,
        ]
        return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()


class CacheEntry(BaseModel):
    """A stored value plus the key that produced it."""

    model_config = ConfigDict(extra="forbid")

    key: CacheKey
    digest: str
    created_at: float
    value: Any = None
    meta: dict[str, Any] = Field(default_factory=dict)

    @property
    def age_seconds(self) -> float:
        return time.time() - self.created_at


class CacheStats(BaseModel):
    """Counters for one cache instance's lifetime."""

    model_config = ConfigDict(extra="forbid")

    hits: int = 0
    misses: int = 0
    writes: int = 0
    errors: int = 0

    @property
    def lookups(self) -> int:
        return self.hits + self.misses

    def hit_rate(self) -> float:
        return self.hits / self.lookups if self.lookups else 0.0

    def summary(self) -> str:
        return (
            f"{self.hits} hit(s), {self.misses} miss(es), {self.writes} write(s), "
            f"{self.errors} error(s), {self.hit_rate():.0%} hit rate"
        )


class DiskCache:
    """A sharded, atomically-written JSON cache on the local filesystem.

    Layout is ``<dir>/<digest[:2]>/<digest>.json`` — the two-character shard
    keeps directory listings usable once a long optimization run has produced
    thousands of entries.
    """

    def __init__(
        self,
        dir: str | Path,
        enabled: bool = True,
        refresh: bool = False,
        stage: str = "",
    ):
        self.dir = Path(dir).expanduser()
        self.enabled = enabled
        # refresh=True recomputes on every lookup but still writes the result,
        # which is how you refresh a stale answer without discarding the cache.
        self.refresh = refresh
        self.stage = stage
        self.stats = CacheStats()
        # Guards the counters only. Cross-process safety comes from atomic
        # replace: concurrent writers of the same key produce the same value.
        self._lock = threading.Lock()

    @classmethod
    def from_config(cls, cache_config: Any, stage: str = "") -> "DiskCache":
        """Build from a :class:`agent.config.models.CacheConfig`."""
        return cls(
            dir=cache_config.dir,
            enabled=cache_config.enabled,
            refresh=cache_config.refresh,
            stage=stage,
        )

    # --- addressing -------------------------------------------------------

    def path_for(self, digest: str) -> Path:
        """On-disk location for a digest."""
        return self.dir / digest[:2] / f"{digest}.json"

    def make_key(
        self,
        payload: Any,
        prompt_fingerprint: str = "",
        model_signature: str = "",
        kind: Literal["lm", "stage", "tool"] = "lm",
        stage: Optional[str] = None,
    ) -> CacheKey:
        """Build a key from a call's inputs. Convenience over the model."""
        return CacheKey(
            stage=stage if stage is not None else self.stage,
            kind=kind,
            prompt_fingerprint=prompt_fingerprint,
            payload_hash=canonical_hash(payload),
            model_signature=model_signature,
        )

    # --- read / write -----------------------------------------------------

    def get(self, key: CacheKey) -> Optional[CacheEntry]:
        """Look up an entry, or return None on a miss.

        A corrupt or unreadable entry counts as a miss rather than an error:
        the cache is a performance layer and must never be able to fail a run.
        """
        if not self.enabled:
            return None
        digest = key.digest()
        path = self.path_for(digest)
        if self.refresh:
            # Deliberately not a hit: the caller recomputes and overwrites.
            with self._lock:
                self.stats.misses += 1
            return None
        if not path.exists():
            with self._lock:
                self.stats.misses += 1
            return None
        try:
            entry = CacheEntry.model_validate_json(path.read_text(encoding="utf-8"))
        except Exception:
            with self._lock:
                self.stats.errors += 1
                self.stats.misses += 1
            return None
        with self._lock:
            self.stats.hits += 1
        return entry

    def put(self, key: CacheKey, value: Any, meta: Optional[dict[str, Any]] = None) -> CacheEntry:
        """Store a value atomically.

        Writes to a temp file in the destination directory and ``os.replace``s
        it into position, so a crashed or concurrent run can never leave a
        half-written entry that a later run would read as valid.
        """
        digest = key.digest()
        entry = CacheEntry(
            key=key,
            digest=digest,
            created_at=time.time(),
            value=value,
            meta=meta or {},
        )
        if not self.enabled:
            return entry
        path = self.path_for(digest)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = entry.model_dump_json(indent=2)
            fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    handle.write(payload)
                os.replace(tmp_name, path)
            except BaseException:
                Path(tmp_name).unlink(missing_ok=True)
                raise
            with self._lock:
                self.stats.writes += 1
        except Exception:
            # A cache that cannot write must not fail the stage that called it.
            with self._lock:
                self.stats.errors += 1
        return entry

    def memoize(
        self,
        key: CacheKey,
        fn: Callable[[], T],
        meta: Optional[dict[str, Any]] = None,
    ) -> tuple[T, bool]:
        """Return ``fn()``'s result, cached under ``key``.

        Returns ``(value, was_hit)`` so callers can record cache status in the
        trace rather than guessing at it from timing.
        """
        entry = self.get(key)
        if entry is not None:
            return entry.value, True
        value = fn()
        self.put(key, value, meta=meta)
        return value, False

    # --- maintenance ------------------------------------------------------

    def clear(self) -> int:
        """Delete every entry. Returns how many files were removed."""
        if not self.dir.exists():
            return 0
        removed = 0
        for path in self.dir.rglob("*.json"):
            path.unlink(missing_ok=True)
            removed += 1
        return removed

    def count(self) -> int:
        """Number of entries currently on disk."""
        return sum(1 for _ in self.dir.rglob("*.json")) if self.dir.exists() else 0
