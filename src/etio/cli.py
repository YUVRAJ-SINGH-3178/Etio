"""Command-line entry point for the Etio composite action."""

from __future__ import annotations

import os
from collections.abc import Mapping

from etio.bisect import build_failure_diff, find_last_passing_commit
from etio.logs import (
    extract_failure_context,
    fetch_job_logs,
    list_attempt_jobs,
    select_failed_job,
)


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
    }
    with open(output_path, "a", encoding="utf-8") as output_file:
        for name, value in output_values.items():
            output_file.write(f"{name}={value.replace(chr(10), ' ').strip()}\n")


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
    base_sha = find_last_passing_commit(
        repository,
        _branch_name(os.environ),
        head_sha,
        token,
        _workflow_file(os.environ),
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
    write_action_outputs(os.environ, "context-and-diff-extracted")
    print(
        "Etio extracted "
        f"{len(failure_context)} failure-context characters and "
        f"{len(diff)} diff characters."
    )
    return 0


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


if __name__ == "__main__":
    raise SystemExit(run_action())
