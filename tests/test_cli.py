from pathlib import Path

import pytest

from etio.cli import run_action, validate_action_inputs
from etio.logs import WorkflowJob


def test_validate_action_inputs_requires_tokens() -> None:
    with pytest.raises(ValueError, match="github_token, groq_api_key"):
        validate_action_inputs({})


def test_run_action_writes_context_status(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    output_path = tmp_path / "github-output"
    monkeypatch.setenv("INPUT_GITHUB_TOKEN", "token")
    monkeypatch.setenv("INPUT_GROQ_API_KEY", "key")
    monkeypatch.setenv("GITHUB_OUTPUT", str(output_path))
    monkeypatch.setenv("GITHUB_REPOSITORY", "octo/etio")
    monkeypatch.setenv("GITHUB_RUN_ID", "42")
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "1")
    monkeypatch.setattr(
        "etio.cli.list_attempt_jobs",
        lambda *_: [WorkflowJob(17, "test", "completed", "failure")],
    )
    monkeypatch.setattr("etio.cli.fetch_job_logs", lambda *_: "error: broken")

    assert run_action() == 0
    assert output_path.read_text(encoding="utf-8") == (
        "status=context-extracted\n" "breaking-commit=\n" "report-url=\n" "diagnosis=\n"
    )


def test_run_action_does_not_guess_between_failed_jobs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    output_path = tmp_path / "github-output"
    monkeypatch.setenv("INPUT_GITHUB_TOKEN", "token")
    monkeypatch.setenv("INPUT_GROQ_API_KEY", "key")
    monkeypatch.setenv("GITHUB_OUTPUT", str(output_path))
    monkeypatch.setenv("GITHUB_REPOSITORY", "octo/etio")
    monkeypatch.setenv("GITHUB_RUN_ID", "42")
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "1")
    monkeypatch.setattr(
        "etio.cli.list_attempt_jobs",
        lambda *_: [
            WorkflowJob(17, "test", "completed", "failure"),
            WorkflowJob(18, "lint", "completed", "failure"),
        ],
    )
    monkeypatch.setattr(
        "etio.cli.fetch_job_logs",
        lambda *_: pytest.fail("Etio must not fetch an arbitrary failed job."),
    )

    assert run_action() == 0
    assert output_path.read_text(encoding="utf-8").startswith(
        "status=no-failure-context\n"
    )
