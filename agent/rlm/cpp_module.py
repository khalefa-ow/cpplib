"""``CppRLM``: a ``dspy.RLM`` wired to the cpplib tool set.

Large inputs — a schema, a query workload, a storage plan, a compiler log — are
passed as **keyword arguments**, not concatenated into the instruction. That is
what makes RLM worth using: the sandbox receives them as Python variables the
model can slice, grep and summarize, so a 500k-character input never has to fit
in a single prompt.

The class also carries the two guards a real RLM run needs: a preflight check
for Deno (a missing Deno is the most common RLM failure and its native error is
opaque), and budget caps from config, since one RLM call can fan out into dozens
of sub-LM calls.
"""

from __future__ import annotations

import shutil
from typing import Any, Callable, Optional, Sequence, cast

from agent.config.models import ModelConfig, RLMConfig
from agent.errors import DependencyMissingError
from agent.rlm.cpp_tools import make_cpp_tools, tool_names
from agent.rlm.workspace import CppWorkspace
from agent.trace.callbacks import TraceWriter


def deno_available() -> bool:
    """Whether the Deno runtime that hosts the RLM sandbox is installed."""
    return shutil.which("deno") is not None


def preflight(require_deno: bool = True) -> None:
    """Verify the RLM runtime prerequisites, with an actionable message.

    DSPy's own failure for a missing Deno surfaces deep inside the interpreter
    as a protocol error, which is hard to connect back to "install Deno".
    """
    try:
        import dspy  # noqa: F401
    except ImportError as exc:
        raise DependencyMissingError(
            "The 'dspy' package",
            'Install the agent extra: uv pip install -e ".[dev,agent]"',
        ) from exc
    if require_deno and not deno_available():
        raise DependencyMissingError(
            "The Deno runtime (required by dspy.RLM's Pyodide sandbox)",
            "Install it from https://deno.land, then ensure `deno` is on PATH.",
        )


def _module_base() -> type:
    """``dspy.Module`` when available, else ``object``.

    Lets this module be imported (and its helpers tested) without dspy present,
    so ``agent.cli doctor`` can report the missing extra rather than traceback.
    """
    try:
        import dspy

        return cast(type, dspy.Module)
    except Exception:  # pragma: no cover - depends on env
        return object


class CppRLM(_module_base()):  # type: ignore[misc]
    """An RLM that can read, edit and compile a C++ workspace.

    Args:
        signature: A DSPy signature, as a string like
            ``"schema, queries -> storage_plan"`` or a ``dspy.Signature`` class.
        workspace: The C++ tree the tools act on. Omit for a pure reasoning
            stage with no code access.
        rlm_config: Budget and routing settings.
        sub_lm: Model for the RLM's internal recursive calls. Usually cheaper
            than the outer model.
        writer: Trace writer; tool calls are recorded as spans.
        allow_writes: Expose the mutating tools.
        allow_build: Expose the compile/build tools.
        run_query: Optional query execution callable.
        extra_tools: Additional callables to expose alongside the cpplib ones.
    """

    def __init__(
        self,
        signature: Any,
        workspace: Optional[CppWorkspace] = None,
        rlm_config: Optional[RLMConfig] = None,
        sub_lm: Optional[Any] = None,
        writer: Optional[TraceWriter] = None,
        allow_writes: bool = True,
        allow_build: bool = True,
        run_query: Optional[Callable[[str, str], str]] = None,
        extra_tools: Sequence[Callable[..., Any]] = (),
        require_deno: bool = True,
    ):
        preflight(require_deno=require_deno)
        super().__init__()

        import dspy

        self.config = rlm_config or RLMConfig()
        self.workspace = workspace
        self.writer = writer

        tools: list[Callable[..., Any]] = []
        if workspace is not None:
            tools.extend(
                make_cpp_tools(
                    workspace,
                    writer=writer,
                    allow_writes=allow_writes,
                    allow_build=allow_build,
                    run_query=run_query,
                    auto_build=self.config.auto_build_after_write,
                )
            )
        tools.extend(extra_tools)
        self.tools = tools

        self.rlm = dspy.RLM(
            signature,
            # DSPy 3.3 names this `max_iters`, not `max_iterations`.
            max_iters=self.config.max_iterations,
            max_llm_calls=self.config.max_llm_calls,
            max_output_chars=self.config.max_output_chars,
            verbose=self.config.verbose,
            tools=tools or None,
            sub_lm=sub_lm,
        )

    def forward(self, **kwargs: Any) -> Any:
        """Run the RLM. Every input is passed as a sandbox variable."""
        return self.rlm(**kwargs)

    def describe(self) -> str:
        """One-line summary for logs and trace metadata."""
        names = ", ".join(tool_names(self.tools)) or "none"
        return (
            f"CppRLM(max_iters={self.config.max_iterations}, "
            f"max_llm_calls={self.config.max_llm_calls}, tools=[{names}])"
        )


def build_predictor(
    signature: Any,
    payload_chars: int,
    rlm_config: RLMConfig,
    workspace: Optional[CppWorkspace] = None,
    sub_lm: Optional[Any] = None,
    writer: Optional[TraceWriter] = None,
    allow_writes: bool = False,
    allow_build: bool = False,
    use_chain_of_thought: bool = True,
) -> tuple[Any, str]:
    """Choose between RLM and a plain predictor based on input size.

    Below ``rlm_config.threshold_chars`` the REPL round-trips cost more than they
    save, so a ``ChainOfThought`` is both cheaper and more reliable. Above it,
    the input may not fit a single prompt at all and RLM is the only option.

    Returns ``(module, reason)`` so the choice can be recorded in the trace
    instead of being invisible.
    """
    import dspy

    if payload_chars >= rlm_config.threshold_chars or workspace is not None:
        reason = (
            f"RLM: payload {payload_chars} chars >= threshold {rlm_config.threshold_chars}"
            if payload_chars >= rlm_config.threshold_chars
            else "RLM: a C++ workspace was provided, so code tools are needed"
        )
        module = CppRLM(
            signature,
            workspace=workspace,
            rlm_config=rlm_config,
            sub_lm=sub_lm,
            writer=writer,
            allow_writes=allow_writes,
            allow_build=allow_build,
        )
        return module, reason

    reason = (
        f"ChainOfThought: payload {payload_chars} chars < threshold {rlm_config.threshold_chars}"
    )
    module = dspy.ChainOfThought(signature) if use_chain_of_thought else dspy.Predict(signature)
    return module, reason


def build_sub_lm(sub_model: Optional[ModelConfig], require_key: bool = True) -> Optional[Any]:
    """Construct the RLM's inner LM from config, if one is configured."""
    if sub_model is None:
        return None
    from agent.llm.lm_factory import build_lm

    return build_lm(sub_model, require_key=require_key)
