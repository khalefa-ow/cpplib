"""Prompt files on disk, organized by a JSON manifest."""

from agent.prompting.build_manifest import build_manifest, classify
from agent.prompting.manifest import PromptEntry, PromptManifest, scan_placeholders, sha256_text
from agent.prompting.registry import (
    DEFAULT_MANIFEST_PATH,
    PromptRegistry,
    RenderedPrompt,
    load_registry,
)

__all__ = [
    "DEFAULT_MANIFEST_PATH",
    "PromptEntry",
    "PromptManifest",
    "PromptRegistry",
    "RenderedPrompt",
    "build_manifest",
    "classify",
    "load_registry",
    "scan_placeholders",
    "sha256_text",
]
