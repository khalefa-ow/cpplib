"""Prompt manifest maintenance and strict rendering."""

import json

import pytest

from agent.errors import (
    InvalidTemplateError,
    MissingPlaceholderError,
    MissingPromptError,
    PromptError,
)
from agent.prompting.build_manifest import (
    classify,
    import_prompts_dir,
    rebuild_manifest,
    set_prompt_text,
)
from agent.prompting.manifest import scan_placeholders
from agent.prompting.registry import PromptRegistry


class TestScanPlaceholders:
    def test_detects_braced_form(self):
        names, problems = scan_placeholders("a ${one} b ${two}")
        assert names == ["one", "two"]
        assert problems == []

    def test_detects_bare_form(self):
        """optim_pretext_general uses bare $name, not ${name}."""
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


class TestImportPromptsDir:
    """The directory → inline-manifest migration path."""

    def test_imports_over_a_scratch_directory(self, prompts_dir, tmp_path):
        manifest = import_prompts_dir(prompts_dir, tmp_path / "m.json", strict=True)
        assert set(manifest.entries) == {"storage_plan_policy", "divide_policy", "optim_constraints"}
        assert "${query_id}" in manifest.entries["storage_plan_policy"].text

    def test_records_placeholders_from_the_imported_text(self, prompts_dir, tmp_path):
        manifest = import_prompts_dir(prompts_dir, tmp_path / "m.json", strict=True)
        assert manifest.entries["divide_policy"].placeholders == ["level_names"]

    def test_classify_assigns_stage_and_role(self):
        assert classify("optim_constraints") == ("optimize", "fragment")
        assert classify("optim_pretext_general") == ("optimize", "fragment")
        assert classify("expert_knowledge") == ("optimize", "knowledge")
        assert classify("optim_w_trace") == ("optimize", "task")
        assert classify("storage_plan_policy") == ("storage_plan", "task")
        assert classify("divide_policy") == ("divide", "task")
        assert classify("something_else") == (None, "task")

    def test_strict_import_fails_on_invalid_dollar(self, prompts_dir, tmp_path):
        (prompts_dir / "broken.txt").write_text("price is $5\n", encoding="utf-8")
        with pytest.raises(InvalidTemplateError):
            import_prompts_dir(prompts_dir, tmp_path / "m.json", strict=True)

    def test_lax_import_tolerates_invalid_dollar(self, prompts_dir, tmp_path):
        (prompts_dir / "broken.txt").write_text("price is $5\n", encoding="utf-8")
        manifest = import_prompts_dir(prompts_dir, tmp_path / "m.json", strict=False)
        assert "broken" in manifest.entries

    def test_merges_into_an_existing_manifest_without_touching_other_ids(
        self, prompts_dir, tmp_path
    ):
        path = tmp_path / "m.json"
        set_prompt_text(path, "untouched", "some text", strict=True)
        import_prompts_dir(prompts_dir, path, strict=True)
        manifest = PromptRegistry.from_manifest(path).manifest
        assert "untouched" in manifest.entries
        assert "storage_plan_policy" in manifest.entries

    def test_version_bumps_only_when_content_changes(self, prompts_dir, tmp_path):
        path = tmp_path / "m.json"
        first = import_prompts_dir(prompts_dir, path, strict=True)
        assert first.entries["optim_constraints"].version == 1

        unchanged = import_prompts_dir(prompts_dir, path, strict=True)
        assert unchanged.entries["optim_constraints"].version == 1

        target = prompts_dir / "optim_constraints.txt"
        target.write_text(target.read_text(encoding="utf-8") + "\n- extra rule\n", encoding="utf-8")
        bumped = import_prompts_dir(prompts_dir, path, strict=True)
        assert bumped.entries["optim_constraints"].version == 2

    def test_empty_directory_is_an_error(self, tmp_path):
        empty = tmp_path / "empty"
        empty.mkdir()
        with pytest.raises(PromptError, match="No prompt files"):
            import_prompts_dir(empty, tmp_path / "m.json")


class TestRebuildManifest:
    """The everyday path: recompute derived fields from each entry's own text."""

    def test_recomputes_over_the_real_manifest(self, manifest_path):
        manifest = rebuild_manifest(manifest_path, strict=True)
        assert "optim_w_trace" in manifest.entries
        assert "storage_plan_policy" in manifest.entries
        assert len(manifest.entries) >= 12

    def test_hand_edited_text_leaves_the_version_untouched(self, manifest_path):
        """rebuild_manifest has no snapshot of the pre-edit text to diff
        against (the manifest is both its input and output), so it cannot
        detect the edit to bump version. That's fine: fingerprint() hashes
        text directly, so cache invalidation does not depend on this."""
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
        before_version = raw["entries"]["optim_constraints"]["version"]
        raw["entries"]["optim_constraints"]["text"] += "\n- one more rule\n"
        manifest_path.write_text(json.dumps(raw), encoding="utf-8")

        rebuilt = rebuild_manifest(manifest_path, strict=True)
        assert rebuilt.entries["optim_constraints"].version == before_version

    def test_unchanged_text_keeps_the_version(self, manifest_path):
        before = rebuild_manifest(manifest_path, strict=True)
        again = rebuild_manifest(manifest_path, strict=True)
        assert (
            before.entries["optim_constraints"].version
            == again.entries["optim_constraints"].version
        )

    def test_preserves_curated_metadata(self, manifest_path):
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
        raw["entries"]["optim_w_trace"]["description"] = "hand written note"
        raw["entries"]["optim_w_trace"]["stage"] = "custom_stage"
        manifest_path.write_text(json.dumps(raw), encoding="utf-8")

        rebuilt = rebuild_manifest(manifest_path, strict=True)
        assert rebuilt.entries["optim_w_trace"].description == "hand written note"
        assert rebuilt.entries["optim_w_trace"].stage == "custom_stage"

    def test_strict_rebuild_fails_on_invalid_dollar(self, manifest_path):
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
        raw["entries"]["optim_constraints"]["text"] = "price is $5"
        manifest_path.write_text(json.dumps(raw), encoding="utf-8")
        with pytest.raises(InvalidTemplateError):
            rebuild_manifest(manifest_path, strict=True)

    def test_lax_rebuild_tolerates_invalid_dollar(self, manifest_path):
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
        raw["entries"]["optim_constraints"]["text"] = "price is $5"
        manifest_path.write_text(json.dumps(raw), encoding="utf-8")
        manifest = rebuild_manifest(manifest_path, strict=False)
        assert "optim_constraints" in manifest.entries

    def test_missing_manifest_is_an_error(self, tmp_path):
        with pytest.raises(PromptError, match="not found"):
            rebuild_manifest(tmp_path / "nope.json")

    def test_empty_manifest_is_an_error(self, tmp_path):
        path = tmp_path / "empty.json"
        path.write_text(json.dumps({"entries": {}}), encoding="utf-8")
        with pytest.raises(PromptError, match="no entries"):
            rebuild_manifest(path)


class TestSetPromptText:
    def test_adds_a_new_entry(self, manifest_path):
        manifest = set_prompt_text(manifest_path, "greeting", "Hello, ${name}!")
        entry = manifest.entries["greeting"]
        assert entry.text == "Hello, ${name}!"
        assert entry.placeholders == ["name"]
        assert entry.version == 1

    def test_updates_an_existing_entry_and_bumps_version(self, manifest_path):
        before = PromptRegistry.from_manifest(manifest_path).get("optim_constraints")
        manifest = set_prompt_text(manifest_path, "optim_constraints", "New text.")
        after = manifest.entries["optim_constraints"]
        assert after.text == "New text."
        assert after.version == before.version + 1
        assert after.stage == before.stage  # curated field preserved

    def test_explicit_metadata_overrides_curated_fields(self, manifest_path):
        manifest = set_prompt_text(
            manifest_path,
            "optim_constraints",
            "New text.",
            stage="custom",
            role="knowledge",
            description="note",
        )
        entry = manifest.entries["optim_constraints"]
        assert entry.stage == "custom"
        assert entry.role == "knowledge"
        assert entry.description == "note"

    def test_strict_set_fails_on_invalid_dollar(self, manifest_path):
        with pytest.raises(InvalidTemplateError):
            set_prompt_text(manifest_path, "broken", "price is $5", strict=True)

    def test_lax_set_tolerates_invalid_dollar(self, manifest_path):
        manifest = set_prompt_text(manifest_path, "broken", "price is $5", strict=False)
        assert "broken" in manifest.entries

    def test_creates_a_manifest_that_does_not_exist_yet(self, tmp_path):
        path = tmp_path / "new.json"
        manifest = set_prompt_text(path, "first", "text")
        assert path.exists()
        assert "first" in manifest.entries


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

    def test_fingerprint_changes_the_moment_text_is_edited(self, manifest_path):
        """No rebuild needed: the fingerprint hashes text directly, so a bare
        hand edit invalidates the right cache entries immediately."""
        before = PromptRegistry.from_manifest(manifest_path).fingerprint("optim_constraints")

        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
        raw["entries"]["optim_constraints"]["text"] += "\n- new rule\n"
        manifest_path.write_text(json.dumps(raw), encoding="utf-8")

        after = PromptRegistry.from_manifest(manifest_path).fingerprint("optim_constraints")
        # This is what makes editing a prompt invalidate its cached completions.
        assert before != after

    def test_rebuild_does_not_undo_the_fingerprint_change(self, manifest_path):
        """A rebuild after the edit still leaves the fingerprint changed —
        rebuild_manifest doesn't bump version here (see TestRebuildManifest),
        but that's irrelevant: the fingerprint already reflects the new text."""
        before = PromptRegistry.from_manifest(manifest_path).fingerprint("optim_constraints")

        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
        raw["entries"]["optim_constraints"]["text"] += "\n- new rule\n"
        manifest_path.write_text(json.dumps(raw), encoding="utf-8")
        rebuild_manifest(manifest_path, strict=True)

        after = PromptRegistry.from_manifest(manifest_path).fingerprint("optim_constraints")
        assert before != after

    def test_rendered_fingerprint_covers_injected_fragments(self, registry):
        out = registry.render("optim_with_sample_plan", query_id="1", sf="0.2", duckdb_plan="X")
        assert out.fingerprint == registry.fingerprint(*out.prompt_ids)
        assert len(out.prompt_ids) == 2


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
