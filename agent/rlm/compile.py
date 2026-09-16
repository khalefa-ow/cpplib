"""Compiling generated C++ and reporting the result in a structured form.

cpplib's validator returns bare ``Tuple[bool, str]``, supports no include dirs
or extra flags, and its ``check_syntax`` both ignores the configured C++
standard and can return ``None`` for its success flag when no compiler is
installed — so ``if not ok`` reads "no compiler" as "syntax error". A code-fixing
loop cannot work against that, so compilation is invoked directly here and the
result is parsed into diagnostics the model can act on.

``CMakeValidator`` is still reused for whole-project builds, wrapped by
:func:`build_cmake_project`, because that part of it works.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Literal, Optional, Sequence

from pydantic import BaseModel, ConfigDict, Field

Severity = Literal["error", "warning", "note"]

# gcc/clang: "file.cpp:12:5: error: message" (column optional)
_DIAG_RE = re.compile(
    r"^(?P<file>[^\s:][^:]*):(?P<line>\d+)(?::(?P<col>\d+))?:\s*"
    r"(?P<severity>error|warning|note|fatal error):\s*(?P<message>.*)$"
)


class Diagnostic(BaseModel):
    """One compiler diagnostic."""

    model_config = ConfigDict(extra="forbid")

    file: Optional[str] = None
    line: Optional[int] = None
    column: Optional[int] = None
    severity: Severity = "error"
    message: str = ""

    def format(self) -> str:
        location = self.file or "<unknown>"
        if self.line is not None:
            location += f":{self.line}"
            if self.column is not None:
                location += f":{self.column}"
        return f"{location}: {self.severity}: {self.message}"


class CompileResult(BaseModel):
    """The outcome of a compile or build, in a form a fix loop can branch on.

    ``ok`` is always a real bool. A missing compiler sets ``compiler_missing``
    instead of being conflated with a compile failure, because the two need
    completely different responses: install a toolchain, versus fix the code.
    """

    model_config = ConfigDict(extra="forbid")

    ok: bool
    command: list[str] = Field(default_factory=list)
    returncode: Optional[int] = None
    stdout: str = ""
    stderr: str = ""
    diagnostics: list[Diagnostic] = Field(default_factory=list)
    duration_s: float = 0.0
    timed_out: bool = False
    compiler_missing: bool = False
    target: Optional[str] = None

    @property
    def errors(self) -> list[Diagnostic]:
        return [d for d in self.diagnostics if d.severity == "error"]

    @property
    def warnings(self) -> list[Diagnostic]:
        return [d for d in self.diagnostics if d.severity == "warning"]

    def brief(self, max_diags: int = 10) -> str:
        """A compact rendering for an LLM prompt.

        Raw compiler output is repetitive and long; the first handful of errors
        is what actually drives a fix, and templates can emit hundreds of notes
        for a single mistake.
        """
        if self.compiler_missing:
            return f"COMPILER NOT FOUND: {self.stderr.strip() or 'no C++ compiler available'}"
        if self.timed_out:
            return f"TIMED OUT after {self.duration_s:.1f}s running: {' '.join(self.command)}"
        if self.ok:
            warn = f" ({len(self.warnings)} warning(s))" if self.warnings else ""
            return f"OK{warn}"

        lines = [f"FAILED (exit {self.returncode})"]
        errors = self.errors or self.diagnostics
        for diag in errors[:max_diags]:
            lines.append("  " + diag.format())
        if len(errors) > max_diags:
            lines.append(f"  ... {len(errors) - max_diags} more diagnostic(s)")
        if not self.diagnostics:
            # Linker errors and cmake failures have no file:line form, so fall
            # back to raw output rather than reporting nothing.
            tail = (self.stderr or self.stdout).strip().splitlines()[-20:]
            lines.extend("  " + line for line in tail)
        return "\n".join(lines)

    def __str__(self) -> str:
        return self.brief()


def parse_diagnostics(output: str) -> list[Diagnostic]:
    """Extract diagnostics from compiler output.

    Deduplicates by (file, line, column, severity, message): gcc repeats the
    same diagnostic once per template instantiation, which would otherwise
    flood a fix prompt with identical lines.
    """
    seen: set[tuple[Any, ...]] = set()
    out: list[Diagnostic] = []
    for line in output.splitlines():
        match = _DIAG_RE.match(line.strip())
        if not match:
            continue
        severity = match.group("severity")
        diag = Diagnostic(
            file=match.group("file"),
            line=int(match.group("line")),
            column=int(match.group("col")) if match.group("col") else None,
            severity="error" if severity == "fatal error" else severity,  # type: ignore[arg-type]
            message=match.group("message").strip(),
        )
        identity = (diag.file, diag.line, diag.column, diag.severity, diag.message)
        if identity in seen:
            continue
        seen.add(identity)
        out.append(diag)
    return out


def resolve_compiler(preferred: str = "g++") -> Optional[str]:
    """Find a usable C++ compiler, preferring the configured one."""
    for candidate in (preferred, "g++", "clang++", "c++"):
        if candidate and shutil.which(candidate):
            return candidate
    return None


def build_command(
    source: Path,
    compiler: str = "g++",
    cpp_standard: str = "c++20",
    include_dirs: Sequence[Path] = (),
    extra_flags: Sequence[str] = (),
    syntax_only: bool = True,
    output: Optional[Path] = None,
) -> list[str]:
    """Assemble the compiler invocation.

    ``-std=`` is always passed. cpplib's ``check_syntax`` omits it, so a C++20
    source is checked at the compiler's default standard and fails on syntax
    that is perfectly legal for the project.
    """
    cmd = [compiler, f"-std={cpp_standard}"]
    for include in include_dirs:
        cmd.extend(["-I", str(include)])
    cmd.extend(extra_flags)
    if syntax_only:
        cmd.append("-fsyntax-only")
    elif output is not None:
        cmd.extend(["-o", str(output)])
    cmd.append(str(source))
    return cmd


def compile_source_file(
    source: Path,
    compiler: str = "g++",
    cpp_standard: str = "c++20",
    include_dirs: Sequence[Path] = (),
    extra_flags: Sequence[str] = (),
    syntax_only: bool = True,
    timeout_s: int = 300,
    output: Optional[Path] = None,
) -> CompileResult:
    """Compile one file on disk and return a structured result."""
    resolved = resolve_compiler(compiler)
    if resolved is None:
        return CompileResult(
            ok=False,
            compiler_missing=True,
            stderr=f"No C++ compiler found (tried {compiler}, g++, clang++, c++).",
            target=str(source),
        )
    if not source.exists():
        return CompileResult(
            ok=False,
            stderr=f"Source file does not exist: {source}",
            target=str(source),
        )

    cmd = build_command(
        source,
        compiler=resolved,
        cpp_standard=cpp_standard,
        include_dirs=include_dirs,
        extra_flags=extra_flags,
        syntax_only=syntax_only,
        output=output,
    )
    started = time.perf_counter()
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            cwd=str(source.parent),
        )
    except subprocess.TimeoutExpired as exc:
        return CompileResult(
            ok=False,
            command=cmd,
            timed_out=True,
            duration_s=time.perf_counter() - started,
            stdout=_as_text(exc.stdout),
            stderr=_as_text(exc.stderr) or f"Timed out after {timeout_s}s.",
            target=str(source),
        )

    combined = (proc.stderr or "") + "\n" + (proc.stdout or "")
    return CompileResult(
        ok=proc.returncode == 0,
        command=cmd,
        returncode=proc.returncode,
        stdout=proc.stdout or "",
        stderr=proc.stderr or "",
        diagnostics=parse_diagnostics(combined),
        duration_s=time.perf_counter() - started,
        target=str(source),
    )


def compile_snippet(
    code: str,
    compiler: str = "g++",
    cpp_standard: str = "c++20",
    include_dirs: Sequence[Path] = (),
    extra_flags: Sequence[str] = (),
    timeout_s: int = 60,
    suffix: str = ".cpp",
) -> CompileResult:
    """Syntax-check a code string via a temporary file.

    The replacement for ``CMakeValidator.check_syntax``: it honours the C++
    standard, accepts include dirs, and always returns a real bool.
    """
    tmp_dir = Path(tempfile.mkdtemp(prefix="agent_snippet_"))
    source = tmp_dir / f"snippet{suffix}"
    try:
        source.write_text(code, encoding="utf-8")
        result = compile_source_file(
            source,
            compiler=compiler,
            cpp_standard=cpp_standard,
            include_dirs=include_dirs,
            extra_flags=extra_flags,
            syntax_only=True,
            timeout_s=timeout_s,
        )
        result.target = "<snippet>"
        return result
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def build_cmake_project(
    cmake_dir: Path,
    build_dir: Optional[Path] = None,
    cpp_standard: str = "c++20",
    clean: bool = False,
    timeout_s: int = 600,
    jobs: Optional[int] = None,
) -> CompileResult:
    """Configure and build a CMake project, returning a structured result.

    Invoked directly rather than through ``CMakeValidator`` so that the build
    directory, parallelism and timeout are all configurable, and so that
    cleaning is an ordinary ``shutil.rmtree`` of a validated path rather than a
    ``subprocess`` call to ``rm -rf``.
    """
    cmake_dir = Path(cmake_dir)
    if not cmake_dir.exists():
        return CompileResult(ok=False, stderr=f"CMake directory not found: {cmake_dir}")
    if not (cmake_dir / "CMakeLists.txt").exists():
        return CompileResult(ok=False, stderr=f"No CMakeLists.txt in {cmake_dir}")
    if shutil.which("cmake") is None:
        return CompileResult(
            ok=False,
            compiler_missing=True,
            stderr="cmake was not found on PATH.",
        )

    build = Path(build_dir) if build_dir else cmake_dir / "build"
    started = time.perf_counter()

    if clean and build.exists():
        # Guard before deleting: an unvalidated build_dir from a config file
        # would otherwise be an arbitrary recursive delete.
        if build.resolve() == cmake_dir.resolve() or not _is_within(build, cmake_dir.parent):
            return CompileResult(
                ok=False,
                stderr=(
                    f"Refusing to clean {build}: it must be a subdirectory of "
                    f"{cmake_dir.parent} and not the project root itself."
                ),
            )
        shutil.rmtree(build, ignore_errors=True)
    build.mkdir(parents=True, exist_ok=True)

    std_num = cpp_standard.lower().replace("c++", "").replace("gnu++", "")
    configure_cmd = [
        "cmake",
        f"-DCMAKE_CXX_STANDARD={std_num}",
        "-DCMAKE_CXX_STANDARD_REQUIRED=ON",
        "-DCMAKE_EXPORT_COMPILE_COMMANDS=ON",
        str(cmake_dir),
    ]
    configure = _run(configure_cmd, cwd=build, timeout_s=timeout_s, started=started)
    if not configure.ok:
        return configure

    build_cmd = ["cmake", "--build", "."]
    if jobs:
        build_cmd.extend(["--parallel", str(jobs)])
    result = _run(build_cmd, cwd=build, timeout_s=timeout_s, started=started)
    result.target = str(cmake_dir)
    # Surface configure output too; a warning there often explains a build error.
    result.stdout = configure.stdout + "\n" + result.stdout
    return result


def _run(cmd: list[str], cwd: Path, timeout_s: int, started: float) -> CompileResult:
    """Run a build subprocess and convert it into a CompileResult."""
    try:
        proc = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, timeout=timeout_s)
    except subprocess.TimeoutExpired as exc:
        return CompileResult(
            ok=False,
            command=cmd,
            timed_out=True,
            duration_s=time.perf_counter() - started,
            stdout=_as_text(exc.stdout),
            stderr=_as_text(exc.stderr) or f"Timed out after {timeout_s}s.",
        )
    combined = (proc.stderr or "") + "\n" + (proc.stdout or "")
    return CompileResult(
        ok=proc.returncode == 0,
        command=cmd,
        returncode=proc.returncode,
        stdout=proc.stdout or "",
        stderr=proc.stderr or "",
        diagnostics=parse_diagnostics(combined),
        duration_s=time.perf_counter() - started,
    )


def _as_text(value: Any) -> str:
    """Normalize subprocess output that may be bytes, str or None."""
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _is_within(path: Path, parent: Path) -> bool:
    """True when ``path`` is inside ``parent``."""
    try:
        Path(path).resolve().relative_to(Path(parent).resolve())
        return True
    except ValueError:
        return False
