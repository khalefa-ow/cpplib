"""Manifest models and template scanning for the prompt registry.

Prompt text lives inline in the manifest JSON, alongside the metadata the
workflow needs: which stage a prompt belongs to, what placeholders it
requires, and which fragments it composes with. There is exactly one source
of truth on disk, so a fingerprint can hash ``text`` directly at cache-key
time rather than comparing against a separately stored digest — there is
nothing for that digest to go stale against.
"""

from __future__ import annotations

import hashlib
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
    """One prompt's text plus its metadata.

    ``stage``, ``role``, ``description`` and ``composes`` are meant to be
    hand-edited; :mod:`agent.prompting.build_manifest` preserves them across
    rebuilds. ``text`` is the prompt template itself and is always the
    current one — :meth:`fingerprint` hashes it directly rather than trusting
    a separately stored digest, so a hand edit to ``text`` invalidates the
    right caches immediately, with no rebuild step required for that to be
    true. ``placeholders`` is likewise informational (``render()`` rescans
    ``text`` itself); it is kept for ``prompts list``/``prompts build`` to
    display without rendering.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    text: str = ""
    stage: Optional[str] = None
    role: PromptRole = "task"
    placeholders: list[str] = Field(default_factory=list)
    # placeholder name -> prompt id that supplies its text
    composes: dict[str, str] = Field(default_factory=dict)
    version: int = 1
    description: str = ""

    def fingerprint(self) -> str:
        """Identity for cache keying: manifest version plus a hash of the current text."""
        return f"{self.id}@{self.version}:{sha256_text(self.text)[:16]}"


class PromptManifest(BaseModel):
    """The manifest document: every prompt entry, keyed by id."""

    model_config = ConfigDict(extra="forbid")

    entries: dict[str, PromptEntry] = Field(default_factory=dict)

    def sorted_entries(self) -> list[PromptEntry]:
        """Entries in id order, for stable output."""
        return [self.entries[key] for key in sorted(self.entries)]

    def by_stage(self, stage: str) -> list[PromptEntry]:
        """Every entry tagged with the given stage."""
        return [entry for entry in self.sorted_entries() if entry.stage == stage]
