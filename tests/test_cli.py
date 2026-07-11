import json
import shutil
from pathlib import Path

from click.testing import CliRunner
from src.cli.main import cli

REPO_ROOT = Path(__file__).resolve().parents[1]


def _make_workdir(tmp_path: Path) -> Path:
    """Copy the shipped config/ into an isolated tmp workdir so CLI tests
    never write into the real repo's runs/ directory."""
    shutil.copytree(REPO_ROOT / "config", tmp_path / "config")
    return tmp_path


def _last_json_object(output: str) -> dict:
    """Extract the final pretty-printed JSON object from CLI stdout.

    Structured log lines (single-line JSON) precede the command's final
    `click.echo(json.dumps(..., indent=2))` summary. A naive `rindex("{")`
    would match a brace nested *inside* that pretty-printed summary (e.g.
    "job_counts": {...}), so instead find the last line that is exactly
    an opening brace at column 0 - that's where the indented summary starts.
    """
    lines = output.splitlines()
    start = max(i for i, line in enumerate(lines) if line == "{")
    return json.loads("\n".join(lines[start:]))


def test_run_dry_run_executes_empty_pipeline(tmp_path, monkeypatch):
    workdir = _make_workdir(tmp_path)
    monkeypatch.chdir(workdir)
    monkeypatch.delenv("JOBRIGHT_EMAIL", raising=False)
    monkeypatch.delenv("JOBRIGHT_PASSWORD", raising=False)

    runner = CliRunner()
    result = runner.invoke(cli, ["run", "--dry-run"])

    assert result.exit_code == 0, result.output
    summary = _last_json_object(result.output)
    assert summary["dry_run"] is True
    assert set(summary["stages_run"]) == {
        "auth",
        "discovery",
        "selection",
        "resume_tailoring",
        "executor",
        "reporting",
    }
    assert summary["job_counts"]["discovered"] == 0


def test_run_without_dry_run_fails_fast_without_secrets(tmp_path, monkeypatch):
    workdir = _make_workdir(tmp_path)
    monkeypatch.chdir(workdir)
    monkeypatch.delenv("JOBRIGHT_EMAIL", raising=False)
    monkeypatch.delenv("JOBRIGHT_PASSWORD", raising=False)

    runner = CliRunner()
    result = runner.invoke(cli, ["run"])

    assert result.exit_code == 1
    assert "startup failed" in result.output
    assert "JOBRIGHT_EMAIL" in result.output


def test_run_without_dry_run_succeeds_with_secrets(tmp_path, monkeypatch):
    workdir = _make_workdir(tmp_path)
    monkeypatch.chdir(workdir)
    monkeypatch.setenv("JOBRIGHT_EMAIL", "user@example.com")
    monkeypatch.setenv("JOBRIGHT_PASSWORD", "hunter2")

    runner = CliRunner()
    result = runner.invoke(cli, ["run"])

    assert result.exit_code == 0, result.output
    assert "hunter2" not in result.output  # secrets never appear in logs/output


def test_discover_only_stub(tmp_path, monkeypatch):
    workdir = _make_workdir(tmp_path)
    monkeypatch.chdir(workdir)

    runner = CliRunner()
    result = runner.invoke(cli, ["discover-only"])

    assert result.exit_code == 0, result.output
    summary = _last_json_object(result.output)
    assert summary["stage"] == "discovery"
    assert summary["status"] == "not_yet_implemented"


def test_resume_hitl_unknown_ticket(tmp_path, monkeypatch):
    workdir = _make_workdir(tmp_path)
    monkeypatch.chdir(workdir)

    runner = CliRunner()
    result = runner.invoke(cli, ["resume-hitl", "some-ticket-id"])

    assert result.exit_code == 0, result.output
    payload = _last_json_object(result.output)
    assert payload["ticket_id"] == "some-ticket-id"
    assert payload["status"] == "not_found"
