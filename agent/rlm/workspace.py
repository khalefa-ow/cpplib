"""A safe, agent-friendly adapter over cpplib.

cpplib is a good parser with several sharp edges that an autonomous edit/compile
loop would hit immediately. Each is handled here, and each mitigation is
deliberate:

``FileModifier`` queues edits and every mutator re-reads the file *from disk*, so
two queued edits to one file silently lose the first when the queue is applied.
Mitigation: never hold more than one queued edit — apply and clear immediately —
and for whole-file or patch-shaped output (which is what an LLM produces) bypass
``FileModifier`` entirely.

``FileIndex``/``CPPCodebase`` have no invalidation, so after a write the AST,
file contents, symbol table and dependency graph are all stale. Mitigation:
every mutating method marks the index dirty and the ``CPPCodebase`` is rebuilt
lazily on the next read, so a burst of edits costs one re-index rather than one
per edit.

``FileModifier._find_function_start`` is ``content.find(f"{name}(")``, which
matches call sites, comments, strings and substring names (``add(`` inside
``quickadd(``). Mitigation: ``replace_function`` splices by the AST-derived line
range from ``Symbol.line_start``/``line_end``.

``CPPCodebase`` indexes the entire tree eagerly with no exclusion patterns, so
pointing it at a project root means parsing everything under ``build/`` too.
Mitigation: the workspace indexes a configurable source subdirectory, and
filters index results by exclusion patterns.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence

from pydantic import BaseModel, ConfigDict, Field

from agent.errors import PatchError, UnsafePathError, WorkspaceError
from agent.rlm.compile import (
    CompileResult,
    build_cmake_project,
    compile_snippet,
    compile_source_file,
)

CPP_SUFFIXES = (".cpp", ".cc", ".cxx", ".c", ".h", ".hpp", ".hxx", ".hh", ".ipp", ".inl")
DEFAULT_EXCLUDES = (
    "build",
    ".git",
    ".cache",
    "cmake-build-debug",
    "cmake-build-release",
    "__pycache__",
)


class FileEdit(BaseModel):
    """A recorded modification, kept so a stage can report what it changed."""

    model_config = ConfigDict(extra="forbid")

    path: str
    action: str  # "create" | "update" | "delete"
    bytes_before: int = 0
    bytes_after: int = 0


class PatchResult(BaseModel):
    """The outcome of applying an apply_patch block."""

    model_config = ConfigDict(extra="forbid")

    ok: bool
    edits: list[FileEdit] = Field(default_factory=list)
    error: Optional[str] = None

    def summary(self) -> str:
        if not self.ok:
            return f"PATCH FAILED: {self.error}"
        if not self.edits:
            return "PATCH OK: no changes"
        parts = [f"{e.action} {e.path}" for e in self.edits]
        return "PATCH OK: " + ", ".join(parts)


class CppWorkspace:
    """A C++ source tree the agent may read, edit and compile.

    Args:
        root: The directory to index and confine all edits to.
        source_dir: Subdirectory to hand to ``CPPCodebase``. Defaults to
            ``root/src`` when it exists, else ``root``. Keeps ``build/`` out of
            the eager index.
        cmake_dir: Project root for CMake builds. Defaults to ``root``.
        compile_config: A :class:`agent.config.models.CompileConfig`.
        excludes: Directory names to keep out of listings.
    """

    def __init__(
        self,
        root: str | Path,
        source_dir: Optional[str | Path] = None,
        cmake_dir: Optional[str | Path] = None,
        compile_config: Any = None,
        excludes: Sequence[str] = DEFAULT_EXCLUDES,
    ):
        self.root = Path(root).expanduser().resolve()
        if not self.root.exists():
            self.root.mkdir(parents=True, exist_ok=True)
        if not self.root.is_dir():
            raise WorkspaceError(f"Workspace root is not a directory: {self.root}")

        if source_dir is not None:
            self.source_dir = self._resolve_inside(source_dir)
        else:
            default_src = self.root / "src"
            self.source_dir = default_src if default_src.is_dir() else self.root

        self.cmake_dir = self._resolve_inside(cmake_dir) if cmake_dir else self.root
        self.excludes = tuple(excludes)

        if compile_config is None:
            from agent.config.models import CompileConfig

            compile_config = CompileConfig()
        self.compile_config = compile_config

        self._codebase: Optional[Any] = None
        self._dirty = True
        self.edits: list[FileEdit] = []

    # --- path safety ------------------------------------------------------

    def _resolve_inside(self, candidate: str | Path) -> Path:
        """Resolve a path and require it to stay inside the workspace root.

        The RLM's tools run on the host with real filesystem access, so this is
        the security boundary, not a convenience check. ``..`` segments and
        absolute paths from model output both land here.
        """
        path = Path(candidate).expanduser()
        if not path.is_absolute():
            path = self.root / path
        resolved = Path(path).resolve()
        try:
            resolved.relative_to(self.root)
        except ValueError:
            raise UnsafePathError(candidate, self.root) from None
        return resolved

    def safe_path(self, candidate: str | Path) -> Path:
        """Public alias for the path guard, used by the tool wrappers."""
        return self._resolve_inside(candidate)

    def rel(self, path: str | Path) -> str:
        """Workspace-relative form of a path, for display."""
        try:
            return str(Path(path).resolve().relative_to(self.root))
        except ValueError:
            return str(path)

    def _excluded(self, path: Path) -> bool:
        return any(part in self.excludes for part in path.parts)

    # --- index management -------------------------------------------------

    def invalidate(self) -> None:
        """Mark the parsed index stale.

        Lazy on purpose: cpplib re-parses the whole tree on construction, so
        rebuilding eagerly after each write would make a fix loop quadratic.
        """
        self._dirty = True

    @property
    def codebase(self) -> Any:
        """The ``CPPCodebase``, rebuilt if a write has happened since last use."""
        if self._codebase is None or self._dirty:
            self._codebase = self._build_codebase()
            self._dirty = False
        return self._codebase

    def _build_codebase(self) -> Any:
        from cpplib.codebase import CPPCodebase
        from cpplib.validator.cpp_standard import CppStandard

        standard = CppStandard.from_string(self.compile_config.cpp_standard) or CppStandard.CXX20
        return CPPCodebase(
            root_dir=str(self.source_dir),
            cmake_dir=str(self.cmake_dir),
            cpp_standard=standard,
        )

    # --- reading ----------------------------------------------------------

    def list_files(self) -> list[Path]:
        """Every C++ source file in the workspace, excluding build dirs."""
        out: list[Path] = []
        for path in sorted(self.root.rglob("*")):
            if (
                path.is_file()
                and path.suffix in CPP_SUFFIXES
                and not self._excluded(path.relative_to(self.root))
            ):
                out.append(path)
        return out

    def read_file(self, rel_path: str | Path) -> str:
        """Full text of one file."""
        path = self._resolve_inside(rel_path)
        if not path.exists():
            raise WorkspaceError(f"File not found in workspace: {self.rel(path)}")
        return path.read_text(encoding="utf-8", errors="replace")

    def read_lines(self, rel_path: str | Path, start: int, end: int) -> str:
        """A 1-indexed, inclusive line range, prefixed with line numbers.

        Bounded reads are the point of using RLM here: the model inspects the
        few lines a diagnostic names instead of loading the file into context.
        """
        lines = self.read_file(rel_path).splitlines()
        first = max(1, int(start))
        last = min(len(lines), int(end))
        if first > last:
            return ""
        width = len(str(last))
        return "\n".join(f"{n:>{width}}\t{lines[n - 1]}" for n in range(first, last + 1))

    def symbols(self, kind: Optional[str] = None) -> list[Any]:
        """Indexed symbols, optionally filtered to 'function' or 'class'."""
        codebase = self.codebase
        # list(...) rather than a bare return: cpplib is untyped, so the values
        # arrive as Any and would silently widen this method's return type.
        if kind in ("function", "functions"):
            return list(codebase.get_all_functions())
        if kind in ("class", "classes"):
            return list(codebase.get_all_classes())
        return list(codebase.get_all_functions()) + list(codebase.get_all_classes())

    def get_symbol(self, name: str) -> Optional[Any]:
        """Look up one symbol by name (short or qualified)."""
        return self.codebase.get_symbol(name)

    def extract_function(self, name: str) -> Optional[Any]:
        """A function as a ``CodePiece``, or None."""
        return self.codebase.extract_function(name)

    def extract_class(self, name: str) -> Optional[Any]:
        """A class as a ``CodePiece``, or None."""
        return self.codebase.extract_class(name)

    def dependencies(self, name: str) -> dict[str, list[str]]:
        """Typed dependencies of a symbol.

        Uses ``DependencyCollector`` directly rather than
        ``CPPCodebase.extract_*``, which flattens the five dependency categories
        into one untyped list and loses the distinction between an include, a
        type and a call.
        """
        collector = self.codebase.dependency_collector
        symbol = self.get_symbol(name)
        kind = getattr(symbol, "kind", None)
        if kind in ("class", "struct"):
            raw = collector.collect_class_dependencies(name)
        else:
            raw = collector.collect_function_dependencies(name)
        return {key: sorted(value) for key, value in raw.items()}

    def analysis_summary(self, compact: bool = True) -> str:
        """Token-efficient overview via cpplib's ``AnalysisReporter``."""
        from cpplib.reporter.text_reporter import AnalysisReporter

        return AnalysisReporter(self.codebase, compact=compact).full_analysis()

    def symbol_details(self, name: str, compact: bool = True) -> str:
        """Token-efficient detail for one symbol via ``AnalysisReporter``."""
        from cpplib.reporter.text_reporter import AnalysisReporter

        return AnalysisReporter(self.codebase, compact=compact).symbol_details(name)

    def grep(self, pattern: str, max_hits: int = 50) -> list[tuple[str, int, str]]:
        """Regex search over workspace sources; returns (rel_path, line, text)."""
        try:
            regex = re.compile(pattern)
        except re.error as exc:
            raise WorkspaceError(f"Invalid regex {pattern!r}: {exc}") from exc
        hits: list[tuple[str, int, str]] = []
        for path in self.list_files():
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for number, line in enumerate(text.splitlines(), start=1):
                if regex.search(line):
                    hits.append((self.rel(path), number, line.rstrip()))
                    if len(hits) >= max_hits:
                        return hits
        return hits

    # --- writing ----------------------------------------------------------

    def write_file(self, rel_path: str | Path, content: str) -> FileEdit:
        """Create or overwrite a file, then invalidate the index.

        Bypasses ``FileModifier`` deliberately. LLM output is whole files or
        patches rather than surgical AST inserts, so a direct write is both
        simpler and immune to the queue-clobbering bug.
        """
        path = self._resolve_inside(rel_path)
        existed = path.exists()
        before = path.stat().st_size if existed else 0
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        edit = FileEdit(
            path=self.rel(path),
            action="update" if existed else "create",
            bytes_before=before,
            bytes_after=len(content.encode("utf-8")),
        )
        self.edits.append(edit)
        self.invalidate()
        return edit

    def delete_file(self, rel_path: str | Path) -> FileEdit:
        """Delete a file inside the workspace."""
        path = self._resolve_inside(rel_path)
        if not path.exists():
            raise WorkspaceError(f"Cannot delete missing file: {self.rel(path)}")
        before = path.stat().st_size
        path.unlink()
        edit = FileEdit(path=self.rel(path), action="delete", bytes_before=before)
        self.edits.append(edit)
        self.invalidate()
        return edit

    def replace_function(self, rel_path: str | Path, name: str, new_text: str) -> FileEdit:
        """Replace a function's definition, located by its AST line range.

        Deliberately does not use ``FileModifier.replace_function``, whose
        ``content.find(f"{name}(")`` would match a call site or a comment before
        the definition. ``FunctionExtractor`` gives the real node's line range.
        """
        path = self._resolve_inside(rel_path)
        content = self.read_file(path)

        from cpplib.parser.cpp_parser import CPPParser
        from cpplib.parser.function_extractor import FunctionExtractor

        extractor = FunctionExtractor(CPPParser())
        piece = extractor.extract_function_by_name(path, content, name)
        if piece is None or piece.line_start is None or piece.line_end is None:
            raise WorkspaceError(
                f"Function '{name}' was not found in {self.rel(path)}. "
                f"Use list_symbols to see what the file defines."
            )

        lines = content.splitlines(keepends=True)
        # cpplib line numbers are 1-indexed and inclusive.
        head = lines[: piece.line_start - 1]
        tail = lines[piece.line_end :]
        replacement = new_text if new_text.endswith("\n") else new_text + "\n"
        updated = "".join(head) + replacement + "".join(tail)
        return self.write_file(path, updated)

    def apply_patch(self, patch: str) -> PatchResult:
        """Apply an ``apply_patch``-format block.

        Supports the format the workflow's prompts specify::

            *** Begin Patch
            *** Add File: path/to/new.cpp
            +line one
            +line two
            *** Update File: path/to/existing.hpp
            +replacement content
            *** Delete File: path/to/old.cpp
            *** End Patch

        ``Update File`` replaces the file's whole content with the ``+`` lines,
        which matches how the generation prompts are written (they emit complete
        files, not context diffs). A context-diff dialect would need a hunk
        matcher; that is intentionally out of scope here.

        Always returns a :class:`PatchResult`; it never raises. This is called
        as a model-facing tool, and a structured failure the model can read and
        retry against is far more useful than a traceback.
        """
        try:
            edits = self._parse_and_apply_patch(patch)
        except WorkspaceError as exc:
            # Covers PatchError and UnsafePathError alike.
            return PatchResult(ok=False, error=str(exc))
        return PatchResult(ok=True, edits=edits)

    def _parse_and_apply_patch(self, patch: str) -> list[FileEdit]:
        text = patch.strip()
        if not text:
            raise PatchError("Patch is empty.")
        # Tolerate a stray markdown fence even though the prompts forbid one.
        if text.startswith("```"):
            text = "\n".join(line for line in text.splitlines() if not line.startswith("```"))
            text = text.strip()
        if not text.startswith("*** Begin Patch"):
            raise PatchError("Patch must start with '*** Begin Patch'.")
        if "*** End Patch" not in text:
            raise PatchError("Patch must end with '*** End Patch'.")

        body = text.split("*** Begin Patch", 1)[1].split("*** End Patch", 1)[0]

        actions: list[tuple[str, str, list[str]]] = []
        current: Optional[tuple[str, str, list[str]]] = None
        for line in body.splitlines():
            stripped = line.strip()
            for marker, action in (
                ("*** Add File:", "create"),
                ("*** Update File:", "update"),
                ("*** Delete File:", "delete"),
            ):
                if stripped.startswith(marker):
                    if current is not None:
                        actions.append(current)
                    current = (action, stripped[len(marker) :].strip(), [])
                    break
            else:
                if stripped.startswith("*** "):
                    raise PatchError(
                        f"Unsupported patch directive: {stripped!r}. Use only "
                        f"'*** Add File:', '*** Update File:' and '*** Delete File:'."
                    )
                if current is not None:
                    current[2].append(line)
        if current is not None:
            actions.append(current)

        if not actions:
            raise PatchError("Patch contained no file sections.")

        # Validate every path and target before touching the filesystem. A
        # multi-file patch whose third section escapes the workspace must not
        # leave the first two applied — a half-applied patch is harder for the
        # model to recover from than a rejected one.
        for action, rel_path, _lines in actions:
            if not rel_path:
                raise PatchError(f"A '{action}' section has no file path.")
            path = self._resolve_inside(rel_path)
            if action == "delete" and not path.exists():
                raise PatchError(f"Cannot delete missing file: {rel_path}")

        edits: list[FileEdit] = []
        for action, rel_path, lines in actions:
            if action == "delete":
                edits.append(self.delete_file(rel_path))
                continue
            content = self._patch_body_to_content(lines)
            edits.append(self.write_file(rel_path, content))
        return edits

    @staticmethod
    def _patch_body_to_content(lines: Iterable[str]) -> str:
        """Strip apply_patch line prefixes to recover the file content."""
        out: list[str] = []
        for line in lines:
            if line.startswith("+"):
                out.append(line[1:])
            elif line.startswith("-"):
                # A removal line in a whole-file section: nothing to keep.
                continue
            elif line.startswith(" "):
                out.append(line[1:])
            elif not line.strip():
                out.append("")
            else:
                # Unprefixed content: accept it rather than losing the body.
                out.append(line)
        text = "\n".join(out).strip("\n")
        return text + "\n" if text else ""

    # --- compiling --------------------------------------------------------

    def compile_file(
        self,
        rel_path: str | Path,
        syntax_only: bool = True,
        extra_flags: Optional[Sequence[str]] = None,
    ) -> CompileResult:
        """Compile one workspace file with the configured standard and flags.

        ``extra_flags`` appends to (not replaces) ``compile_config.extra_flags``
        for this one call, e.g. Arrow/Parquet's pkg-config flags when compiling
        a loader file that ``#include``s them but no other file in the tree does.
        """
        path = self._resolve_inside(rel_path)
        cfg = self.compile_config
        include_dirs = list(cfg.include_dirs) + [self.source_dir, self.root]
        result = compile_source_file(
            path,
            compiler=cfg.compiler,
            cpp_standard=cfg.cpp_standard,
            include_dirs=include_dirs,
            extra_flags=list(cfg.extra_flags) + list(extra_flags or []),
            syntax_only=syntax_only,
            timeout_s=cfg.timeout_s,
        )
        return self._relativize(result)

    def _relativize(self, result: CompileResult) -> CompileResult:
        """Rewrite diagnostic paths to workspace-relative form.

        The model reads a diagnostic and then calls ``read_lines`` on the file it
        names, so the path it sees has to be one the tools accept. Absolute
        temp-dir paths also leak the sandbox layout into the prompt for no gain.
        """
        for diag in result.diagnostics:
            if diag.file:
                diag.file = self.rel(diag.file)
        if result.target:
            result.target = self.rel(result.target)
        return result

    def compile_snippet(self, code: str) -> CompileResult:
        """Syntax-check a code string without writing it into the workspace."""
        cfg = self.compile_config
        return compile_snippet(
            code,
            compiler=cfg.compiler,
            cpp_standard=cfg.cpp_standard,
            include_dirs=list(cfg.include_dirs) + [self.source_dir, self.root],
            extra_flags=cfg.extra_flags,
            timeout_s=min(cfg.timeout_s, 120),
        )

    def has_cmake_project(self) -> bool:
        """Whether :meth:`build_project` has anything to build.

        Checked by the stages before building: a tree that was never set up for
        CMake would otherwise fail every query on a configuration error that has
        nothing to do with the generated code, and send the fix loop chasing it.
        """
        cmake_dir = self.compile_config.cmake_dir or self.cmake_dir
        return (Path(cmake_dir) / "CMakeLists.txt").exists()

    def build_project(self) -> CompileResult:
        """Configure and build the whole CMake project."""
        cfg = self.compile_config
        return build_cmake_project(
            cmake_dir=cfg.cmake_dir or self.cmake_dir,
            build_dir=cfg.build_dir,
            cpp_standard=cfg.cpp_standard,
            clean=cfg.clean_build,
            timeout_s=cfg.timeout_s,
        )

    # --- housekeeping -----------------------------------------------------

    def snapshot(self, dest: str | Path) -> Path:
        """Copy the workspace aside, for a revert-on-regression loop."""
        target = Path(dest).expanduser().resolve()
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(
            self.root,
            target,
            ignore=shutil.ignore_patterns(*self.excludes),
        )
        return target

    def restore(self, source: str | Path) -> None:
        """Restore a snapshot taken by :meth:`snapshot`."""
        src = Path(source).expanduser().resolve()
        if not src.is_dir():
            raise WorkspaceError(f"Snapshot directory not found: {src}")
        for path in self.root.iterdir():
            if path.name in self.excludes:
                continue
            if path.is_dir():
                shutil.rmtree(path, ignore_errors=True)
            else:
                path.unlink(missing_ok=True)
        for path in src.iterdir():
            dest = self.root / path.name
            if path.is_dir():
                shutil.copytree(path, dest)
            else:
                shutil.copy2(path, dest)
        self.invalidate()

    def changed_files(self) -> list[str]:
        """Distinct files this workspace has edited, in first-touch order."""
        return list(dict.fromkeys(edit.path for edit in self.edits))
