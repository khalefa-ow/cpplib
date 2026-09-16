"""Manifest models and template scanning for the prompt registry.

Prompts stay as plain ``.txt`` files on disk (easy to read, diff and hand-edit)
while the manifest JSON carries the metadata the workflow needs: which stage a
prompt belongs to, what placeholders it requires, which fragments it composes
with, and a content hash used for cache invalidation.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from string import Template
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

PromptRole = Literal["system", "task", "fragment", "knowledge"]

# Placeholders that are conventionally filled by another prompt rather than by a
# caller-supplied value. Every `optim_w_*.txt` in prompts/ opens with
# `${constraints}`, and the expert-knowledge variant injects `${expert_knowledge}`.
FRAGMENT_PLACEHOLDERS: dict[str, str] = {
    "constraints": "optim_constraints",
    "expert_knowledge": "expert_knowledge",
}


def sha256_text(text: str) -> str:
    """Hash template text. Used for cache keys and change detection."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def scan_placeholders(text: str) -> tuple[list[str], list[str]]:
    """Find every placeholder in a ``string.Template`` body.

    Handles both the ``${braced}`` form used by most of the prompts and the bare
    ``$named`` form used by ``optim_pretext_general.txt``.

    Returns ``(placeholders, problems)`` where ``problems`` describes any ``$``
    that is not a valid placeholder and not an escaped ``$$``. Those are
    reported rather than ignored because ``Template.substitute`` would raise on
    them at render time, i.e. mid-run.
    """
    names: list[str] = []
    problems: list[str] = []
    for match in Template.pattern.finditer(text):
        if match.group("escaped") is not None:
            continue
        name = match.group("named") or match.group("braced")
        if name is not None:
            if name not in names:
                names.append(name)
            continue
        if match.group("invalid") is not None:
            line = text.count("\n", 0, match.start()) + 1
            snippet = text[match.start() : match.start() + 24].replace("\n", "\\n")
            problems.append(f"line {line}: {snippet!r}")
    return names, problems


class PromptEntry(BaseModel):
    """One prompt file plus its metadata.

    ``stage``, ``role``, ``description`` and ``composes`` are meant to be
    hand-edited; :mod:`agent.prompting.build_manifest` preserves them across
    rebuilds. ``placeholders`` and ``sha256`` are always regenerated from the
    file, so editing them by hand has no effect.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    file: Path
    stage: Optional[str] = None
    role: PromptRole = "task"
    placeholders: list[str] = Field(default_factory=list)
    # placeholder name -> prompt id that supplies its text
    composes: dict[str, str] = Field(default_factory=dict)
    sha256: str = ""
    version: int = 1
    description: str = ""

    def fingerprint(self) -> str:
        """Identity for cache keying: content hash plus manifest version."""
        return f"{self.id}@{self.version}:{self.sha256[:16]}"


class PromptManifest(BaseModel):
    """The manifest document: a prompts root plus the entries under it."""

    model_config = ConfigDict(extra="forbid")

    prompts_root: Path
    entries: dict[str, PromptEntry] = Field(default_factory=dict)

    def sorted_entries(self) -> list[PromptEntry]:
        """Entries in id order, for stable output."""
        return [self.entries[key] for key in sorted(self.entries)]

    def by_stage(self, stage: str) -> list[PromptEntry]:
        """Every entry tagged with the given stage."""
        return [entry for entry in self.sorted_entries() if entry.stage == stage]
