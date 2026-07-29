"""Command-line entry point for the Etio composite action."""

import os
from collections.abc import Mapping

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


def write_action_outputs(environment: Mapping[str, str], status: str) -> None:
    output_path = environment.get("GITHUB_OUTPUT")
    if not output_path:
        return
    with open(output_path, "a", encoding="utf-8") as output_file:
        output_file.write(f"status={status}\n")
        output_file.write("breaking-commit=\n")
        output_file.write("report-url=\n")
        output_file.write("diagnosis=\n")


def run_action() -> int:
    """Retrieve a completed job's logs and extract failure context locally."""
    validate_action_inputs(os.environ)
    repository = _required_environment_value(os.environ, "GITHUB_REPOSITORY")
    run_id = int(_required_environment_value(os.environ, "GITHUB_RUN_ID"))
    attempt = int(_required_environment_value(os.environ, "GITHUB_RUN_ATTEMPT"))
    api_url = os.environ.get("GITHUB_API_URL", "https://api.github.com")
    requested_job_id = _optional_integer(os.environ.get("INPUT_FAILED_JOB_ID"))
    jobs = list_attempt_jobs(
        repository,
        run_id,
        attempt,
        os.environ["INPUT_GITHUB_TOKEN"],
        api_url,
    )
    failed_job = select_failed_job(jobs, requested_job_id)
    if failed_job is None:
        write_action_outputs(os.environ, "no-failure-context")
        print(
            "Etio found zero or multiple failed jobs; set failed-job-id to choose one."
        )
        return 0
    raw_logs = fetch_job_logs(
        repository,
        failed_job.id,
        os.environ["INPUT_GITHUB_TOKEN"],
        api_url,
    )
    failure_context = extract_failure_context(raw_logs)
    write_action_outputs(os.environ, "context-extracted")
    print(
        f"Etio extracted {len(failure_context)} characters of failure context locally."
    )
    return 0


def _required_environment_value(environment: Mapping[str, str], name: str) -> str:
    value = environment.get(name)
    if value:
        return value
    raise ValueError(f"Etio requires the GitHub Actions environment variable {name}.")


def _optional_integer(value: str | None) -> int | None:
    if not value:
        return None
    try:
        return int(value)
    except ValueError as error:
        raise ValueError(
            "Etio input failed-job-id must be a numeric workflow job id."
        ) from error


if __name__ == "__main__":
    raise SystemExit(run_action())
