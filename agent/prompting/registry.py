"""The prompt registry: load a manifest, render templates, fingerprint them.

Rendering is deliberately strict. A prompt that reaches the model still
containing a literal ``${query_id}`` produces confident nonsense that costs a
full stage re-run to notice, so a missing placeholder is an error naming the
prompt, the file and the missing names.
"""

from __future__ import annotations

import json
from pathlib import Path
from string import Template
from typing import Any, Mapping, Optional

from pydantic import BaseModel, ConfigDict

from agent.errors import (
    InvalidTemplateError,
    MissingPlaceholderError,
    MissingPromptError,
    PromptError,
)
from agent.prompting.manifest import (
    PromptEntry,
    PromptManifest,
    scan_placeholders,
    sha256_text,
)

DEFAULT_MANIFEST_PATH = Path(__file__).with_name("manifest.json")


class RenderedPrompt(BaseModel):
    """A rendered prompt plus the provenance needed to key a cache on it."""

    model_config = ConfigDict(extra="forbid")

    text: str
    prompt_ids: list[str]
    fingerprint: str
    vars_used: dict[str, Any] = {}

    def __str__(self) -> str:
        return self.text


class PromptRegistry:
    """Holds a loaded manifest and renders its prompts strictly."""

    def __init__(self, manifest: PromptManifest, manifest_path: Optional[Path] = None):
        self.manifest = manifest
        self.manifest_path = manifest_path

    # --- construction -----------------------------------------------------

    @classmethod
    def from_manifest(cls, path: str | Path = DEFAULT_MANIFEST_PATH) -> "PromptRegistry":
        """Load a registry from a manifest JSON file."""
        manifest_path = Path(path).expanduser().resolve()
        if not manifest_path.exists():
            raise PromptError(
                f"Prompt manifest not found at {manifest_path}. "
                f"Run `python -m agent.cli prompts build` to generate it."
            )
        try:
            raw = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise PromptError(f"Prompt manifest {manifest_path} is not valid JSON: {exc}") from exc
        manifest = PromptManifest.model_validate(raw)
        return cls(manifest, manifest_path=manifest_path)

    # --- lookup -----------------------------------------------------------

    def ids(self) -> list[str]:
        """Every prompt id in the manifest, sorted."""
        return sorted(self.manifest.entries)

    def get(self, prompt_id: str) -> PromptEntry:
        """Look up one entry, or raise naming the available ids."""
        entry = self.manifest.entries.get(prompt_id)
        if entry is None:
            raise MissingPromptError(prompt_id, self.manifest.entries.keys())
        return entry

    def text_of(self, prompt_id: str) -> str:
        """Raw template text, straight from the entry.

        There is nothing to go stale against: ``text`` is the only copy, and
        :meth:`PromptEntry.fingerprint` hashes it directly, so a hand edit is
        reflected immediately without needing a rebuild first.
        """
        return self.get(prompt_id).text

    # --- fingerprinting ---------------------------------------------------

    def fingerprint(self, *prompt_ids: str) -> str:
        """Cache fingerprint over one or more prompts.

        Editing any referenced prompt file changes this value, which in turn
        changes every cache key derived from it, so prompt edits automatically
        invalidate cached completions.
        """
        parts = [self.get(pid).fingerprint() for pid in prompt_ids]
        return sha256_text("|".join(parts))

    # --- rendering --------------------------------------------------------

    def render(
        self,
        prompt_id: str,
        *,
        auto_fragments: bool = True,
        variables: Optional[Mapping[str, Any]] = None,
        **kwargs: Any,
    ) -> RenderedPrompt:
        """Render one prompt, substituting every placeholder.

        When ``auto_fragments`` is set, placeholders listed in the entry's
        ``composes`` map are filled by rendering the referenced fragment (for
        example ``${constraints}`` from ``optim_constraints``). An explicit
        value always wins.

        Placeholder values can be passed either as keyword arguments, which
        reads well for a literal call, or in the ``variables`` mapping, which is
        the right choice when the names come from user input: a placeholder
        named ``auto_fragments`` passed as a keyword would bind this method's own
        parameter instead of being substituted.

        Raises :class:`MissingPlaceholderError` if anything is still unfilled.
        """
        entry = self.get(prompt_id)
        text = self.text_of(prompt_id)

        placeholders, problems = scan_placeholders(text)
        if problems:
            raise InvalidTemplateError(prompt_id, "; ".join(problems))

        supplied: dict[str, Any] = {**(variables or {}), **kwargs}
        values: dict[str, Any] = {}
        used_ids = [prompt_id]
        if auto_fragments:
            for placeholder, fragment_id in entry.composes.items():
                if placeholder in supplied or placeholder not in placeholders:
                    continue
                fragment = self.render(fragment_id, auto_fragments=auto_fragments)
                values[placeholder] = fragment.text
                used_ids.extend(fragment.prompt_ids)
        values.update(supplied)

        missing = [name for name in placeholders if name not in values]
        if missing:
            raise MissingPlaceholderError(prompt_id, missing)

        rendered = Template(text).substitute(values)
        # Deduplicate while keeping first-seen order, so the fingerprint is stable.
        ordered_ids = list(dict.fromkeys(used_ids))
        return RenderedPrompt(
            text=rendered,
            prompt_ids=ordered_ids,
            fingerprint=self.fingerprint(*ordered_ids),
            vars_used={k: v for k, v in values.items() if k in placeholders},
        )

    def compose(
        self,
        *prompt_ids: str,
        separator: str = "\n\n",
        auto_fragments: bool = True,
        **variables: Any,
    ) -> RenderedPrompt:
        """Render several prompts and join them.

        Used to build the layered prompts in ``prompts/``, where a general
        pretext, a constraints fragment and a task template are concatenated.
        """
        if not prompt_ids:
            raise PromptError("compose() needs at least one prompt id.")
        parts: list[str] = []
        used_ids: list[str] = []
        vars_used: dict[str, Any] = {}
        for prompt_id in prompt_ids:
            entry_placeholders, _ = scan_placeholders(self.text_of(prompt_id))
            subset = {k: v for k, v in variables.items() if k in entry_placeholders}
            rendered = self.render(prompt_id, auto_fragments=auto_fragments, variables=subset)
            parts.append(rendered.text.strip())
            used_ids.extend(rendered.prompt_ids)
            vars_used.update(rendered.vars_used)
        ordered_ids = list(dict.fromkeys(used_ids))
        return RenderedPrompt(
            text=separator.join(parts),
            prompt_ids=ordered_ids,
            fingerprint=self.fingerprint(*ordered_ids),
            vars_used=vars_used,
        )

    def render_any(
        self,
        prompt_id: Optional[str],
        inline: Optional[str] = None,
        **variables: Any,
    ) -> RenderedPrompt:
        """Render a prompt by id, or fall back to an inline string.

        The legacy configs carry some prompt text inline (``divide.policy``);
        this lets a stage accept either without branching at every call site.
        """
        if inline:
            placeholders, problems = scan_placeholders(inline)
            if problems:
                raise InvalidTemplateError("<inline>", "; ".join(problems))
            missing = [name for name in placeholders if name not in variables]
            if missing:
                raise MissingPlaceholderError("<inline>", missing)
            text = Template(inline).substitute(variables) if placeholders else inline
            return RenderedPrompt(
                text=text,
                prompt_ids=["<inline>"],
                fingerprint=sha256_text(inline),
                vars_used={k: v for k, v in variables.items() if k in placeholders},
            )
        if prompt_id is None:
            raise PromptError("render_any() needs either a prompt id or inline text.")
        return self.render(prompt_id, **variables)


def load_registry(path: str | Path = DEFAULT_MANIFEST_PATH) -> PromptRegistry:
    """Module-level convenience loader."""
    return PromptRegistry.from_manifest(path)
