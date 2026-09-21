"""CLI commands. Each is exercised through main() with real arguments."""

import json

import pytest

from agent.cli import main


class TestDoctor:
    def test_reports_the_environment(self, capsys):
        assert main(["doctor"]) == 0
        out = capsys.readouterr().out
        assert "Python packages:" in out
        assert "dspy" in out
        assert "deno" in out
        assert "API keys:" in out

    def test_validates_a_config_and_its_inputs(self, capsys, config_file):
        assert main(["doctor", "--config", str(config_file)]) == 0
        out = capsys.readouterr().out
        assert "storage_plan" in out
        assert "input schema" in out
        assert "MISSING" not in out.split("Config")[1]

    def test_flags_a_missing_input_file(self, capsys, config_file, loaded_config):
        loaded_config.resolve_stage("storage_plan").inputs["schema"].unlink()
        assert main(["doctor", "--config", str(config_file)]) == 0
        assert "MISSING" in capsys.readouterr().out

    def test_reports_a_broken_config(self, capsys, tmp_path):
        bad = tmp_path / "bad.json"
        bad.write_text("{not json", encoding="utf-8")
        assert main(["doctor", "--config", str(bad)]) == 1
        assert "not valid JSON" in capsys.readouterr().out


class TestPrompts:
    def test_build_imports_a_directory(self, capsys, prompts_dir, tmp_path):
        manifest = tmp_path / "m.json"
        code = main(
            ["prompts", "build", "--prompts-dir", str(prompts_dir), "--manifest", str(manifest)]
        )
        assert code == 0
        assert manifest.exists()
        out = capsys.readouterr().out
        assert "optim_constraints" in out
        assert "storage_plan_policy" in out

    def test_build_without_a_directory_recomputes_from_the_manifest(self, capsys, manifest_path):
        """The everyday path: no --prompts-dir, just resync derived fields."""
        code = main(["prompts", "build", "--manifest", str(manifest_path)])
        assert code == 0
        out = capsys.readouterr().out
        assert "optim_constraints" in out
        assert "storage_plan_policy" in out

    def test_build_fails_on_an_invalid_template(self, capsys, prompts_dir, tmp_path):
        (prompts_dir / "bad.txt").write_text("costs $5\n", encoding="utf-8")
        code = main(
            [
                "prompts",
                "build",
                "--prompts-dir",
                str(prompts_dir),
                "--manifest",
                str(tmp_path / "m.json"),
            ]
        )
        assert code == 1
        assert "invalid '$'" in capsys.readouterr().err

    def test_build_lax_accepts_it(self, prompts_dir, tmp_path):
        (prompts_dir / "bad.txt").write_text("costs $5\n", encoding="utf-8")
        code = main(
            [
                "prompts",
                "build",
                "--prompts-dir",
                str(prompts_dir),
                "--manifest",
                str(tmp_path / "m.json"),
                "--lax",
            ]
        )
        assert code == 0

    def test_list(self, capsys, manifest_path):
        assert main(["prompts", "list", "--manifest", str(manifest_path)]) == 0
        out = capsys.readouterr().out
        assert "optim_constraints" in out
        assert "[optimize/fragment]" in out

    def test_show_renders_with_vars(self, capsys, manifest_path):
        code = main(
            [
                "prompts",
                "show",
                "optim_with_sample_plan",
                "--manifest",
                str(manifest_path),
                "--var",
                "query_id=7",
                "--var",
                "sf=0.25",
                "--var",
                "duckdb_plan=HASH_JOIN",
            ]
        )
        assert code == 0
        captured = capsys.readouterr()
        assert "HASH_JOIN" in captured.out
        assert "Hard constraints" in captured.out  # fragment auto-injected
        assert "composed from" in captured.err

    def test_show_raw_skips_rendering(self, capsys, manifest_path):
        assert (
            main(["prompts", "show", "optim_w_trace", "--manifest", str(manifest_path), "--raw"])
            == 0
        )
        assert "${query_id}" in capsys.readouterr().out

    def test_show_reports_a_missing_variable(self, capsys, manifest_path):
        code = main(["prompts", "show", "optim_w_trace", "--manifest", str(manifest_path)])
        assert code == 1
        assert "missing values for placeholder" in capsys.readouterr().err

    def test_show_rejects_a_malformed_var(self, capsys, manifest_path):
        code = main(
            ["prompts", "show", "optim_w_trace", "--manifest", str(manifest_path), "--var", "nope"]
        )
        assert code == 1
        assert "name=value" in capsys.readouterr().err

    def test_show_unknown_prompt(self, capsys, manifest_path):
        assert main(["prompts", "show", "nope", "--manifest", str(manifest_path)]) == 1
        assert "not in the manifest" in capsys.readouterr().err


class TestPromptsSet:
    def test_adds_a_new_prompt_from_a_file(self, capsys, manifest_path, tmp_path):
        source = tmp_path / "new_prompt.txt"
        source.write_text("Summarize ${topic} in one sentence.\n", encoding="utf-8")

        code = main(
            [
                "prompts",
                "set",
                "brand_new_prompt",
                "--manifest",
                str(manifest_path),
                "--file",
                str(source),
            ]
        )
        assert code == 0, capsys.readouterr()
        out = capsys.readouterr().out
        assert "brand_new_prompt" in out
        assert "topic" in out  # the placeholder was detected

        from agent.prompting.registry import PromptRegistry

        registry = PromptRegistry.from_manifest(manifest_path)
        assert "topic" in registry.get("brand_new_prompt").placeholders
        assert registry.render("brand_new_prompt", topic="C++").text == "Summarize C++ in one sentence.\n"

    def test_sets_text_directly_without_a_file(self, capsys, manifest_path):
        code = main(
            [
                "prompts",
                "set",
                "inline_prompt",
                "--manifest",
                str(manifest_path),
                "--text",
                "plain text, no placeholders",
            ]
        )
        assert code == 0

        from agent.prompting.registry import PromptRegistry

        registry = PromptRegistry.from_manifest(manifest_path)
        assert registry.text_of("inline_prompt") == "plain text, no placeholders"

    def test_updates_an_existing_prompt_and_bumps_its_version(self, capsys, manifest_path):
        from agent.prompting.registry import PromptRegistry

        before = PromptRegistry.from_manifest(manifest_path).get("optim_constraints")

        code = main(
            [
                "prompts",
                "set",
                "optim_constraints",
                "--manifest",
                str(manifest_path),
                "--text",
                "New constraints text.",
            ]
        )
        assert code == 0

        after = PromptRegistry.from_manifest(manifest_path).get("optim_constraints")
        assert after.text == "New constraints text."
        assert after.version == before.version + 1
        # Curated metadata survives the edit.
        assert after.stage == before.stage
        assert after.role == before.role

    def test_stage_and_role_overrides_are_applied(self, manifest_path):
        code = main(
            [
                "prompts",
                "set",
                "custom_prompt",
                "--manifest",
                str(manifest_path),
                "--text",
                "text",
                "--stage",
                "optimize",
                "--role",
                "fragment",
                "--description",
                "a hand-written note",
            ]
        )
        assert code == 0

        from agent.prompting.registry import PromptRegistry

        entry = PromptRegistry.from_manifest(manifest_path).get("custom_prompt")
        assert entry.stage == "optimize"
        assert entry.role == "fragment"
        assert entry.description == "a hand-written note"

    def test_rejects_an_invalid_dollar_sequence(self, capsys, manifest_path):
        code = main(
            [
                "prompts",
                "set",
                "broken_prompt",
                "--manifest",
                str(manifest_path),
                "--text",
                "costs $5",
            ]
        )
        assert code == 1
        assert "invalid '$'" in capsys.readouterr().err


class TestConfig:
    def test_show_prints_resolved_stages(self, capsys, config_file):
        assert main(["config", "show", "--config", str(config_file)]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert set(payload) == {"storage_plan", "divide"}
        assert payload["storage_plan"]["model"]["name"] == "openai/gpt-4o-mini"
        # Paths come out absolute.
        assert payload["storage_plan"]["artifacts_dir"].startswith("/")

    def test_show_one_stage(self, capsys, config_file):
        assert main(["config", "show", "--config", str(config_file), "--stage", "divide"]) == 0
        assert set(json.loads(capsys.readouterr().out)) == {"divide"}

    def test_normalize_prints_the_canonical_form(self, capsys, tmp_path):
        legacy = tmp_path / "legacy.json"
        legacy.write_text(
            json.dumps(
                {
                    "model": "openai/gpt-5.4-mini",
                    "schema": "./input/schema.txt",
                    "queries_file": "./input/allqueries.txt",
                    "trace_path": "out/t.jsonl",
                }
            ),
            encoding="utf-8",
        )
        assert main(["config", "normalize", "--config", str(legacy)]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["common"]["inputs"]["queries"] == "./input/allqueries.txt"
        assert payload["common"]["trace"]["path"] == "out/t.jsonl"


class TestRun:
    def test_dry_run_prints_the_plan_without_running(self, capsys, config_file):
        assert main(["run", "--config", str(config_file), "--dry-run"]) == 0
        out = capsys.readouterr().out
        assert "storage_plan" in out
        assert "requires[-]" in out
        assert "would run" in out

    def test_dry_run_of_a_single_stage(self, capsys, config_file):
        code = main(["run", "--config", str(config_file), "--stages", "divide", "--dry-run"])
        assert code == 0
        assert "1 stage(s)" in capsys.readouterr().out

    def test_unknown_stage_is_rejected(self, capsys, config_file):
        code = main(["run", "--config", str(config_file), "--stages", "nope", "--dry-run"])
        assert code == 1
        assert "Unknown stage" in capsys.readouterr().err

    def test_missing_config_file(self, capsys, tmp_path):
        assert main(["run", "--config", str(tmp_path / "absent.json")]) == 1
        assert "not found" in capsys.readouterr().err

    def test_run_writes_a_result_json(
        self, capsys, config_file, manifest_path, tmp_path, monkeypatch
    ):
        """A full run through the CLI with a fake LM, including --result-json."""
        import dspy
        from dspy.utils.dummies import DummyLM

        lm = DummyLM([{"reasoning": "r", "storage_plan": "PLAN", "rationale": "why"}] * 4)

        def fake_configure(model_cfg, callbacks=None, require_key=True):
            dspy.configure(lm=lm, callbacks=list(callbacks) if callbacks else None)
            return lm

        monkeypatch.setattr("agent.stages.storage_plan.configure_dspy", fake_configure)

        result_path = tmp_path / "result.json"
        code = main(
            [
                "run",
                "--config",
                str(config_file),
                "--stages",
                "storage_plan",
                "--manifest",
                str(manifest_path),
                "--result-json",
                str(result_path),
            ]
        )
        assert code == 0, capsys.readouterr()
        out = capsys.readouterr().out
        assert "storage_plan: ok" in out

        summary = json.loads(result_path.read_text(encoding="utf-8"))
        assert summary["results"][0]["status"] == "ok"
        assert summary["results"][0]["artifacts"]["storage_plan"]["content_hash"]

    def test_cache_flags_control_reuse_across_runs(self, capsys, config_file, manifest_path, monkeypatch):
        """--no-cache always calls the model; --refresh-cache recomputes but re-caches.

        Every run passes --force so the pipeline's artifact-freshness skip never
        short-circuits the stage, which would otherwise hide whether the
        stage's own disk cache (a separate layer) was consulted at all.
        """
        import dspy
        from dspy.utils.dummies import DummyLM

        calls = []

        def fake_configure(model_cfg, callbacks=None, require_key=True):
            calls.append(1)
            lm = DummyLM([{"reasoning": "r", "storage_plan": "PLAN", "rationale": "why"}])
            dspy.configure(lm=lm, callbacks=list(callbacks) if callbacks else None)
            return lm

        monkeypatch.setattr("agent.stages.storage_plan.configure_dspy", fake_configure)

        base_args = [
            "run",
            "--config",
            str(config_file),
            "--stages",
            "storage_plan",
            "--manifest",
            str(manifest_path),
            "--force",
        ]

        assert main(base_args) == 0
        assert len(calls) == 1, "first run: nothing cached yet"

        assert main(base_args) == 0
        assert len(calls) == 1, "second run: the disk cache should have been hit"

        assert main(base_args + ["--no-cache"]) == 0
        assert len(calls) == 2, "--no-cache must bypass an existing hit"

        assert main(base_args + ["--refresh-cache"]) == 0
        assert len(calls) == 3, "--refresh-cache must recompute despite a valid entry"

        assert main(base_args) == 0
        assert len(calls) == 3, "a plain run afterward should hit the entry --refresh-cache wrote"

    def test_run_exits_nonzero_when_a_stage_fails(self, capsys, config_file, manifest_path):
        """hppgen without its upstream artifact exercises the failure exit path.

        Chosen because it fails before any model call, so the exit-code path is
        tested without a network round trip.
        """
        code = main(
            [
                "run",
                "--config",
                str(config_file),
                "--stages",
                "hppgen",
                "--manifest",
                str(manifest_path),
            ]
        )
        assert code == 1
        assert "Missing required artifact(s): schema_levels" in capsys.readouterr().out


class TestRunSteps:
    """``--steps N`` runs N pipeline stages one at a time, advancing automatically."""

    @staticmethod
    def _patch_storage_plan_lm(monkeypatch):
        import dspy
        from dspy.utils.dummies import DummyLM

        lm = DummyLM([{"reasoning": "r", "storage_plan": "PLAN", "rationale": "why"}] * 4)

        def fake_configure(model_cfg, callbacks=None, require_key=True):
            dspy.configure(lm=lm, callbacks=list(callbacks) if callbacks else None)
            return lm

        monkeypatch.setattr("agent.stages.storage_plan.configure_dspy", fake_configure)

    @staticmethod
    def _patch_divide_lm(monkeypatch):
        import dspy
        from dspy.utils.dummies import DummyLM

        schema_levels = json.dumps(
            {
                "levels": {"no_hints": "no hints text", "all_hints": "all hints text"},
                "data_structure_descriptions": {"no_hints": "d1", "all_hints": "d2"},
            }
        )
        lm = DummyLM([{"reasoning": "r", "schema_levels": schema_levels}] * 4)

        def fake_configure(model_cfg, callbacks=None, require_key=True):
            dspy.configure(lm=lm, callbacks=list(callbacks) if callbacks else None)
            return lm

        monkeypatch.setattr("agent.stages.predict.configure_dspy", fake_configure)

    def test_runs_each_stage_as_its_own_step(
        self, capsys, config_file, manifest_path, monkeypatch
    ):
        """--steps 2 over a two-stage config runs storage_plan then divide."""
        self._patch_storage_plan_lm(monkeypatch)
        self._patch_divide_lm(monkeypatch)

        code = main(
            [
                "run",
                "--config",
                str(config_file),
                "--manifest",
                str(manifest_path),
                "--steps",
                "2",
            ]
        )
        out = capsys.readouterr().out
        assert code == 0, out
        assert "[step 1/2] storage_plan" in out
        assert "[step 2/2] divide" in out
        assert "storage_plan: ok" in out
        assert "divide: ok" in out
        assert "completed 2/2 step(s)" in out

    def test_steps_beyond_the_stage_count_is_clamped(
        self, capsys, config_file, manifest_path, monkeypatch
    ):
        """--steps 5 on a two-stage config runs both stages, not five."""
        self._patch_storage_plan_lm(monkeypatch)
        self._patch_divide_lm(monkeypatch)

        code = main(
            [
                "run",
                "--config",
                str(config_file),
                "--manifest",
                str(manifest_path),
                "--steps",
                "5",
            ]
        )
        out = capsys.readouterr().out
        assert code == 0, out
        assert "[step 2/2] divide" in out
        assert "[step 3" not in out
        assert "completed 2/2 step(s)" in out

    def test_stops_at_the_failing_step_by_default(
        self, capsys, config_file, manifest_path, monkeypatch
    ):
        """A stage failure halts --steps immediately unless --keep-going is set."""
        self._patch_storage_plan_lm(monkeypatch)
        # divide is left unpatched so its model call fails without an API key,
        # exercising the same failure path as a real broken step.

        code = main(
            [
                "run",
                "--config",
                str(config_file),
                "--manifest",
                str(manifest_path),
                "--steps",
                "2",
            ]
        )
        out = capsys.readouterr().out
        assert code == 1
        assert "[step 1/2] storage_plan" in out
        assert "storage_plan: ok" in out
        assert "[step 2/2] divide" in out
        assert "stopped after 2/2 step(s): divide failed" in out

    def test_single_stage_selection_runs_one_step(
        self, capsys, config_file, manifest_path, monkeypatch
    ):
        """--stages combined with --steps still counts steps over the filtered plan."""
        self._patch_storage_plan_lm(monkeypatch)

        code = main(
            [
                "run",
                "--config",
                str(config_file),
                "--stages",
                "storage_plan",
                "--manifest",
                str(manifest_path),
                "--steps",
                "5",
            ]
        )
        out = capsys.readouterr().out
        assert code == 0, out
        assert "[step 1/1] storage_plan" in out
        assert "completed 1/1 step(s)" in out


class TestArgParsing:
    def test_version(self, capsys):
        with pytest.raises(SystemExit) as excinfo:
            main(["--version"])
        assert excinfo.value.code == 0
        assert "agent" in capsys.readouterr().out

    def test_a_command_is_required(self):
        with pytest.raises(SystemExit):
            main([])
