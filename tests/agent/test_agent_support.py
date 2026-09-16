"""The parsing, comparison, gold, execution and fix-loop layers.

These run entirely offline and without a model: they are the parts of the code
stages whose failure modes are silent (a mis-split workload, a mismatch reported
in the wrong place, a fix loop that never terminates), so they are tested
directly rather than only through a stage.
"""

import pytest

from agent.stages.execution import QueryRunner, render_command
from agent.stages.fixing import compile_verdict, fix_loop, no_progress_reason
from agent.stages.gold import GoldProvider
from agent.stages.parsing import ParseError, extract_code, extract_json, parse_workload
from agent.stages.results import compare_tables, parse_table


class TestWorkloadParsing:
    def test_ids_come_from_query_headers(self):
        queries = parse_workload("-- Q1: revenue\nSELECT 1;\n\n-- Q2: orders\nSELECT 2;\n")
        assert [q.id for q in queries] == ["q1", "q2"]
        assert queries[0].label.startswith("-- Q1")

    def test_a_trailing_statement_without_a_semicolon_is_kept(self):
        """The shipped example workload ends this way."""
        queries = parse_workload("SELECT 1;\n-- Q9: last\nSELECT 9\n")
        assert [q.id for q in queries] == ["q1", "q9"]
        assert queries[1].text.endswith(";")

    def test_a_semicolon_inside_a_string_does_not_split(self):
        queries = parse_workload("SELECT 'a;b' AS x FROM t;\n")
        assert len(queries) == 1
        assert "'a;b'" in queries[0].text

    def test_a_semicolon_inside_a_comment_does_not_split(self):
        queries = parse_workload("-- note; not a split\nSELECT 1;\n")
        assert len(queries) == 1

    def test_doubled_quotes_inside_a_literal(self):
        queries = parse_workload("SELECT 'it''s; fine' FROM t;\n")
        assert len(queries) == 1

    def test_block_comments_are_not_split_points(self):
        queries = parse_workload("/* a; b */ SELECT 1;\n")
        assert len(queries) == 1

    def test_comment_only_trailer_is_not_a_query(self):
        queries = parse_workload("SELECT 1;\n-- nothing follows\n")
        assert len(queries) == 1

    def test_duplicate_ids_are_disambiguated_not_collapsed(self):
        """Two queries sharing a gold file would be a correctness hazard."""
        queries = parse_workload("-- Q1 a\nSELECT 1;\n-- Q1 b\nSELECT 2;\n")
        assert [q.id for q in queries] == ["q1", "q1_2"]

    def test_unlabelled_queries_are_numbered_in_file_order(self):
        queries = parse_workload("SELECT 1;\nSELECT 2;\nSELECT 3;\n")
        assert [q.id for q in queries] == ["q1", "q2", "q3"]

    def test_slug_is_filesystem_safe(self):
        queries = parse_workload("-- query 12\nSELECT 1;\n")
        assert queries[0].id == "q12"
        assert queries[0].slug == "q12"

    def test_the_shipped_example_workload_parses(self):
        from pathlib import Path

        root = Path(__file__).resolve().parents[2]
        text = (root / "agent/examples/input/allqueries.txt").read_text(encoding="utf-8")
        assert [q.id for q in parse_workload(text)] == ["q1", "q2", "q3"]


class TestExtractCode:
    def test_a_fenced_block_loses_its_fence(self):
        assert extract_code("```cpp\nint main() {}\n```").strip() == "int main() {}"

    def test_unfenced_output_is_returned_as_is(self):
        assert extract_code("int main() {}").strip() == "int main() {}"

    def test_a_fence_after_prose_still_yields_the_code(self):
        text = "Here you go:\n```cpp\nint x = 1;\n```\nHope that helps."
        assert extract_code(text).strip() == "int x = 1;"

    def test_empty_stays_empty(self):
        assert extract_code("") == ""


class TestExtractJson:
    def test_plain_json(self):
        assert extract_json('{"a": 1}') == {"a": 1}

    def test_fenced_json(self):
        assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}

    def test_json_surrounded_by_prose(self):
        assert extract_json('Sure! {"a": {"b": 2}} Let me know.') == {"a": {"b": 2}}

    def test_a_brace_inside_a_string_does_not_end_the_scan(self):
        assert extract_json('{"a": "} not the end"}') == {"a": "} not the end"}

    def test_empty_output_names_the_field(self):
        with pytest.raises(ParseError, match="empty schema_levels"):
            extract_json("   ", what="schema_levels")

    def test_unparseable_output_shows_what_it_saw(self):
        with pytest.raises(ParseError, match="no JSON object"):
            extract_json("definitely not json", what="levels")


class TestComparison:
    def test_identical_tables_match(self):
        table = parse_table("a,b\n1,2\n")
        assert compare_tables(table, table).matched

    def test_float_drift_within_tolerance_matches(self):
        """A DECIMAL sum accumulated in another order differs in the last digit."""
        expected = parse_table("1,100.00\n")
        actual = parse_table("1,100.0000001\n")
        assert compare_tables(expected, actual, float_tolerance=1e-6).matched

    def test_float_drift_beyond_tolerance_does_not(self):
        expected = parse_table("1,100.00\n")
        actual = parse_table("1,100.01\n")
        assert not compare_tables(expected, actual, float_tolerance=1e-6).matched

    def test_an_off_by_one_integer_is_a_mismatch(self):
        result = compare_tables(parse_table("5\n"), parse_table("6\n"))
        assert not result.matched
        assert result.expected == "5" and result.actual == "6"

    def test_the_first_difference_is_located(self):
        """A model can act on "row 2, column rev"; it cannot act on "wrong"."""
        expected = parse_table("seg,rev\nA,1\nB,2\nC,3\n")
        actual = parse_table("A,1\nB,9\nC,3\n")
        result = compare_tables(expected, actual, header_mode="gold")
        assert (result.row, result.column, result.column_name) == (2, 2, "rev")
        assert "row 2" in result.brief()
        assert result.expected == "2" and result.actual == "9"

    def test_row_count_difference_is_reported_before_cells(self):
        result = compare_tables(parse_table("1\n2\n"), parse_table("1\n"))
        assert "row count differs" in result.reason

    def test_column_count_difference_names_the_row(self):
        result = compare_tables(parse_table("1,2\n"), parse_table("1,2,3\n"))
        assert "column count differs on row 1" in result.reason

    def test_header_mode_gold_drops_only_the_expected_header(self):
        """The real case: DuckDB gold has a header, the engine prints bare rows."""
        gold = parse_table("segment,revenue\nA,1\n")
        engine = parse_table("A,1\n")
        assert compare_tables(gold, engine, header_mode="gold").matched
        assert not compare_tables(gold, engine, header_mode="none").matched

    def test_header_mode_both_drops_both_headers(self):
        gold = parse_table("segment,revenue\nA,1\n")
        engine = parse_table("col0,col1\nA,1\n")
        assert compare_tables(gold, engine, header_mode="both").matched

    def test_an_unknown_header_mode_is_rejected(self):
        with pytest.raises(ValueError, match="header_mode must be one of"):
            compare_tables(parse_table("1\n"), parse_table("1\n"), header_mode="maybe")

    def test_a_row_count_mismatch_shows_the_differing_row(self):
        result = compare_tables(parse_table("A,1\nB,2\n"), parse_table("A,1\n"))
        assert "row count differs" in result.reason
        assert result.row is None or result.expected is not None

    def test_sort_rows_compares_as_multisets(self):
        expected = parse_table("A,1\nB,2\n")
        actual = parse_table("B,2\nA,1\n")
        assert not compare_tables(expected, actual).matched
        assert compare_tables(expected, actual, sort_rows=True).matched

    def test_char_padding_is_forgiven(self):
        assert compare_tables(parse_table("BUILDING\n"), parse_table("BUILDING   \n")).matched

    def test_null_spellings_agree(self):
        assert compare_tables(parse_table("1,NULL\n"), parse_table("1,\n")).matched

    def test_quoted_delimiters_survive(self):
        table = parse_table('"Smith, John",1\n')
        assert table.rows == [["Smith, John", "1"]]


class TestCommandRendering:
    def test_a_value_with_spaces_stays_one_argument(self):
        argv = render_command("run.py --sql {query_text}", query_text="SELECT a, b FROM t;")
        assert argv == ["run.py", "--sql", "SELECT a, b FROM t;"]

    def test_an_unused_optional_flag_is_dropped(self):
        argv = render_command("./e {trace} --out {output}", drop_empty=True, trace="", output="o")
        assert argv == ["./e", "--out", "o"]

    def test_an_unknown_placeholder_lists_what_is_available(self):
        with pytest.raises(Exception, match="unknown placeholder"):
            render_command("./e {nope}", query_id="q1")


class TestQueryRunner:
    @pytest.fixture
    def project(self, tmp_path):
        (tmp_path / "rows.csv").write_text("A,1\nB,2\n", encoding="utf-8")
        return tmp_path

    def test_output_is_read_from_the_output_file(self, project):
        runner = QueryRunner("cp rows.csv {output}", project_root=project)
        outcome = runner.run(_query("q1"))
        assert outcome.ok
        assert outcome.output == "A,1\nB,2\n"

    def test_output_is_read_from_stdout_when_the_template_has_no_output(self, project):
        runner = QueryRunner("cat rows.csv", project_root=project)
        assert runner.run(_query("q1")).output == "A,1\nB,2\n"

    def test_a_nonzero_exit_is_a_failure_with_its_stderr(self, project):
        runner = QueryRunner("cat missing.csv", project_root=project)
        outcome = runner.run(_query("q1"))
        assert not outcome.ok
        assert "FAILED" in outcome.brief()

    def test_a_missing_binary_says_so_instead_of_raising(self, project):
        outcome = QueryRunner("./not-a-binary", project_root=project).run(_query("q1"))
        assert not outcome.ok
        assert "Is the project built?" in outcome.stderr

    def test_the_engines_own_runtime_is_preferred_to_wall_clock(self, project):
        runner = QueryRunner(
            "echo RUNTIME 0.25", project_root=project, runtime_pattern=r"RUNTIME ([0-9.]+)"
        )
        outcome = runner.run(_query("q1"))
        assert outcome.reported_s == 0.25
        assert outcome.runtime_s == 0.25

    def test_measure_reports_a_median_of_repeats(self, project):
        runner = QueryRunner(
            "echo RUNTIME 2.0", project_root=project, runtime_pattern=r"RUNTIME ([0-9.]+)"
        )
        measurement = runner.measure(_query("q1"), repeats=3)
        assert measurement.ok and measurement.samples == [2.0, 2.0, 2.0]
        assert measurement.median_s == 2.0

    def test_measure_fails_loudly_rather_than_timing_a_broken_run(self, project):
        measurement = QueryRunner("false", project_root=project).measure(_query("q1"), repeats=3)
        assert not measurement.ok and measurement.error

    def test_no_template_means_unavailable(self, project):
        runner = QueryRunner(None, project_root=project)
        assert not runner.available()
        assert "run_command" in runner.unavailable_reason()


class TestGoldProvider:
    def test_mode_none_requires_the_file_to_exist(self, tmp_path):
        from agent.config.models import GoldConfig

        provider = GoldProvider(GoldConfig(mode="none", dir=tmp_path / "gold"))
        result = provider.ensure([_query("q1")])
        assert not result.ok
        assert "must already exist" in result.files["q1"].error

    def test_mode_none_accepts_an_existing_file(self, tmp_path):
        from agent.config.models import GoldConfig

        gold_dir = tmp_path / "gold"
        gold_dir.mkdir()
        (gold_dir / "q1.csv").write_text("A,1\n", encoding="utf-8")
        result = GoldProvider(GoldConfig(mode="none", dir=gold_dir)).ensure([_query("q1")])
        assert result.ok and not result.files["q1"].generated

    def test_mode_command_writes_the_gold_file(self, tmp_path):
        from agent.config.models import GoldConfig

        cfg = GoldConfig(
            mode="command",
            dir=tmp_path / "gold",
            command="sh -c 'printf \"A,1\\n\" > {gold_output}'",
        )
        result = GoldProvider(cfg).ensure([_query("q1")])
        assert result.ok, result.files["q1"].error
        assert result.files["q1"].path.read_text() == "A,1\n"
        assert result.files["q1"].generated

    def test_an_existing_file_is_not_regenerated_unless_overwrite(self, tmp_path):
        """Regenerating gold silently is how a wrong engine starts passing."""
        from agent.config.models import GoldConfig

        gold_dir = tmp_path / "gold"
        gold_dir.mkdir()
        (gold_dir / "q1.csv").write_text("ORIGINAL\n", encoding="utf-8")
        cfg = GoldConfig(mode="command", dir=gold_dir, command="sh -c 'printf NEW > {gold_output}'")
        GoldProvider(cfg).ensure([_query("q1")])
        assert (gold_dir / "q1.csv").read_text() == "ORIGINAL\n"

        cfg.overwrite = True
        GoldProvider(cfg).ensure([_query("q1")])
        assert (gold_dir / "q1.csv").read_text() == "NEW"

    def test_a_failing_command_is_recorded_per_query(self, tmp_path):
        from agent.config.models import GoldConfig

        cfg = GoldConfig(mode="command", dir=tmp_path / "gold", command="false")
        result = GoldProvider(cfg).ensure([_query("q1"), _query("q2")])
        assert not result.ok
        assert result.missing() == ["q1", "q2"]
        assert "gold command failed" in result.files["q1"].error

    def test_an_unconfigured_directory_names_the_config_key(self, tmp_path):
        from agent.config.models import GoldConfig
        from agent.stages.gold import GoldError

        with pytest.raises(GoldError, match="common.gold.dir"):
            GoldProvider(GoldConfig(mode="none")).ensure([_query("q1")])


class TestFixLoop:
    def test_content_that_verifies_first_time_makes_no_repairs(self):
        applied = []
        outcome = fix_loop(
            content="good",
            apply=applied.append,
            verify=lambda: (True, "OK"),
            repair=lambda current, report: pytest.fail("repair must not be called"),
            max_rounds=2,
        )
        assert outcome.ok and outcome.repair_rounds == 0
        assert applied == ["good"]

    def test_a_repair_that_works_is_recorded_as_one_round(self):
        state = {"ok": False}

        def verify():
            return (state["ok"], "diagnostics")

        def repair(current, report):
            state["ok"] = True
            return "fixed"

        outcome = fix_loop("broken", lambda c: None, verify, repair, max_rounds=2)
        assert outcome.ok and outcome.repair_rounds == 1
        assert outcome.content == "fixed"

    def test_the_budget_is_enforced(self):
        calls = []
        outcome = fix_loop(
            "broken",
            lambda c: None,
            lambda: (False, "still broken"),
            lambda current, report: calls.append(1) or f"attempt{len(calls)}",
            max_rounds=2,
        )
        assert not outcome.ok
        assert outcome.stopped == "budget exhausted"
        assert len(calls) == 2

    def test_identical_content_stops_the_loop(self):
        outcome = fix_loop(
            "same",
            lambda c: None,
            lambda: (False, "broken"),
            lambda current, report: "same",
            max_rounds=5,
        )
        assert outcome.stopped == "no progress"

    def test_empty_repair_stops_the_loop(self):
        outcome = fix_loop(
            "x", lambda c: None, lambda: (False, "broken"), lambda c, r: "  ", max_rounds=5
        )
        assert outcome.stopped == "empty repair"

    def test_a_raising_repair_keeps_the_last_real_verdict(self):
        def repair(current, report):
            raise RuntimeError("model exploded")

        outcome = fix_loop("x", lambda c: None, lambda: (False, "diag"), repair, max_rounds=3)
        assert not outcome.ok
        assert "model exploded" in outcome.stopped
        assert outcome.last_report == "diag"

    def test_no_progress_reason(self):
        assert no_progress_reason("a", "a")
        assert no_progress_reason("a", "")
        assert no_progress_reason("a", "b") is None


class TestCompileVerdict:
    def test_a_missing_compiler_is_not_a_code_error(self):
        """Otherwise the loop rewrites correct code until the budget runs out."""
        from agent.rlm.compile import CompileResult

        result = CompileResult(ok=False, compiler_missing=True, stderr="no g++")
        assert compile_verdict(result)[0] is True
        assert compile_verdict(result, allow_missing_compiler=False)[0] is False

    def test_a_real_failure_reports_the_diagnostics(self):
        from agent.rlm.compile import CompileResult, Diagnostic

        result = CompileResult(
            ok=False,
            returncode=1,
            diagnostics=[Diagnostic(file="a.cpp", line=3, message="expected ';'")],
        )
        ok, report = compile_verdict(result)
        assert ok is False
        assert "expected ';'" in report


def _query(query_id: str):
    from agent.stages.parsing import Query

    return Query(id=query_id, text=f"SELECT '{query_id}';")
