"""Command-line entry point for the Etio composite action."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path

from etio.auto_pr import AutoPRError, open_auto_pr
from etio.bisect import build_failure_diff, find_last_passing_commit
from etio.config import load_config
from etio.diagnose import DiagnosisError, GroqAPIError, diagnose_failure
from etio.logs import (
    extract_failure_context,
    fetch_job_logs,
    list_attempt_jobs,
    select_failed_job,
)
from etio.models import Diagnosis
from etio.real_bisect import RealBisectError, run_real_bisection
from etio.redact import redact_sensitive_values
from etio.report import GitHubReportError, ReportTarget, post_diagnosis

MAX_OUTPUT_VALUE_CHARACTERS = 1_000


def validate_action_inputs(environment: Mapping[str, str]) -> None:
    """Fail early when required action inputs are absent."""
    missing = [
        name
        for name in ("INPUT_GITHUB_TOKEN", "INPUT_GROQ_API_KEY")
        if not environment.get(name)
    ]
    if missing:
        names = ", ".join(name.removeprefix("INPUT_").lower() for name in missing)
        raise ValueError(f"Missing required Etio action input(s): {names}.")


def write_action_outputs(
    environment: Mapping[str, str],
    status: str,
    breaking_commit: str = "",
    report_url: str = "",
    diagnosis: str = "",
    bisection_status: str = "disabled",
    auto_pr_url: str = "",
    auto_pr_status: str = "disabled",
) -> None:
    """Write bounded single-line action outputs without exposing raw context."""
    output_path = environment.get("GITHUB_OUTPUT")
    if not output_path:
        return
    output_values = {
        "status": status,
        "breaking-commit": breaking_commit,
        "report-url": report_url,
        "diagnosis": diagnosis,
        "bisection-status": bisection_status,
        "auto-pr-url": auto_pr_url,
        "auto-pr-status": auto_pr_status,
    }
    with open(output_path, "a", encoding="utf-8") as output_file:
        for name, value in output_values.items():
            output_file.write(f"{name}={_safe_output_value(value)}\n")


def run_action() -> int:
    """Collect failure context and diff it against the latest successful ancestor."""
    validate_action_inputs(os.environ)
    repository = _required_environment_value(os.environ, "GITHUB_REPOSITORY")
    run_id = _positive_integer(
        _required_environment_value(os.environ, "GITHUB_RUN_ID"), "GITHUB_RUN_ID"
    )
    attempt = _positive_integer(
        _required_environment_value(os.environ, "GITHUB_RUN_ATTEMPT"),
        "GITHUB_RUN_ATTEMPT",
    )
    token = os.environ["INPUT_GITHUB_TOKEN"]
    api_url = os.environ.get("GITHUB_API_URL", "https://api.github.com")
    config = load_config(os.environ.get("INPUT_CONFIG_PATH"), os.environ)
    failed_job = select_failed_job(
        list_attempt_jobs(repository, run_id, attempt, token, api_url),
        _optional_integer(os.environ.get("INPUT_FAILED_JOB_ID"), "failed-job-id"),
    )
    if failed_job is None:
        write_action_outputs(os.environ, "no-failure-context")
        print(
            "Etio found zero or multiple failed jobs; set failed-job-id to choose one."
        )
        return 0

    failure_context = extract_failure_context(
        fetch_job_logs(repository, failed_job.id, token, api_url)
    )
    head_sha = _required_environment_value(os.environ, "GITHUB_SHA")
    workflow_file = config.workflow_file or _workflow_file(os.environ)
    base_sha = find_last_passing_commit(
        repository,
        _branch_name(os.environ),
        head_sha,
        token,
        workflow_file,
        api_url,
    )
    if base_sha is None:
        write_action_outputs(os.environ, "no-passing-commit")
        print("Etio could not find a successful ancestor commit for this workflow.")
        return 0

    diff = build_failure_diff(
        base_sha,
        head_sha,
        _positive_integer(os.environ.get("INPUT_MAX_DIFF_LINES"), "max-diff-lines"),
    )
    breaking_commit, bisection_status = _real_bisect_result(
        config.real_bisect,
        workflow_file,
        repository,
        base_sha,
        head_sha,
        token,
        config.bisect_workflow_inputs,
        run_id,
        attempt,
        config.bisect_max_steps,
        config.bisect_timeout_seconds,
        config.bisect_poll_seconds,
        api_url,
    )
    try:
        diagnosis = diagnose_failure(
            failure_context,
            diff,
            os.environ["INPUT_GROQ_API_KEY"],
            config.groq_model,
            config.diagnosis_timeout_seconds,
        )
    except (DiagnosisError, GroqAPIError) as error:
        message = redact_sensitive_values(str(error))
        write_action_outputs(
            os.environ,
            "diagnosis-failed",
            breaking_commit=breaking_commit,
            diagnosis=message,
            bisection_status=bisection_status,
            auto_pr_status="skipped" if config.auto_pr else "disabled",
        )
        print(f"Etio could not produce a diagnosis: {message}")
        return 0
    auto_pr_url, auto_pr_status = _auto_pr_result(
        config.auto_pr,
        diagnosis,
        repository,
        head_sha,
        run_id,
        attempt,
        token,
        api_url,
        os.environ,
    )
    target = _report_target(config.report_mode, os.environ, repository, head_sha)
    if target is None:
        write_action_outputs(
            os.environ,
            "diagnosed-unreported",
            breaking_commit=breaking_commit,
            diagnosis=diagnosis.summary,
            bisection_status=bisection_status,
            auto_pr_url=auto_pr_url,
            auto_pr_status=auto_pr_status,
        )
        print("Etio produced a diagnosis but no report target was available.")
        return 0
    try:
        report_url = post_diagnosis(
            repository,
            target,
            diagnosis,
            token,
            api_url,
            breaking_commit=breaking_commit or None,
        )
    except GitHubReportError as error:
        message = redact_sensitive_values(str(error))
        write_action_outputs(
            os.environ,
            "report-failed",
            breaking_commit=breaking_commit,
            diagnosis=diagnosis.summary,
            bisection_status=bisection_status,
            auto_pr_url=auto_pr_url,
            auto_pr_status=auto_pr_status,
        )
        print(f"Etio produced a diagnosis but could not publish it: {message}")
        return 0
    write_action_outputs(
        os.environ,
        "diagnosed",
        breaking_commit=breaking_commit,
        report_url=report_url,
        diagnosis=diagnosis.summary,
        bisection_status=bisection_status,
        auto_pr_url=auto_pr_url,
        auto_pr_status=auto_pr_status,
    )
    print(
        "Etio diagnosed "
        f"{len(failure_context)} failure-context characters and "
        f"{len(diff)} diff characters."
    )
    return 0


def _auto_pr_result(
    enabled: bool,
    diagnosis: Diagnosis,
    repository: str,
    head_sha: str,
    run_id: int,
    run_attempt: int,
    token: str,
    api_url: str,
    environment: Mapping[str, str],
) -> tuple[str, str]:
    if not enabled:
        return "", "disabled"
    if diagnosis.confidence != "high" or not diagnosis.suggested_patch:
        print("Etio skipped auto-PR because no high-confidence patch was available.")
        return "", "skipped"
    auto_pr_target = _auto_pr_target(environment, repository, head_sha)
    if auto_pr_target is None:
        print("Etio could not identify a safe base branch for the draft PR.")
        return "", "failed"
    base_branch, source_sha = auto_pr_target
    try:
        result = open_auto_pr(
            repository,
            base_branch,
            source_sha,
            run_id,
            run_attempt,
            diagnosis,
            token,
            Path.cwd(),
            environment.get("GITHUB_SERVER_URL", "https://github.com"),
            api_url,
        )
    except AutoPRError as error:
        message = redact_sensitive_values(str(error))
        print(f"Etio could not open the draft PR: {message}")
        return "", "failed"
    print(f"Etio opened a draft PR: {result.url}")
    return result.url, "created" if result.created else "existing"


def _auto_pr_target(
    environment: Mapping[str, str], repository: str, head_sha: str
) -> tuple[str, str] | None:
    event_path = environment.get("GITHUB_EVENT_PATH")
    payload: Mapping[str, object] = {}
    if event_path:
        try:
            event_payload = json.loads(Path(event_path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            event_payload = None
        if isinstance(event_payload, Mapping):
            payload = event_payload

    payload_repository = payload.get("repository")
    if isinstance(payload_repository, Mapping):
        full_name = payload_repository.get("full_name")
        if full_name is not None and full_name != repository:
            return None

    pull_request = payload.get("pull_request")
    if isinstance(pull_request, Mapping):
        return _same_repository_pull_request_target(pull_request, repository)

    workflow_run = payload.get("workflow_run")
    if isinstance(workflow_run, Mapping):
        pull_requests = workflow_run.get("pull_requests")
        if isinstance(pull_requests, list) and len(pull_requests) == 1:
            pull_request = pull_requests[0]
            if isinstance(pull_request, Mapping):
                return _same_repository_pull_request_target(pull_request, repository)

    if isinstance(payload_repository, Mapping):
        default_branch = payload_repository.get("default_branch")
        if isinstance(default_branch, str) and default_branch.strip():
            return default_branch.strip(), head_sha
    ref_name = environment.get("GITHUB_REF_NAME")
    return (ref_name, head_sha) if ref_name else None


def _same_repository_pull_request_target(
    pull_request: Mapping[str, object], repository: str
) -> tuple[str, str] | None:
    head = pull_request.get("head")
    if not isinstance(head, Mapping):
        return None
    head_repository = head.get("repo")
    branch = head.get("ref")
    source_sha = head.get("sha")
    if (
        not isinstance(head_repository, Mapping)
        or head_repository.get("full_name") != repository
        or not isinstance(branch, str)
        or not branch.strip()
        or not isinstance(source_sha, str)
    ):
        return None
    return branch.strip(), source_sha


def _real_bisect_result(
    enabled: bool,
    workflow_file: str | None,
    repository: str,
    base_sha: str,
    head_sha: str,
    token: str,
    workflow_inputs: Mapping[str, str],
    run_id: int,
    run_attempt: int,
    max_steps: int,
    timeout_seconds: float,
    poll_seconds: float,
    api_url: str,
) -> tuple[str, str]:
    if not enabled:
        return "", "disabled"
    if workflow_file is None:
        print("Etio skipped real bisection because workflow-file is not configured.")
        return "", "failed"
    try:
        result = run_real_bisection(
            repository,
            base_sha,
            head_sha,
            workflow_file,
            token,
            workflow_inputs,
            run_id,
            run_attempt,
            max_steps,
            timeout_seconds,
            poll_seconds,
            api_url,
        )
    except RealBisectError as error:
        print(
            "Etio could not complete real bisection: "
            f"{redact_sensitive_values(str(error))}"
        )
        return "", "failed"
    if not result.complete or result.breaking_commit is None:
        reason = redact_sensitive_values(
            result.reason or "no exact result was returned"
        )
        print(f"Etio stopped real bisection without an exact commit: {reason}")
        return "", "incomplete"
    print(f"Etio confirmed breaking commit {result.breaking_commit}.")
    return result.breaking_commit, "confirmed"


def _required_environment_value(environment: Mapping[str, str], name: str) -> str:
    value = environment.get(name)
    if value:
        return value
    raise ValueError(f"Etio requires the GitHub Actions environment variable {name}.")


def _optional_integer(value: str | None, name: str) -> int | None:
    if not value:
        return None
    return _positive_integer(value, name)


def _positive_integer(value: str | None, name: str) -> int:
    if value is None:
        raise ValueError(f"Etio requires a value for {name}.")
    try:
        parsed = int(value)
    except ValueError as error:
        raise ValueError(f"Etio input {name} must be a positive integer.") from error
    if parsed < 1:
        raise ValueError(f"Etio input {name} must be a positive integer.")
    return parsed


def _branch_name(environment: Mapping[str, str]) -> str:
    variable_name = (
        "GITHUB_HEAD_REF" if environment.get("GITHUB_HEAD_REF") else "GITHUB_REF_NAME"
    )
    return _required_environment_value(environment, variable_name)


def _workflow_file(environment: Mapping[str, str]) -> str | None:
    configured_file = environment.get("INPUT_WORKFLOW_FILE")
    if configured_file:
        return configured_file
    _, marker, workflow_and_ref = environment.get("GITHUB_WORKFLOW_REF", "").partition(
        ".github/workflows/"
    )
    if not marker:
        return None
    workflow_file, _, _ = workflow_and_ref.partition("@")
    return workflow_file or None


def _report_target(
    report_mode: str,
    environment: Mapping[str, str],
    repository: str,
    commit_sha: str,
) -> ReportTarget | None:
    if report_mode == "none":
        return None
    if report_mode == "commit":
        return ReportTarget("commit", commit_sha)
    requested_pr_number = _optional_integer(
        environment.get("INPUT_PR_NUMBER"), "pr-number"
    )
    pr_number = requested_pr_number or _event_pull_request_number(
        environment, repository
    )
    if pr_number is not None:
        return ReportTarget("pull_request", pr_number)
    if report_mode == "pull-request":
        raise ValueError(
            "Etio needs pr-number or a matching pull_request event to report to a PR."
        )
    return None


def _event_pull_request_number(
    environment: Mapping[str, str], repository: str
) -> int | None:
    event_path = environment.get("GITHUB_EVENT_PATH")
    if not event_path:
        return None
    try:
        payload = json.loads(Path(event_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, Mapping):
        return None
    payload_repository = payload.get("repository")
    if (
        not isinstance(payload_repository, Mapping)
        or payload_repository.get("full_name") != repository
    ):
        return None
    direct_pull_request = payload.get("pull_request")
    direct_number = _mapping_positive_integer(direct_pull_request, "number")
    if direct_number is not None:
        return direct_number
    workflow_run = payload.get("workflow_run")
    if not isinstance(workflow_run, Mapping):
        return None
    associated_pull_requests = workflow_run.get("pull_requests")
    if not isinstance(associated_pull_requests, list):
        return None
    numbers = [
        number
        for pull_request in associated_pull_requests
        if (number := _mapping_positive_integer(pull_request, "number")) is not None
    ]
    return numbers[0] if len(numbers) == 1 else None


def _mapping_positive_integer(value: object, key: str) -> int | None:
    if not isinstance(value, Mapping):
        return None
    candidate = value.get(key)
    return candidate if isinstance(candidate, int) and candidate > 0 else None


def _safe_output_value(value: str) -> str:
    normalized = redact_sensitive_values(value).replace("\r", " ").replace("\n", " ")
    return normalized.strip()[:MAX_OUTPUT_VALUE_CHARACTERS]


if __name__ == "__main__":
    raise SystemExit(run_action())
