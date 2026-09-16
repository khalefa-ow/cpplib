"""Generate the prompt manifest by scanning a prompts directory.

The prompt files stay authoritative; the manifest is derived. Metadata a human
curates (``stage``, ``role``, ``description``, ``composes``) is preserved across
rebuilds, and ``version`` is bumped whenever a file's content hash changes, so a
prompt edit invalidates the caches keyed on its fingerprint.
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
    sha256_text,
)

# Filename prefix -> (stage, role). First match wins, so order matters: the
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
    """Guess a prompt's stage and role from its filename.

    A guess, not a rule: the manifest is hand-editable precisely because
    filenames cannot express everything.
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


def build_manifest(
    prompts_root: str | Path,
    manifest_path: str | Path,
    pattern: str = "*.txt",
    strict: bool = True,
) -> PromptManifest:
    """Scan ``prompts_root`` and write a manifest to ``manifest_path``.

    Args:
        prompts_root: Directory holding the prompt ``.txt`` files.
        manifest_path: Where to write the manifest JSON.
        pattern: Glob for prompt files.
        strict: Raise on a template containing a ``$`` that is not a valid
            placeholder. Turning this off records the problem in the entry's
            description instead, which is useful for a first pass over prompts
            written without ``$$`` escaping in mind.

    Returns:
        The manifest that was written.
    """
    root = Path(prompts_root).expanduser().resolve()
    if not root.is_dir():
        raise PromptError(f"Prompts directory not found: {root}")

    out_path = Path(manifest_path).expanduser().resolve()
    previous = _load_previous(out_path)

    files = sorted(root.glob(pattern))
    if not files:
        raise PromptError(f"No prompt files matching {pattern!r} in {root}.")

    prompt_ids = [f.stem for f in files]
    entries: dict[str, PromptEntry] = {}
    problems: list[str] = []

    for file in files:
        prompt_id = file.stem
        text = file.read_text(encoding="utf-8")
        placeholders, template_problems = scan_placeholders(text)
        if template_problems:
            detail = f"{file.name}: {'; '.join(template_problems)}"
            if strict:
                problems.append(detail)
            else:
                problems.append(detail)

        digest = sha256_text(text)
        old = previous.get(prompt_id)
        stage, role = classify(prompt_id)

        entry = PromptEntry(
            id=prompt_id,
            # Stored relative to the manifest so the manifest stays portable.
            file=Path(_relative_to(root, file)),
            stage=old.stage if old and old.stage is not None else stage,
            role=old.role if old else role,
            placeholders=placeholders,
            composes=(
                old.composes if old and old.composes else infer_composes(placeholders, prompt_ids)
            ),
            sha256=digest,
            # Bump only on a content change so fingerprints are stable across
            # no-op rebuilds.
            version=(
                old.version + 1
                if old and old.sha256 and old.sha256 != digest
                else (old.version if old else 1)
            ),
            description=old.description if old else "",
        )
        entries[prompt_id] = entry

    if problems and strict:
        raise InvalidTemplateError(root, " | ".join(problems))

    manifest = PromptManifest(
        prompts_root=Path(_relative_to(out_path.parent, root)), entries=entries
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(manifest.model_dump(mode="json"), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest


def _relative_to(base: Path, target: Path) -> str:
    """Relative path from ``base`` to ``target``, falling back to absolute.

    ``Path.relative_to`` cannot walk upwards, and the prompts directory is a
    sibling of the package, so ``os.path.relpath`` is the right tool here.
    """
    import os

    try:
        return os.path.relpath(target, base)
    except ValueError:
        # Different drives on Windows; an absolute path is still correct.
        return str(target)


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
