"""cpplib-backed tools exposed to the RLM sandbox.

``dspy.RLM`` runs the model's code in a Deno-hosted Pyodide sandbox that has no
filesystem, network or subprocess access. Anything the model needs to do to the
C++ tree therefore has to arrive as a host-side Python callable passed via
``tools=[...]``; DSPy introspects each one's type hints and docstring to describe
it to the model.

Two consequences shape this module:

1. **These run on the host.** Every path argument goes through
   :meth:`CppWorkspace.safe_path`, and no general shell or subprocess tool is
   exposed — only the configured compiler and, if enabled, one run command.

2. **Every tool returns a string and never raises.** An exception crossing the
   sandbox bridge aborts the RLM iteration; an error *message* lets the model
   read what went wrong and try something else. So failures are formatted, not
   thrown.

The read tools are deliberately granular — one function, one class, a line
range, a grep hit — because the whole point of using RLM here is that a model
inspects the few lines it needs instead of loading files into the prompt.
"""

from __future__ import annotations

import functools
from typing import Any, Callable, Optional

from agent.rlm.workspace import CppWorkspace
from agent.trace.callbacks import TraceWriter
from agent.trace.span import current_run_id, current_span_id, current_stage, new_span

# Keeps a single tool's return value from blowing up the model's context.
MAX_TOOL_CHARS = 20_000


def _truncate(text: str, limit: int = MAX_TOOL_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n… output truncated at {limit} chars ({len(text)} total)"


def _traced(
    name: str,
    writer: Optional[TraceWriter] = None,
) -> Callable[[Callable[..., str]], Callable[..., str]]:
    """Wrap a tool so it is traced and can never raise across the bridge."""

    def decorate(fn: Callable[..., str]) -> Callable[..., str]:
        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> str:
            with new_span("cpp_tool", name) as span:
                try:
                    result = _truncate(fn(*args, **kwargs))
                    error = None
                except Exception as exc:
                    # Reported to the model, not raised: it can correct itself.
                    result = f"ERROR ({type(exc).__name__}): {exc}"
                    error = result
                if writer is not None:
                    writer.write(
                        {
                            "event": "cpp_tool",
                            "kind": "tool",
                            "name": name,
                            "run_id": current_run_id(),
                            "stage": current_stage(),
                            "span_id": span.span_id,
                            "parent_span_id": span.parent_span_id or current_span_id(),
                            "inputs": {"args": list(args), "kwargs": kwargs},
                            "outputs": result,
                            "duration_ms": span.duration_ms(),
                            "error": error,
                        }
                    )
                return result

        return wrapper

    return decorate


def _auto_build_note(ws: CppWorkspace, writer: Optional[TraceWriter]) -> str:
    """Build the whole project after an edit and report the outcome.

    Traced the same way a tool call is, so it shows up as its own step rather
    than being folded silently into the write tool's record.
    """
    with new_span("cpp_tool", "auto_build") as span:
        result = ws.build_project()
        if writer is not None:
            writer.write(
                {
                    "event": "cpp_tool",
                    "kind": "tool",
                    "name": "auto_build",
                    "run_id": current_run_id(),
                    "stage": current_stage(),
                    "span_id": span.span_id,
                    "parent_span_id": span.parent_span_id or current_span_id(),
                    "inputs": {},
                    "outputs": result.brief(),
                    "duration_ms": span.duration_ms(),
                    "error": None if result.ok else result.brief(),
                }
            )
    return f"\n[auto-build] {result.brief()}"


def make_cpp_tools(
    ws: CppWorkspace,
    writer: Optional[TraceWriter] = None,
    allow_writes: bool = True,
    allow_build: bool = True,
    run_query: Optional[Callable[[str, str], str]] = None,
    auto_build: bool = False,
) -> list[Callable[..., str]]:
    """Build the tool list for a :class:`CppWorkspace`.

    Args:
        ws: The workspace the tools operate on.
        writer: Optional trace writer; each call is recorded as a span.
        allow_writes: Expose the mutating tools. Turn off for a read-only
            analysis stage such as storage planning, so the model cannot edit
            code it was only asked to reason about.
        allow_build: Expose ``compile_file`` / ``build_project``.
        run_query: Optional ``(query_id, params) -> output`` callable. Left None,
            no execution tool is exposed at all.
        auto_build: When both writes and building are allowed, run
            ``build_project()`` automatically after every successful mutating
            tool call and append its result. Off by default so it never doubles
            up with a stage's own explicit compile loop.

    Returns:
        Plain callables suitable for ``dspy.RLM(tools=...)``.
    """
    auto_build = auto_build and allow_writes and allow_build

    # --- reading ----------------------------------------------------------

    @_traced("codebase_summary", writer)
    def codebase_summary() -> str:
        """Summarize the C++ codebase: files, functions, classes and statistics.

        Start here. It is far cheaper than reading files and usually tells you
        which symbol to look at next.
        """
        return ws.analysis_summary()

    @_traced("list_files", writer)
    def list_files() -> str:
        """List every C++ source and header file in the workspace, one per line."""
        files = ws.list_files()
        if not files:
            return "(no C++ files in workspace)"
        return "\n".join(ws.rel(p) for p in files)

    @_traced("list_symbols", writer)
    def list_symbols(kind: str = "all") -> str:
        """List declared symbols with their file and line range.

        Args:
            kind: "function", "class", or "all".
        """
        symbols = ws.symbols(None if kind == "all" else kind)
        if not symbols:
            return f"(no symbols of kind {kind!r})"
        lines = []
        for sym in symbols:
            location = f"{ws.rel(getattr(sym, 'file_path', '?'))}:{getattr(sym, 'line_start', '?')}"
            lines.append(
                f"{getattr(sym, 'kind', '?'):8s} {getattr(sym, 'name', '?'):40s} {location}"
            )
        return "\n".join(lines)

    @_traced("read_function", writer)
    def read_function(name: str) -> str:
        """Return the full source of one function, by name.

        Use this instead of reading a whole file when you only need one function.
        """
        piece = ws.extract_function(name)
        if piece is None:
            return f"Function {name!r} not found. Use list_symbols to see what exists."
        location = f"{ws.rel(piece.source_file or '?')}:{piece.line_start}-{piece.line_end}"
        return f"// {location}\n{piece.signature} {piece.body}"

    @_traced("read_class", writer)
    def read_class(name: str) -> str:
        """Return the full definition of one class or struct, by name."""
        piece = ws.extract_class(name)
        if piece is None:
            return f"Class {name!r} not found. Use list_symbols to see what exists."
        location = f"{ws.rel(piece.source_file or '?')}:{piece.line_start}-{piece.line_end}"
        # cpplib is asymmetric here: for a class, `body` usually holds the whole
        # definition and `signature` only the head, but when `body` starts with
        # '{' the two must be joined. Same test CodeGenerator.generate_class uses.
        if piece.body.strip().startswith("{"):
            definition = f"{piece.signature} {piece.body}"
        else:
            definition = piece.body
        return f"// {location}\n{definition}"

    @_traced("symbol_details", writer)
    def symbol_details(name: str) -> str:
        """Return a compact report about one symbol: kind, location and dependencies."""
        return ws.symbol_details(name)

    @_traced("get_dependencies", writer)
    def get_dependencies(name: str) -> str:
        """List what a function or class depends on.

        Grouped by category: includes, types, functions, classes and needed
        forward declarations.
        """
        deps = ws.dependencies(name)
        if not any(deps.values()):
            return f"{name}: no internal dependencies found."
        lines = [f"{name}:"]
        for category, values in deps.items():
            if values:
                lines.append(f"  {category}: {', '.join(values)}")
        return "\n".join(lines)

    @_traced("grep_code", writer)
    def grep_code(pattern: str, max_hits: int = 50) -> str:
        """Regex-search the C++ sources. Returns "path:line: text" per hit.

        Args:
            pattern: A Python regular expression.
            max_hits: Stop after this many matches.
        """
        hits = ws.grep(pattern, max_hits=max_hits)
        if not hits:
            return f"No matches for {pattern!r}."
        return "\n".join(f"{path}:{line}: {text}" for path, line, text in hits)

    @_traced("read_lines", writer)
    def read_lines(rel_path: str, start: int, end: int) -> str:
        """Read an inclusive, 1-indexed line range from one file, with line numbers.

        Prefer this over reading a whole file, e.g. to inspect the lines a
        compiler diagnostic points at.
        """
        text = ws.read_lines(rel_path, start, end)
        return text or f"(no lines in range {start}-{end} of {rel_path})"

    tools: list[Callable[..., str]] = [
        codebase_summary,
        list_files,
        list_symbols,
        read_function,
        read_class,
        symbol_details,
        get_dependencies,
        grep_code,
        read_lines,
    ]

    # --- writing ----------------------------------------------------------

    if allow_writes:

        @_traced("write_file", writer)
        def write_file(rel_path: str, content: str) -> str:
            """Create or overwrite a file in the workspace with the given content.

            Write the complete file; the previous content is replaced. Compile
            afterwards to check the result.
            """
            edit = ws.write_file(rel_path, content)
            report = f"{edit.action} {edit.path} ({edit.bytes_after} bytes)"
            if auto_build:
                report += _auto_build_note(ws, writer)
            return report

        @_traced("replace_function", writer)
        def replace_function(rel_path: str, name: str, new_text: str) -> str:
            """Replace one function's definition in a file, leaving the rest intact.

            Args:
                rel_path: File containing the function.
                name: Function name to replace.
                new_text: The complete new definition, signature and body.
            """
            edit = ws.replace_function(rel_path, name, new_text)
            report = f"replaced {name} in {edit.path} ({edit.bytes_after} bytes)"
            if auto_build:
                report += _auto_build_note(ws, writer)
            return report

        @_traced("apply_patch", writer)
        def apply_patch(patch: str) -> str:
            """Apply a patch block to the workspace.

            Format::

                *** Begin Patch
                *** Add File: path/to/new.cpp
                +file content, one '+' per line
                *** Update File: path/to/existing.hpp
                +complete replacement content
                *** Delete File: path/to/old.cpp
                *** End Patch

            Use only those three directives. Do not emit 'diff --git', '---' or
            '+++' headers, and do not wrap the patch in markdown fences. Paths
            are relative to the workspace root. Either the whole patch applies
            or none of it does.
            """
            patch_result = ws.apply_patch(patch)
            report = patch_result.summary()
            if auto_build and patch_result.ok:
                report += _auto_build_note(ws, writer)
            return report

        @_traced("delete_file", writer)
        def delete_file(rel_path: str) -> str:
            """Delete one file from the workspace."""
            edit = ws.delete_file(rel_path)
            report = f"deleted {edit.path}"
            if auto_build:
                report += _auto_build_note(ws, writer)
            return report

        tools.extend([write_file, replace_function, apply_patch, delete_file])

    # --- building ---------------------------------------------------------

    if allow_build:

        @_traced("compile_file", writer)
        def compile_file(rel_path: str) -> str:
            """Compile one file and return the diagnostics.

            Uses the project's configured compiler, C++ standard and include
            paths. Returns "OK" or the parsed errors with file and line numbers.
            """
            return ws.compile_file(rel_path).brief()

        @_traced("compile_snippet", writer)
        def compile_snippet(code: str) -> str:
            """Syntax-check a standalone C++ snippet without writing it to disk.

            Use this to validate an approach before editing a real file.
            """
            return ws.compile_snippet(code).brief()

        @_traced("build_project", writer)
        def build_project() -> str:
            """Configure and build the whole CMake project. Returns OK or the errors."""
            return ws.build_project().brief()

        tools.extend([compile_file, compile_snippet, build_project])

    # --- running ----------------------------------------------------------

    if run_query is not None:

        @_traced("run_query", writer)
        def run_query_tool(query_id: str, params: str = "") -> str:
            """Execute one built query and return its output.

            Args:
                query_id: Identifier of the query to run.
                params: Optional space-separated query parameters.
            """
            return run_query(query_id, params)

        tools.append(run_query_tool)

    return tools


def tool_names(tools: list[Callable[..., str]]) -> list[str]:
    """The names of a tool list, for logging and trace metadata."""
    return [getattr(t, "__name__", repr(t)) for t in tools]
