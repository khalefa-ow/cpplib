"""Prompts, stored inline in a JSON manifest."""

from agent.prompting.build_manifest import (
    classify,
    import_prompts_dir,
    rebuild_manifest,
    set_prompt_text,
)
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
    "classify",
    "import_prompts_dir",
    "load_registry",
    "rebuild_manifest",
    "scan_placeholders",
    "set_prompt_text",
    "sha256_text",
]
