"""RLM integration: a cpplib-backed C++ workspace, tools and module."""

from agent.rlm.compile import (
    CompileResult,
    Diagnostic,
    build_cmake_project,
    compile_snippet,
    compile_source_file,
    parse_diagnostics,
    resolve_compiler,
)
from agent.rlm.cpp_tools import make_cpp_tools, tool_names
from agent.rlm.workspace import CppWorkspace, FileEdit, PatchResult

__all__ = [
    "CompileResult",
    "CppWorkspace",
    "Diagnostic",
    "FileEdit",
    "PatchResult",
    "build_cmake_project",
    "compile_snippet",
    "compile_source_file",
    "make_cpp_tools",
    "parse_diagnostics",
    "resolve_compiler",
    "tool_names",
]


def __getattr__(name: str):
    """Expose the dspy-dependent members lazily.

    Importing ``agent.rlm`` must not require dspy, so ``CppRLM`` and friends are
    resolved on first access instead of at import time.
    """
    if name in ("CppRLM", "build_predictor", "build_sub_lm", "deno_available", "preflight"):
        from agent.rlm import cpp_module

        return getattr(cpp_module, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
