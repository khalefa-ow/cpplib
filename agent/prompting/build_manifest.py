"""Maintain the prompt manifest: recompute derived fields, add/update entries.

Prompt text lives inline in ``manifest.json`` (see :mod:`agent.prompting.manifest`),
so "editing a prompt" means editing an entry's ``text`` field. What used to be
"rescan the prompts directory" is now :func:`rebuild_manifest`: reread the
manifest's own entries and recompute ``placeholders``, bumping ``version``
whenever ``text`` changed since the last build. Metadata a human curates
(``stage``, ``role``, ``description``, ``composes``) is preserved across
rebuilds.

:func:`import_prompts_dir` is the one-time (or occasional bulk-reimport) path
from a directory of ``.txt`` files into manifest entries, kept for migrating
an external prompt corpus in. :func:`set_prompt_text` upserts a single entry,
which is what ``agent.cli prompts set`` uses to add or edit one prompt without
hand-writing JSON.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable, Optional

from agent.errors import InvalidTemplateError, PromptError
from agent.prompting.manifest import (
    FRAGMENT_PLACEHOLDERS,
    PromptEntry,
    PromptManifest,
    PromptRole,
    scan_placeholders,
)

# Filename/id prefix -> (stage, role). First match wins, so order matters: the
# more specific prefixes come first.
_CLASSIFIERS: tuple[tuple[str, tuple[Optional[str], PromptRole]], ...] = (
    ("expert_knowledge", ("optimize", "knowledge")),
    ("optim_constraints", ("optimize", "fragment")),
    ("optim_pretext", ("optimize", "fragment")),
    ("optim_", ("optimize", "task")),
    ("storage_plan", ("storage_plan", "task")),
    ("divide", ("divide", "task")),
    ("hppgen", ("hppgen", "task")),
    ("fix_compile_errors", ("query_codegen", "task")),
    ("query_codegen", ("query_codegen", "task")),
)


def classify(prompt_id: str) -> tuple[Optional[str], PromptRole]:
    """Guess a prompt's stage and role from its id.

    A guess, not a rule: curated metadata in the manifest always wins on a
    rebuild, since an id cannot express everything.
    """
    for prefix, result in _CLASSIFIERS:
        if prompt_id.startswith(prefix):
            return result
    return None, "task"


def infer_composes(placeholders: Iterable[str], available_ids: Iterable[str]) -> dict[str, str]:
    """Map placeholders onto the fragment prompts that conventionally fill them."""
    ids = set(available_ids)
    return {
        name: FRAGMENT_PLACEHOLDERS[name]
        for name in placeholders
        if name in FRAGMENT_PLACEHOLDERS and FRAGMENT_PLACEHOLDERS[name] in ids
    }


def _derive_entry(
    prompt_id: str,
    text: str,
    old: Optional[PromptEntry],
    available_ids: Iterable[str],
    problems: list[str],
    strict: bool,
    bump_on_change: bool = True,
) -> PromptEntry:
    """Build one entry's derived fields from ``text``, preserving curation.

    Args:
        bump_on_change: Bump ``version`` when ``text`` differs from
            ``old.text``. Only meaningful when ``text`` comes from a source
            genuinely independent of ``old`` (an imported file, or text
            supplied to ``set_prompt_text``) — :func:`rebuild_manifest` passes
            ``old.text`` itself as ``text``, so there the two are always
            equal and this comparison would be a no-op; it disables the bump
            instead of silently never firing.
    """
    placeholders, template_problems = scan_placeholders(text)
    if template_problems:
        problems.append(f"{prompt_id}: {'; '.join(template_problems)}")

    stage, role = classify(prompt_id)
    changed = bump_on_change and old is not None and old.text != text

    return PromptEntry(
        id=prompt_id,
        text=text,
        stage=old.stage if old and old.stage is not None else stage,
        role=old.role if old else role,
        placeholders=placeholders,
        composes=(
            old.composes if old and old.composes else infer_composes(placeholders, available_ids)
        ),
        # Bump only on a content change so fingerprints are stable across
        # no-op rebuilds.
        version=(old.version + 1 if changed else (old.version if old else 1)),
        description=old.description if old else "",
    )


def rebuild_manifest(manifest_path: str | Path, strict: bool = True) -> PromptManifest:
    """Recompute every entry's ``placeholders`` from its own ``text``.

    This is the everyday "I edited a prompt's text field, now sync the
    derived metadata" operation — the manifest is both the input and the
    output. Curated fields (``stage``, ``role``, ``description``, ``composes``)
    are preserved unless a placeholder set changed enough that ``composes``
    needs re-inferring (only done when it was empty to begin with).

    ``version`` is left untouched here: with no snapshot of "text as of the
    last build" to compare against, there is nothing to detect a change
    against in this path (the manifest already holds whatever was hand-edited
    into it). That is not a correctness gap — :meth:`PromptEntry.fingerprint`
    hashes ``text`` directly, so a hand edit invalidates the right caches the
    moment it's saved, with no rebuild required first. Use ``prompts set`` (or
    :func:`set_prompt_text`) instead of a direct edit if a version bump matters.

    Args:
        manifest_path: The manifest to reload and rewrite in place.
        strict: Raise on a ``$`` that is not a valid placeholder, rather than
            just recording the problem.

    Returns:
        The manifest that was written.
    """
    path = Path(manifest_path).expanduser().resolve()
    if not path.exists():
        raise PromptError(f"Manifest not found: {path}")

    previous = _load_previous(path)
    if not previous:
        raise PromptError(f"Manifest at {path} has no entries to rebuild.")

    prompt_ids = list(previous.keys())
    entries: dict[str, PromptEntry] = {}
    problems: list[str] = []

    for prompt_id, old in previous.items():
        entries[prompt_id] = _derive_entry(
            prompt_id, old.text, old, prompt_ids, problems, strict, bump_on_change=False
        )

    if problems and strict:
        raise InvalidTemplateError(path, " | ".join(problems))

    manifest = PromptManifest(entries=entries)
    _write(path, manifest)
    return manifest


def import_prompts_dir(
    prompts_root: str | Path,
    manifest_path: str | Path,
    pattern: str = "*.txt",
    strict: bool = True,
) -> PromptManifest:
    """Import (or bulk-reimport) prompt ``.txt`` files as inline manifest entries.

    Every file's stem becomes a prompt id; its content becomes that entry's
    ``text``. Ids already present in the manifest but outside this batch of
    files are left untouched — this merges a directory in, it does not
    replace the whole manifest.

    Args:
        prompts_root: Directory holding the prompt ``.txt`` files.
        manifest_path: Manifest to merge into (created if absent).
        pattern: Glob for prompt files.
        strict: Raise on a template containing a ``$`` that is not a valid
            placeholder, rather than just recording the problem.

    Returns:
        The manifest that was written.
    """
    root = Path(prompts_root).expanduser().resolve()
    if not root.is_dir():
        raise PromptError(f"Prompts directory not found: {root}")

    files = sorted(root.glob(pattern))
    if not files:
        raise PromptError(f"No prompt files matching {pattern!r} in {root}.")

    out_path = Path(manifest_path).expanduser().resolve()
    previous = _load_previous(out_path)

    imported_ids = [f.stem for f in files]
    all_ids = list(dict.fromkeys([*previous.keys(), *imported_ids]))
    problems: list[str] = []

    entries: dict[str, PromptEntry] = dict(previous)
    for file in files:
        prompt_id = file.stem
        text = file.read_text(encoding="utf-8")
        old = previous.get(prompt_id)
        entries[prompt_id] = _derive_entry(prompt_id, text, old, all_ids, problems, strict)

    if problems and strict:
        raise InvalidTemplateError(root, " | ".join(problems))

    manifest = PromptManifest(entries=entries)
    _write(out_path, manifest)
    return manifest


def set_prompt_text(
    manifest_path: str | Path,
    prompt_id: str,
    text: str,
    stage: Optional[str] = None,
    role: Optional[PromptRole] = None,
    description: Optional[str] = None,
    composes: Optional[dict[str, str]] = None,
    strict: bool = True,
) -> PromptManifest:
    """Add or update one prompt entry's text (and optionally its metadata).

    The everyday way to author a prompt without hand-writing an escaped JSON
    string: compose the text as a normal file or string, then call this (or
    ``agent.cli prompts set``) to embed it.

    Args:
        manifest_path: Manifest to update (created if absent).
        prompt_id: Entry id to add or update.
        text: The new prompt template text.
        stage / role / description / composes: Explicit overrides for the
            curated fields; omitted ones keep the existing value (or the
            classifier's guess, for a brand new entry).
        strict: Raise on a ``$`` that is not a valid placeholder.

    Returns:
        The manifest that was written.
    """
    out_path = Path(manifest_path).expanduser().resolve()
    previous = _load_previous(out_path)
    old = previous.get(prompt_id)

    all_ids = list(dict.fromkeys([*previous.keys(), prompt_id]))
    problems: list[str] = []
    entry = _derive_entry(prompt_id, text, old, all_ids, problems, strict)

    if stage is not None:
        entry.stage = stage
    if role is not None:
        entry.role = role
    if description is not None:
        entry.description = description
    if composes is not None:
        entry.composes = composes

    if problems and strict:
        raise InvalidTemplateError(out_path, " | ".join(problems))

    entries = dict(previous)
    entries[prompt_id] = entry
    manifest = PromptManifest(entries=entries)
    _write(out_path, manifest)
    return manifest


def _write(path: Path, manifest: PromptManifest) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(manifest.model_dump(mode="json"), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _load_previous(manifest_path: Path) -> dict[str, PromptEntry]:
    """Read the existing manifest, if any, to preserve curated metadata."""
    if not manifest_path.exists():
        return {}
    try:
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
        return PromptManifest.model_validate(raw).entries
    except Exception:
        # A corrupt or outdated manifest should not block a rebuild; the point
        # of a rebuild is to regenerate it.
        return {}
