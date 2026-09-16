"""Prompt manifest building and strict rendering."""

import json

import pytest

from agent.errors import (
    InvalidTemplateError,
    MissingPlaceholderError,
    MissingPromptError,
    PromptError,
)
from agent.prompting.build_manifest import build_manifest, classify
from agent.prompting.manifest import scan_placeholders
from agent.prompting.registry import PromptRegistry


class TestScanPlaceholders:
    def test_detects_braced_form(self):
        names, problems = scan_placeholders("a ${one} b ${two}")
        assert names == ["one", "two"]
        assert problems == []

    def test_detects_bare_form(self):
        """optim_pretext_general.txt uses bare $name, not ${name}."""
        names, problems = scan_placeholders("queries at $queries_path with $num_queries")
        assert names == ["queries_path", "num_queries"]
        assert problems == []

    def test_deduplicates_preserving_order(self):
        names, _ = scan_placeholders("${b} ${a} ${b}")
        assert names == ["b", "a"]

    def test_escaped_dollar_is_not_a_placeholder(self):
        names, problems = scan_placeholders("costs $$5")
        assert names == []
        assert problems == []

    def test_invalid_dollar_is_reported_with_line_number(self):
        names, problems = scan_placeholders("line one\ncosts $5 today")
        assert names == []
        assert len(problems) == 1
        assert "line 2" in problems[0]


class TestBuildManifest:
    def test_builds_over_the_real_prompts(self, prompts_dir, tmp_path):
        manifest = build_manifest(prompts_dir, tmp_path / "m.json", strict=True)
        assert len(manifest.entries) >= 12
        assert "optim_w_trace" in manifest.entries
        assert "storage_plan_policy" in manifest.entries

    def test_records_placeholders_from_the_real_files(self, registry):
        entry = registry.get("optim_w_trace")
        assert "query_id" in entry.placeholders
        assert "constraints" in entry.placeholders

    def test_infers_composes_for_fragment_placeholders(self, registry):
        assert registry.get("optim_w_trace").composes["constraints"] == "optim_constraints"
        expert = registry.get("optim_w_expert_knowledge").composes
        assert expert["expert_knowledge"] == "expert_knowledge"

    def test_classify_assigns_stage_and_role(self):
        assert classify("optim_constraints") == ("optimize", "fragment")
        assert classify("optim_pretext_general") == ("optimize", "fragment")
        assert classify("expert_knowledge") == ("optimize", "knowledge")
        assert classify("optim_w_trace") == ("optimize", "task")
        assert classify("storage_plan_policy") == ("storage_plan", "task")
        assert classify("divide_policy") == ("divide", "task")
        assert classify("something_else") == (None, "task")

    def test_strict_build_fails_on_invalid_dollar(self, prompts_dir, tmp_path):
        (prompts_dir / "broken.txt").write_text("price is $5\n", encoding="utf-8")
        with pytest.raises(InvalidTemplateError):
            build_manifest(prompts_dir, tmp_path / "m.json", strict=True)

    def test_lax_build_tolerates_invalid_dollar(self, prompts_dir, tmp_path):
        (prompts_dir / "broken.txt").write_text("price is $5\n", encoding="utf-8")
        manifest = build_manifest(prompts_dir, tmp_path / "m.json", strict=False)
        assert "broken" in manifest.entries

    def test_preserves_curated_metadata_across_rebuilds(self, prompts_dir, tmp_path):
        path = tmp_path / "m.json"
        build_manifest(prompts_dir, path, strict=True)
        raw = json.loads(path.read_text(encoding="utf-8"))
        raw["entries"]["optim_w_trace"]["description"] = "hand written note"
        raw["entries"]["optim_w_trace"]["stage"] = "custom_stage"
        path.write_text(json.dumps(raw), encoding="utf-8")

        rebuilt = build_manifest(prompts_dir, path, strict=True)
        assert rebuilt.entries["optim_w_trace"].description == "hand written note"
        assert rebuilt.entries["optim_w_trace"].stage == "custom_stage"

    def test_version_bumps_only_when_content_changes(self, prompts_dir, tmp_path):
        path = tmp_path / "m.json"
        first = build_manifest(prompts_dir, path, strict=True)
        assert first.entries["optim_constraints"].version == 1

        unchanged = build_manifest(prompts_dir, path, strict=True)
        assert unchanged.entries["optim_constraints"].version == 1

        target = prompts_dir / "optim_constraints.txt"
        target.write_text(target.read_text(encoding="utf-8") + "\n- extra rule\n", encoding="utf-8")
        bumped = build_manifest(prompts_dir, path, strict=True)
        assert bumped.entries["optim_constraints"].version == 2

    def test_empty_directory_is_an_error(self, tmp_path):
        empty = tmp_path / "empty"
        empty.mkdir()
        with pytest.raises(PromptError, match="No prompt files"):
            build_manifest(empty, tmp_path / "m.json")


class TestRendering:
    def test_renders_with_supplied_variables(self, registry):
        out = registry.render(
            "optim_with_sample_plan",
            query_id="7",
            sf="0.25",
            duckdb_plan="HASH_JOIN",
        )
        assert "7" in out.text
        assert "HASH_JOIN" in out.text

    def test_auto_injects_fragment_prompts(self, registry):
        """${constraints} is filled from optim_constraints without being passed."""
        out = registry.render("optim_with_sample_plan", query_id="1", sf="0.25", duckdb_plan="X")
        assert "Hard constraints" in out.text
        assert "optim_constraints" in out.prompt_ids

    def test_explicit_value_overrides_the_fragment(self, registry):
        out = registry.render(
            "optim_with_sample_plan",
            constraints="MY OWN CONSTRAINTS",
            query_id="1",
            sf="0.25",
            duckdb_plan="X",
        )
        assert "MY OWN CONSTRAINTS" in out.text
        assert "Hard constraints" not in out.text

    def test_missing_placeholder_raises_naming_it(self, registry):
        with pytest.raises(MissingPlaceholderError) as excinfo:
            registry.render("optim_with_sample_plan", query_id="1")
        message = str(excinfo.value)
        assert "duckdb_plan" in message
        assert "optim_with_sample_plan" in message

    def test_unknown_prompt_id_lists_available_ids(self, registry):
        with pytest.raises(MissingPromptError, match="optim_w_trace"):
            registry.render("no_such_prompt")

    def test_prompt_with_no_placeholders_renders_verbatim(self, registry):
        out = registry.render("optim_constraints")
        assert out.text == registry.text_of("optim_constraints")

    def test_compose_joins_several_prompts(self, registry):
        out = registry.compose(
            "optim_pretext_general",
            "optim_constraints",
            queries_path="q.txt",
            num_queries="3",
            query_str="SELECT 1",
        )
        assert "q.txt" in out.text
        assert "Hard constraints" in out.text
        assert out.prompt_ids[0] == "optim_pretext_general"

    def test_compose_requires_at_least_one_id(self, registry):
        with pytest.raises(PromptError):
            registry.compose()


class TestFingerprints:
    def test_fingerprint_is_stable(self, registry):
        assert registry.fingerprint("optim_constraints") == registry.fingerprint(
            "optim_constraints"
        )

    def test_fingerprint_changes_when_the_file_changes(self, prompts_dir, tmp_path):
        path = tmp_path / "m.json"
        build_manifest(prompts_dir, path, strict=True)
        before = PromptRegistry.from_manifest(path).fingerprint("optim_constraints")

        target = prompts_dir / "optim_constraints.txt"
        target.write_text(target.read_text(encoding="utf-8") + "\n- new rule\n", encoding="utf-8")
        build_manifest(prompts_dir, path, strict=True)
        after = PromptRegistry.from_manifest(path).fingerprint("optim_constraints")

        # This is what makes editing a prompt invalidate its cached completions.
        assert before != after

    def test_rendered_fingerprint_covers_injected_fragments(self, registry):
        out = registry.render("optim_with_sample_plan", query_id="1", sf="0.2", duckdb_plan="X")
        assert out.fingerprint == registry.fingerprint(*out.prompt_ids)
        assert len(out.prompt_ids) == 2


class TestStaleDetection:
    def test_editing_a_file_without_rebuilding_is_detected(self, prompts_dir, manifest_path):
        """A stale manifest means stale placeholders and stale cache keys."""
        registry = PromptRegistry.from_manifest(manifest_path)
        target = prompts_dir / "optim_constraints.txt"
        target.write_text("totally different content\n", encoding="utf-8")
        with pytest.raises(PromptError, match="has changed since the manifest was built"):
            registry.text_of("optim_constraints")


class TestRenderAny:
    def test_prefers_inline_text_over_the_prompt_id(self, registry):
        out = registry.render_any("optim_constraints", inline="inline policy text")
        assert out.text == "inline policy text"
        assert out.prompt_ids == ["<inline>"]

    def test_falls_back_to_the_prompt_id(self, registry):
        out = registry.render_any("optim_constraints", inline=None)
        assert "Hard constraints" in out.text

    def test_inline_text_supports_placeholders(self, registry):
        out = registry.render_any(None, inline="levels: ${level_names}", level_names="a, b")
        assert out.text == "levels: a, b"

    def test_inline_missing_placeholder_raises(self, registry):
        with pytest.raises(MissingPlaceholderError):
            registry.render_any(None, inline="levels: ${level_names}")

    def test_needs_either_id_or_inline(self, registry):
        with pytest.raises(PromptError):
            registry.render_any(None, inline=None)
