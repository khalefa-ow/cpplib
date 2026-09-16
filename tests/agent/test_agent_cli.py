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
    def test_build(self, capsys, prompts_dir, tmp_path):
        manifest = tmp_path / "m.json"
        code = main(
            ["prompts", "build", "--prompts-dir", str(prompts_dir), "--manifest", str(manifest)]
        )
        assert code == 0
        assert manifest.exists()
        out = capsys.readouterr().out
        assert "optim_w_trace" in out
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

    def test_run_exits_nonzero_when_a_stage_fails(self, capsys, config_file, manifest_path):
        """divide is a stub, so this exercises the failure exit path."""
        from agent.stages.base import ArtifactStore
        from agent.config.loader import LoadedConfig

        config = LoadedConfig.from_file(config_file)
        store = ArtifactStore(config.resolve_stage("divide").artifacts_dir)
        store.put_text("storage_plan", "storage_plan", "a plan")

        code = main(
            [
                "run",
                "--config",
                str(config_file),
                "--stages",
                "divide",
                "--manifest",
                str(manifest_path),
            ]
        )
        assert code == 1
        assert "not implemented" in capsys.readouterr().out


class TestArgParsing:
    def test_version(self, capsys):
        with pytest.raises(SystemExit) as excinfo:
            main(["--version"])
        assert excinfo.value.code == 0
        assert "agent" in capsys.readouterr().out

    def test_a_command_is_required(self):
        with pytest.raises(SystemExit):
            main([])
