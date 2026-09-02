from pathlib import Path

import pytest

from etio.cli import run_action, validate_action_inputs
from etio.logs import WorkflowJob


def test_validate_action_inputs_requires_tokens() -> None:
    with pytest.raises(ValueError, match="github_token, groq_api_key"):
        validate_action_inputs({})


def test_run_action_collects_context_and_diff(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    output_path = tmp_path / "github-output"
    monkeypatch.setenv("INPUT_GITHUB_TOKEN", "token")
    monkeypatch.setenv("INPUT_GROQ_API_KEY", "key")
    monkeypatch.setenv("INPUT_MAX_DIFF_LINES", "400")
    monkeypatch.setenv("GITHUB_OUTPUT", str(output_path))
    monkeypatch.setenv("GITHUB_REPOSITORY", "octo/etio")
    monkeypatch.setenv("GITHUB_RUN_ID", "42")
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "1")
    monkeypatch.setenv("GITHUB_SHA", "head-sha")
    monkeypatch.setenv("GITHUB_REF_NAME", "main")
    monkeypatch.setattr(
        "etio.cli.list_attempt_jobs",
        lambda *_: [WorkflowJob(17, "test", "completed", "failure")],
    )
    monkeypatch.setattr("etio.cli.fetch_job_logs", lambda *_: "error: broken")
    monkeypatch.setattr("etio.cli.find_last_passing_commit", lambda *_: "base-sha")
    monkeypatch.setattr("etio.cli.build_failure_diff", lambda *_: "diff")

    assert run_action() == 0
    assert output_path.read_text(encoding="utf-8") == (
        "status=context-and-diff-extracted\n"
        "breaking-commit=\n"
        "report-url=\n"
        "diagnosis=\n"
    )


def test_run_action_does_not_guess_between_failed_jobs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    output_path = tmp_path / "github-output"
    monkeypatch.setenv("INPUT_GITHUB_TOKEN", "token")
    monkeypatch.setenv("INPUT_GROQ_API_KEY", "key")
    monkeypatch.setenv("INPUT_MAX_DIFF_LINES", "400")
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


def test_run_action_reports_missing_passing_commit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    output_path = tmp_path / "github-output"
    monkeypatch.setenv("INPUT_GITHUB_TOKEN", "token")
    monkeypatch.setenv("INPUT_GROQ_API_KEY", "key")
    monkeypatch.setenv("INPUT_MAX_DIFF_LINES", "400")
    monkeypatch.setenv("GITHUB_OUTPUT", str(output_path))
    monkeypatch.setenv("GITHUB_REPOSITORY", "octo/etio")
    monkeypatch.setenv("GITHUB_RUN_ID", "42")
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "1")
    monkeypatch.setenv("GITHUB_SHA", "head-sha")
    monkeypatch.setenv("GITHUB_REF_NAME", "main")
    monkeypatch.setattr(
        "etio.cli.list_attempt_jobs",
        lambda *_: [WorkflowJob(17, "test", "completed", "failure")],
    )
    monkeypatch.setattr("etio.cli.fetch_job_logs", lambda *_: "error: broken")
    monkeypatch.setattr("etio.cli.find_last_passing_commit", lambda *_: None)
    monkeypatch.setattr(
        "etio.cli.build_failure_diff",
        lambda *_: pytest.fail("No diff should be built without a base commit."),
    )

    assert run_action() == 0
    assert output_path.read_text(encoding="utf-8").startswith(
        "status=no-passing-commit\n"
    )
