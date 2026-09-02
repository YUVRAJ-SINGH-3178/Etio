from pathlib import Path

import pytest

from etio.cli import (
    _event_pull_request_number,
    _report_target,
    run_action,
    validate_action_inputs,
    write_action_outputs,
)
from etio.logs import WorkflowJob
from etio.models import Diagnosis


def test_validate_action_inputs_requires_tokens() -> None:
    with pytest.raises(ValueError, match="github_token, groq_api_key"):
        validate_action_inputs({})


def test_run_action_collects_an_unreported_diagnosis(
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
    monkeypatch.setattr(
        "etio.cli.diagnose_failure",
        lambda *_: Diagnosis("The setup is broken.", "Initialization changed.", None),
    )

    assert run_action() == 0
    assert output_path.read_text(encoding="utf-8") == (
        "status=diagnosed-unreported\n"
        "breaking-commit=\n"
        "report-url=\n"
        "diagnosis=The setup is broken.\n"
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


def test_run_action_reports_to_the_pull_request_in_a_matching_event(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    output_path = tmp_path / "github-output"
    event_path = tmp_path / "event.json"
    event_path.write_text(
        '{"repository":{"full_name":"octo/etio"},"pull_request":{"number":7}}',
        encoding="utf-8",
    )
    monkeypatch.setenv("INPUT_GITHUB_TOKEN", "token")
    monkeypatch.setenv("INPUT_GROQ_API_KEY", "key")
    monkeypatch.setenv("INPUT_MAX_DIFF_LINES", "400")
    monkeypatch.setenv("GITHUB_OUTPUT", str(output_path))
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event_path))
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
    monkeypatch.setattr(
        "etio.cli.diagnose_failure",
        lambda *_: Diagnosis("The setup is broken.", "Initialization changed.", None),
    )
    monkeypatch.setattr(
        "etio.cli.post_diagnosis",
        lambda *_: "https://github.test/octo/etio/pull/7#issuecomment-1",
    )

    assert run_action() == 0
    assert output_path.read_text(encoding="utf-8") == (
        "status=diagnosed\n"
        "breaking-commit=\n"
        "report-url=https://github.test/octo/etio/pull/7#issuecomment-1\n"
        "diagnosis=The setup is broken.\n"
    )


def test_report_target_rejects_mismatched_event_repository(tmp_path: Path) -> None:
    event_path = tmp_path / "event.json"
    event_path.write_text(
        '{"repository":{"full_name":"someone-else/repo"},"pull_request":{"number":7}}',
        encoding="utf-8",
    )
    environment = {"GITHUB_EVENT_PATH": str(event_path)}

    assert _event_pull_request_number(environment, "octo/etio") is None
    assert _report_target("auto", environment, "octo/etio", "head") is None


def test_explicit_pull_request_target_requires_a_number() -> None:
    with pytest.raises(ValueError, match="needs pr-number"):
        _report_target("pull-request", {}, "octo/etio", "head")


def test_write_action_outputs_prevents_newline_injection(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    output_path = tmp_path / "github-output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output_path))

    write_action_outputs(
        {"GITHUB_OUTPUT": str(output_path)},
        "diagnosed",
        diagnosis="first\r\nforged=value ghp_abcdefghijklmnopqrstuvwxyz1234567890",
    )

    contents = output_path.read_text(encoding="utf-8")
    assert "forged=value" in contents
    assert contents.count("\n") == 4
    assert "ghp_" not in contents
