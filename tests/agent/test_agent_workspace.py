"""CppWorkspace, compilation and the RLM tool callables.

Includes explicit regression tests for the cpplib defects the workspace works
around. Those tests are the contract: if a future refactor routes edits back
through ``FileModifier`` or drops the lazy re-index, they fail.
"""

import shutil

import pytest

from agent.config.models import CompileConfig
from agent.errors import UnsafePathError, WorkspaceError
from agent.rlm.compile import (
    CompileResult,
    build_command,
    compile_snippet,
    parse_diagnostics,
    resolve_compiler,
)
from agent.rlm.cpp_tools import make_cpp_tools, tool_names
from agent.rlm.workspace import CppWorkspace
from agent.trace.callbacks import TraceWriter
from agent.trace.span import run_context

needs_gpp = pytest.mark.skipif(resolve_compiler("g++") is None, reason="no C++ compiler")


class TestIndexing:
    def test_source_dir_defaults_to_src(self, workspace, cpp_tree):
        assert workspace.source_dir == (cpp_tree / "src").resolve()

    def test_build_dir_is_excluded(self, workspace):
        """cpplib's FileIndex has no exclusions and would parse build/."""
        listed = [workspace.rel(p) for p in workspace.list_files()]
        assert "src/engine.cpp" in listed
        assert not any("build" in path for path in listed)

    def test_symbols_are_discovered(self, workspace):
        names = {s.name for s in workspace.symbols()}
        assert {"add", "quickadd", "Table"} <= names

    def test_symbols_filter_by_kind(self, workspace):
        functions = {s.name for s in workspace.symbols("function")}
        classes = {s.name for s in workspace.symbols("class")}
        assert "add" in functions
        assert "Table" in classes
        assert "Table" not in functions

    def test_typed_dependencies(self, workspace):
        deps = workspace.dependencies("quickadd")
        # Categories are preserved, unlike CPPCodebase.extract_* which flattens.
        assert set(deps) == {"includes", "types", "functions", "classes", "forward_decls"}


class TestReading:
    def test_read_lines_is_bounded_and_numbered(self, workspace):
        text = workspace.read_lines("src/engine.cpp", 6, 8)
        lines = text.splitlines()
        assert len(lines) == 3
        assert "int add(int a, int b)" in lines[0]
        assert lines[0].startswith("6\t")

    def test_read_lines_clamps_out_of_range(self, workspace):
        assert workspace.read_lines("src/engine.cpp", 1000, 2000) == ""

    def test_extract_function_returns_only_that_function(self, workspace):
        piece = workspace.extract_function("add")
        assert piece is not None
        assert "return a + b;" in piece.body
        assert "quickadd" not in piece.body

    def test_missing_file_raises(self, workspace):
        with pytest.raises(WorkspaceError, match="File not found"):
            workspace.read_file("src/nope.cpp")

    def test_grep_returns_locations(self, workspace):
        hits = workspace.grep(r"\breturn\b", max_hits=10)
        assert hits
        assert all(len(hit) == 3 for hit in hits)
        assert hits[0][0] == "src/engine.cpp"

    def test_grep_respects_max_hits(self, workspace):
        assert len(workspace.grep(r".", max_hits=3)) == 3

    def test_invalid_regex_raises_workspace_error(self, workspace):
        with pytest.raises(WorkspaceError, match="Invalid regex"):
            workspace.grep("[unclosed")


class TestPathGuard:
    """These tools run on the host, so the guard is a security boundary."""

    @pytest.mark.parametrize(
        "candidate",
        ["../escape.cpp", "/etc/passwd", "src/../../outside.cpp", "../../../../etc/shadow"],
    )
    def test_escaping_paths_are_rejected(self, workspace, candidate):
        with pytest.raises(UnsafePathError):
            workspace.safe_path(candidate)

    def test_paths_inside_the_root_are_accepted(self, workspace):
        assert workspace.safe_path("src/engine.cpp").exists()
        assert workspace.safe_path("new/nested/file.cpp").name == "file.cpp"


class TestWriteRegressions:
    def test_new_symbol_is_visible_after_a_write(self, workspace):
        """Regression: cpplib has no index invalidation.

        Without CppWorkspace.invalidate(), the CPPCodebase built before the
        write keeps its stale AST and symbol table forever.
        """
        before = {s.name for s in workspace.symbols()}
        assert "brand_new" not in before

        workspace.write_file("src/added.cpp", "int brand_new() { return 42; }\n")

        after = {s.name for s in workspace.symbols()}
        assert "brand_new" in after

    def test_sequential_edits_to_one_file_all_survive(self, workspace):
        """Regression: FileModifier's queued edits clobber each other.

        Each of its mutators re-reads the file from disk, so two queued edits to
        one file lose the first when the queue is applied.
        """
        workspace.write_file("src/multi.cpp", "int one() { return 1; }\n")
        workspace.replace_function("src/multi.cpp", "one", "int one() { return 111; }")
        current = workspace.read_file("src/multi.cpp")
        workspace.write_file("src/multi.cpp", current + "int two() { return 2; }\n")

        final = workspace.read_file("src/multi.cpp")
        assert "return 111;" in final
        assert "int two()" in final

    def test_replace_function_ignores_decoy_text(self, workspace):
        """Regression: FileModifier locates a function with content.find(f"{name}(").

        That matches call sites, comments and substring names. The fixture has a
        comment mentioning "add(" before the real definition, plus a
        ``quickadd`` whose name contains ``add``.
        """
        workspace.replace_function(
            "src/engine.cpp", "add", "int add(int a, int b) {\n    return a + b + 1000;\n}"
        )
        text = workspace.read_file("src/engine.cpp")

        assert "return a + b + 1000;" in text
        assert "a decoy mentioning add( inside a comment" in text
        assert "return add(a, 1);" in text  # quickadd body untouched
        assert text.count("int quickadd(int a)") == 1

    def test_replace_unknown_function_raises_with_guidance(self, workspace):
        with pytest.raises(WorkspaceError, match="list_symbols"):
            workspace.replace_function("src/engine.cpp", "no_such_fn", "void x() {}")

    def test_edits_are_recorded(self, workspace):
        workspace.write_file("src/a.cpp", "int a();\n")
        workspace.write_file("src/a.cpp", "int a(); int b();\n")
        workspace.write_file("src/b.cpp", "int c();\n")
        assert workspace.changed_files() == ["src/a.cpp", "src/b.cpp"]
        assert [e.action for e in workspace.edits] == ["create", "update", "create"]

    def test_delete_file(self, workspace):
        workspace.write_file("src/gone.cpp", "int x();\n")
        workspace.delete_file("src/gone.cpp")
        assert not (workspace.root / "src" / "gone.cpp").exists()
        with pytest.raises(WorkspaceError):
            workspace.delete_file("src/gone.cpp")


class TestApplyPatch:
    def test_add_and_update(self, workspace):
        result = workspace.apply_patch(
            "*** Begin Patch\n"
            "*** Add File: src/loader_impl.hpp\n"
            "+#pragma once\n"
            "+void load();\n"
            "*** Update File: src/engine.cpp\n"
            "+int only_thing() { return 0; }\n"
            "*** End Patch"
        )
        assert result.ok, result.error
        assert workspace.read_file("src/loader_impl.hpp") == "#pragma once\nvoid load();\n"
        assert workspace.read_file("src/engine.cpp") == "int only_thing() { return 0; }\n"

    def test_delete_section(self, workspace):
        workspace.write_file("src/temp.cpp", "int t();\n")
        result = workspace.apply_patch(
            "*** Begin Patch\n*** Delete File: src/temp.cpp\n*** End Patch"
        )
        assert result.ok
        assert not (workspace.root / "src" / "temp.cpp").exists()

    def test_tolerates_a_markdown_fence(self, workspace):
        result = workspace.apply_patch(
            "```\n*** Begin Patch\n*** Add File: src/x.cpp\n+int x();\n*** End Patch\n```"
        )
        assert result.ok

    @pytest.mark.parametrize(
        "patch,reason",
        [
            ("", "empty"),
            ("no markers at all", "Begin Patch"),
            ("*** Begin Patch\n*** Add File: a.cpp\n+x\n", "End Patch"),
            ("*** Begin Patch\ndiff --git a/x b/x\n*** End Patch", "no file sections"),
            ("*** Begin Patch\n*** Move File: a.cpp\n*** End Patch", "Unsupported patch directive"),
        ],
    )
    def test_malformed_patches_are_reported_not_raised(self, workspace, patch, reason):
        result = workspace.apply_patch(patch)
        assert result.ok is False
        assert reason.lower() in (result.error or "").lower()

    def test_escaping_path_returns_a_result_not_an_exception(self, workspace):
        """apply_patch is a model-facing tool; it must never raise."""
        result = workspace.apply_patch(
            "*** Begin Patch\n*** Add File: ../../evil.cpp\n+pwned\n*** End Patch"
        )
        assert result.ok is False
        assert "outside the workspace root" in (result.error or "")
        assert not (workspace.root.parent / "evil.cpp").exists()

    def test_a_rejected_patch_applies_nothing(self, workspace):
        """Partial application is harder to recover from than rejection."""
        before = sorted(p.name for p in (workspace.root / "src").iterdir())
        result = workspace.apply_patch(
            "*** Begin Patch\n"
            "*** Add File: src/good.cpp\n"
            "+int good();\n"
            "*** Add File: ../../evil.cpp\n"
            "+pwned\n"
            "*** End Patch"
        )
        assert result.ok is False
        assert sorted(p.name for p in (workspace.root / "src").iterdir()) == before

    def test_delete_of_a_missing_file_rejects_the_whole_patch(self, workspace):
        result = workspace.apply_patch(
            "*** Begin Patch\n"
            "*** Add File: src/new.cpp\n"
            "+int n();\n"
            "*** Delete File: src/absent.cpp\n"
            "*** End Patch"
        )
        assert result.ok is False
        assert not (workspace.root / "src" / "new.cpp").exists()


class TestDiagnosticParsing:
    def test_parses_gcc_format(self):
        output = "src/a.cpp:12:5: error: 'x' was not declared in this scope\n"
        diags = parse_diagnostics(output)
        assert len(diags) == 1
        assert diags[0].file == "src/a.cpp"
        assert diags[0].line == 12
        assert diags[0].column == 5
        assert diags[0].severity == "error"

    def test_parses_without_a_column(self):
        diags = parse_diagnostics("src/a.cpp:3: warning: unused variable\n")
        assert diags[0].line == 3 and diags[0].column is None
        assert diags[0].severity == "warning"

    def test_fatal_error_is_normalized_to_error(self):
        diags = parse_diagnostics("a.cpp:1:1: fatal error: x.h: No such file\n")
        assert diags[0].severity == "error"

    def test_deduplicates_repeated_diagnostics(self):
        """gcc repeats a diagnostic per template instantiation."""
        line = "a.cpp:1:1: error: same thing\n"
        assert len(parse_diagnostics(line * 5)) == 1

    def test_ignores_unrelated_output(self):
        assert parse_diagnostics("Building target foo\n[ 50%] linking\n") == []


class TestCompileCommand:
    def test_always_passes_the_std_flag(self, tmp_path):
        """cpplib's check_syntax omits -std=, so C++20 code fails spuriously."""
        cmd = build_command(tmp_path / "a.cpp", cpp_standard="c++20")
        assert "-std=c++20" in cmd

    def test_include_dirs_and_extra_flags_are_passed(self, tmp_path):
        cmd = build_command(
            tmp_path / "a.cpp",
            include_dirs=[tmp_path / "inc"],
            extra_flags=["-O2", "-Wall"],
        )
        assert "-I" in cmd and str(tmp_path / "inc") in cmd
        assert "-O2" in cmd and "-Wall" in cmd


@needs_gpp
class TestCompiling:
    def test_valid_file_compiles(self, workspace):
        result = workspace.compile_file("src/engine.cpp")
        assert result.ok, result.brief()
        assert result.brief().startswith("OK")

    def test_broken_file_reports_diagnostics(self, workspace):
        workspace.write_file("src/broken.cpp", "int f() { return undefined_thing; }\n")
        result = workspace.compile_file("src/broken.cpp")
        assert result.ok is False
        assert isinstance(result.ok, bool)
        assert result.errors
        assert "undefined_thing" in result.brief()

    def test_diagnostic_paths_are_workspace_relative(self, workspace):
        """The model feeds these paths straight back into read_lines."""
        workspace.write_file("src/broken.cpp", "int f() { return nope; }\n")
        result = workspace.compile_file("src/broken.cpp")
        assert result.diagnostics[0].file == "src/broken.cpp"

    def test_cpp20_syntax_needs_the_std_flag(self, workspace):
        """Proves -std= is actually reaching the compiler."""
        code = "#include <concepts>\ntemplate <std::integral T> T twice(T v) { return v * 2; }\n"
        ok20 = compile_snippet(code, cpp_standard="c++20")
        old = compile_snippet(code, cpp_standard="c++14")
        assert ok20.ok, ok20.brief()
        assert old.ok is False

    def test_missing_source_is_not_reported_as_a_compiler_problem(self, workspace):
        result = workspace.compile_file("src/absent.cpp")
        assert result.ok is False
        assert result.compiler_missing is False
        assert "does not exist" in result.stderr

    def test_missing_compiler_sets_its_own_flag(self, workspace, monkeypatch):
        """cpplib's check_syntax returns None here, so `if not ok` misreads it."""
        monkeypatch.setattr(shutil, "which", lambda name: None)
        result = workspace.compile_file("src/engine.cpp")
        assert result.ok is False
        assert isinstance(result.ok, bool)
        assert result.compiler_missing is True
        assert "COMPILER NOT FOUND" in result.brief()

    def test_brief_caps_the_diagnostic_count(self):
        result = CompileResult(
            ok=False,
            returncode=1,
            diagnostics=parse_diagnostics(
                "".join(f"a.cpp:{n}:1: error: problem {n}\n" for n in range(1, 31))
            ),
        )
        text = result.brief(max_diags=5)
        assert "25 more diagnostic(s)" in text

    def test_brief_falls_back_to_raw_output(self):
        """Linker and cmake failures have no file:line form."""
        result = CompileResult(ok=False, returncode=1, stderr="undefined reference to `main'")
        assert "undefined reference" in result.brief()


class TestSnapshotRestore:
    def test_restore_undoes_edits(self, workspace, tmp_path):
        """What makes the optimize prompts' "or remove your changes" enforceable."""
        original = workspace.read_file("src/engine.cpp")
        snapshot = workspace.snapshot(tmp_path / "snap")

        workspace.write_file("src/engine.cpp", "int replaced() { return 0; }\n")
        workspace.write_file("src/extra.cpp", "int extra();\n")
        assert workspace.read_file("src/engine.cpp") != original

        workspace.restore(snapshot)
        assert workspace.read_file("src/engine.cpp") == original
        assert not (workspace.root / "src" / "extra.cpp").exists()

    def test_restore_from_a_missing_snapshot_raises(self, workspace, tmp_path):
        with pytest.raises(WorkspaceError, match="Snapshot directory not found"):
            workspace.restore(tmp_path / "nope")


class TestTools:
    def test_full_tool_set(self, workspace):
        names = tool_names(make_cpp_tools(workspace))
        for expected in (
            "codebase_summary",
            "read_function",
            "grep_code",
            "read_lines",
            "write_file",
            "apply_patch",
            "compile_file",
            "build_project",
        ):
            assert expected in names

    def test_read_only_set_excludes_mutation(self, workspace):
        names = tool_names(make_cpp_tools(workspace, allow_writes=False, allow_build=False))
        for forbidden in (
            "write_file",
            "apply_patch",
            "delete_file",
            "compile_file",
            "build_project",
            "replace_function",
        ):
            assert forbidden not in names
        assert "read_function" in names

    def test_run_query_is_absent_unless_wired(self, workspace):
        assert "run_query_tool" not in tool_names(make_cpp_tools(workspace))
        with_runner = make_cpp_tools(workspace, run_query=lambda qid, params: f"ran {qid}")
        assert "run_query_tool" in tool_names(with_runner)

    def test_tools_have_docstrings_and_annotations(self, workspace):
        """DSPy introspects both to describe each tool to the model."""
        for tool in make_cpp_tools(workspace):
            assert tool.__doc__, tool.__name__
            assert tool.__annotations__, tool.__name__

    def test_tools_return_strings_not_exceptions(self, workspace):
        """An exception crossing the sandbox bridge aborts the RLM iteration."""
        tools = {t.__name__: t for t in make_cpp_tools(workspace)}
        assert "not found" in tools["read_function"]("no_such_function")
        assert tools["grep_code"]("[unclosed").startswith("ERROR")
        assert tools["read_lines"]("../../etc/passwd", 1, 2).startswith("ERROR")
        assert tools["write_file"]("../evil.cpp", "x").startswith("ERROR")

    def test_tool_output_is_truncated(self, workspace):
        from agent.rlm.cpp_tools import MAX_TOOL_CHARS

        workspace.write_file("src/huge.cpp", "// filler\n" * 40000)
        tools = {t.__name__: t for t in make_cpp_tools(workspace)}
        out = tools["read_lines"]("src/huge.cpp", 1, 40000)
        assert len(out) <= MAX_TOOL_CHARS + 200
        assert "truncated" in out

    def test_tool_calls_are_traced(self, workspace):
        writer = TraceWriter()
        tools = {t.__name__: t for t in make_cpp_tools(workspace, writer=writer)}
        with run_context(stage="demo"):
            tools["list_symbols"]("all")
            tools["read_function"]("add")
        assert [r["name"] for r in writer.events(event="cpp_tool")] == [
            "list_symbols",
            "read_function",
        ]

    def test_read_class_renders_the_definition(self, workspace):
        tools = {t.__name__: t for t in make_cpp_tools(workspace)}
        out = tools["read_class"]("Table")
        assert "std::vector<int> ids" in out
        assert "src/engine.cpp" in out


class TestWorkspaceConstruction:
    def test_falls_back_to_root_when_there_is_no_src(self, tmp_path):
        root = tmp_path / "flat"
        root.mkdir()
        (root / "main.cpp").write_text("int main() { return 0; }\n", encoding="utf-8")
        ws = CppWorkspace(root, compile_config=CompileConfig())
        assert ws.source_dir == root.resolve()
        assert {s.name for s in ws.symbols()} == {"main"}

    def test_creates_a_missing_root(self, tmp_path):
        ws = CppWorkspace(tmp_path / "fresh", compile_config=CompileConfig())
        assert ws.root.is_dir()
        assert ws.list_files() == []
