"""Exception hierarchy for the agent package.

Every error carries an actionable message: which config key, which file, which
env var. The workflow is long-running and mostly unattended, so a failure that
does not name its own cause costs a full re-run to diagnose.
"""

from typing import Any, Iterable


class AgentError(Exception):
    """Base class for every error raised by the agent package."""


# --- configuration ---------------------------------------------------------


class ConfigError(AgentError):
    """The configuration document is malformed or internally inconsistent."""


class MissingInputError(ConfigError):
    """A required input path is absent from the config or missing on disk."""

    def __init__(self, key: str, path: Any = None, reason: str = "not configured"):
        self.key = key
        self.path = path
        location = f" (resolved to {path})" if path is not None else ""
        super().__init__(
            f"Required input '{key}' is {reason}{location}. "
            f"Set common.inputs.{key} in the config to an existing file."
        )


class UnknownStageError(ConfigError):
    """A stage was requested that is not registered."""

    def __init__(self, name: str, known: Iterable[str] = ()):
        self.name = name
        available = ", ".join(sorted(known)) or "<none>"
        super().__init__(f"Unknown stage '{name}'. Registered stages: {available}.")


# --- prompts ---------------------------------------------------------------


class PromptError(AgentError):
    """Base class for prompt registry failures."""


class MissingPromptError(PromptError):
    """A prompt id is not present in the manifest."""

    def __init__(self, prompt_id: str, known: Iterable[str] = ()):
        self.prompt_id = prompt_id
        available = ", ".join(sorted(known)) or "<none>"
        super().__init__(
            f"Prompt '{prompt_id}' is not in the manifest. Available ids: {available}. "
            f"Run `python -m agent.cli prompts set {prompt_id} --file <path>` to add it."
        )


class MissingPlaceholderError(PromptError):
    """Rendering a template omitted one or more required placeholders.

    Rendering is strict on purpose: a half-substituted prompt containing a
    literal '${query_id}' would be silently sent to the model and produce
    plausible-looking garbage.
    """

    def __init__(self, prompt_id: str, missing: Iterable[str]):
        self.prompt_id = prompt_id
        self.missing = sorted(missing)
        names = ", ".join(self.missing)
        super().__init__(
            f"Prompt '{prompt_id}' is missing values for placeholder(s): {names}. "
            f"Provide them via the stage's prompt_vars or the render() call."
        )


class InvalidTemplateError(PromptError):
    """A template contains a '$' that is not a valid placeholder."""

    def __init__(self, prompt_id: Any, detail: str):
        super().__init__(
            f"Prompt '{prompt_id}' contains an invalid '$' sequence: {detail}. "
            f"Escape a literal dollar sign as '$$'."
        )


# --- LLM layer -------------------------------------------------------------


class LMError(AgentError):
    """Base class for language-model construction and invocation failures."""


class MissingApiKeyError(LMError):
    """The API key env var for the configured model is unset or empty."""

    def __init__(self, env_var: str, model: str):
        self.env_var = env_var
        self.model = model
        super().__init__(
            f"Environment variable {env_var} is not set, but model '{model}' requires it. "
            f"Export it, or point model.api_key_env at the variable you use."
        )


# --- workspace / C++ ------------------------------------------------------


class WorkspaceError(AgentError):
    """Base class for C++ workspace failures."""


class UnsafePathError(WorkspaceError):
    """A path escaped the workspace root.

    The RLM tools execute on the host with real filesystem access, so this is a
    security boundary, not a convenience check.
    """

    def __init__(self, requested: Any, root: Any):
        super().__init__(
            f"Path {requested!r} resolves outside the workspace root {root}. "
            f"Tools may only touch files inside the workspace."
        )


class PatchError(WorkspaceError):
    """An apply_patch block was malformed or could not be applied."""


# --- pipeline --------------------------------------------------------------


class PipelineError(AgentError):
    """The stage graph could not be built or executed."""


class StageNotImplemented(AgentError):
    """A stage is wired but its body is not implemented yet.

    Carries the stage's documented contract so the message is useful on its own.
    """

    def __init__(self, stage: str, contract: str = ""):
        self.stage = stage
        detail = f"\n\nContract:\n{contract.strip()}" if contract.strip() else ""
        super().__init__(f"Stage '{stage}' is not implemented yet.{detail}")


class DependencyMissingError(AgentError):
    """An external dependency (deno, cmake, a compiler, a Python extra) is absent."""

    def __init__(self, what: str, how_to_fix: str):
        super().__init__(f"{what} is required but was not found. {how_to_fix}")
