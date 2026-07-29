"""Retrieve GitHub Actions logs and isolate useful failure details."""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

GITHUB_API_URL = "https://api.github.com"
MAX_LOG_BYTES = 10 * 1024 * 1024
DEFAULT_FAILURE_LINES = 80
DEFAULT_FAILURE_CHARACTERS = 12_000
FAILED_CONCLUSIONS = frozenset({"failure", "timed_out", "startup_failure"})
UrlOpener = Callable[[Request], Any]


class GitHubLogError(RuntimeError):
    """Raised when Etio cannot retrieve or decode an Actions job log."""


@dataclass(frozen=True)
class WorkflowJob:
    """The portion of a GitHub workflow-job response Etio needs."""

    id: int
    name: str
    status: str
    conclusion: str | None


class _NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(
        self,
        request: Request,
        file_pointer: Any,
        code: int,
        message: str,
        headers: Any,
        new_url: str,
    ) -> None:
        return None


def list_attempt_jobs(
    repository: str,
    run_id: int,
    attempt: int,
    token: str,
    api_url: str = GITHUB_API_URL,
    opener: UrlOpener | None = None,
) -> list[WorkflowJob]:
    """List jobs from one workflow attempt, without mixing rerun attempts."""
    jobs: list[WorkflowJob] = []
    page = 1
    request_opener = opener or _open_without_redirect
    while True:
        payload = _github_json(
            (
                f"{api_url}/repos/{repository}/actions/runs/{run_id}"
                f"/attempts/{attempt}/jobs?per_page=100&page={page}"
            ),
            token,
            request_opener,
        )
        page_jobs = payload.get("jobs")
        if not isinstance(page_jobs, list):
            raise GitHubLogError(
                "GitHub returned a workflow-job response without jobs."
            )
        jobs.extend(_workflow_job(job) for job in page_jobs)
        if len(page_jobs) < 100:
            return jobs
        page += 1


def select_failed_job(
    jobs: Sequence[WorkflowJob], requested_job_id: int | None = None
) -> WorkflowJob | None:
    """Return the requested failed job, or the only failed job when unambiguous."""
    failed_jobs = [
        job
        for job in jobs
        if job.status == "completed" and job.conclusion in FAILED_CONCLUSIONS
    ]
    if requested_job_id is not None:
        requested_job = next((job for job in jobs if job.id == requested_job_id), None)
        if requested_job is None:
            raise GitHubLogError(
                "Workflow job id "
                f"{requested_job_id} does not belong to this run attempt."
            )
        if requested_job not in failed_jobs:
            raise GitHubLogError(
                f"Workflow job id {requested_job_id} has not completed with a failure."
            )
        return requested_job
    if len(failed_jobs) == 1:
        return failed_jobs[0]
    return None


def fetch_job_logs(
    repository: str,
    job_id: int,
    token: str,
    api_url: str = GITHUB_API_URL,
    opener: UrlOpener | None = None,
) -> str:
    """Download a completed job's plain-text log without persisting it to disk."""
    request_opener = opener or _open_without_redirect
    endpoint = f"{api_url}/repos/{repository}/actions/jobs/{job_id}/logs"
    for request_number in range(2):
        redirect_url = _log_download_url(endpoint, token, request_opener)
        try:
            return _download_log(redirect_url, request_opener)
        except _TemporaryLogUrlExpired:
            if request_number == 1:
                raise GitHubLogError(
                    "GitHub's temporary log URL expired before Etio could read it."
                ) from None
    raise AssertionError("The log download retry loop must return or raise.")


def extract_failure_context(
    raw_logs: str,
    max_lines: int = DEFAULT_FAILURE_LINES,
    max_characters: int = DEFAULT_FAILURE_CHARACTERS,
) -> str:
    """Return the error-focused portion of a noisy Actions log."""
    if max_lines < 1:
        raise ValueError("max_lines must be at least 1.")
    if max_characters < 1:
        raise ValueError("max_characters must be at least 1.")
    lines = [_clean_log_line(line) for line in raw_logs.splitlines()]
    useful_lines = [line for line in lines if line]
    if not useful_lines:
        return "No log output was available for diagnosis."
    start, end = _failure_window(useful_lines, max_lines)
    return _truncate_context("\n".join(useful_lines[start:end]), max_characters)


class _TemporaryLogUrlExpired(RuntimeError):
    pass


def _workflow_job(payload: object) -> WorkflowJob:
    if not isinstance(payload, Mapping):
        raise GitHubLogError("GitHub returned a malformed workflow job.")
    job_id = payload.get("id")
    name = payload.get("name")
    status = payload.get("status")
    conclusion = payload.get("conclusion")
    if (
        not isinstance(job_id, int)
        or not isinstance(name, str)
        or not isinstance(status, str)
    ):
        raise GitHubLogError(
            "GitHub returned a workflow job with missing required fields."
        )
    if conclusion is not None and not isinstance(conclusion, str):
        raise GitHubLogError(
            "GitHub returned a workflow job with an invalid conclusion."
        )
    return WorkflowJob(job_id, name, status, conclusion)


def _github_json(url: str, token: str, opener: UrlOpener) -> Mapping[str, Any]:
    response = _github_response(url, token, opener)
    try:
        payload = json.loads(_read_limited(response).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise GitHubLogError("GitHub returned invalid workflow-job JSON.") from error
    finally:
        response.close()
    if not isinstance(payload, Mapping):
        raise GitHubLogError("GitHub returned an unexpected workflow-job response.")
    return payload


def _log_download_url(endpoint: str, token: str, opener: UrlOpener) -> str:
    response = _github_response(endpoint, token, opener)
    try:
        status = response.getcode()
        if status not in {301, 302, 303, 307, 308}:
            raise GitHubLogError(
                f"GitHub returned status {status} instead of a log download redirect."
            )
        location = response.headers.get("Location")
    finally:
        response.close()
    if not isinstance(location, str) or not location:
        raise GitHubLogError("GitHub did not provide a temporary log download URL.")
    if urlparse(location).scheme != "https":
        raise GitHubLogError("GitHub provided an insecure temporary log download URL.")
    return location


def _github_response(url: str, token: str, opener: UrlOpener) -> Any:
    request = Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        return opener(request)
    except HTTPError as error:
        if error.code in {301, 302, 303, 307, 308}:
            return error
        raise GitHubLogError(
            f"GitHub could not retrieve Actions logs ({error.code} {error.reason})."
        ) from error
    except URLError as error:
        raise GitHubLogError("GitHub Actions logs could not be reached.") from error


def _download_log(url: str, opener: UrlOpener) -> str:
    request = Request(url, headers={"Accept": "text/plain"})
    try:
        response = opener(request)
    except HTTPError as error:
        if error.code in {403, 404}:
            raise _TemporaryLogUrlExpired from error
        raise GitHubLogError(
            f"GitHub could not download the job log ({error.code} {error.reason})."
        ) from error
    except URLError as error:
        raise GitHubLogError(
            "GitHub's temporary log download could not be reached."
        ) from error
    try:
        return _read_limited(response).decode("utf-8", errors="replace")
    finally:
        response.close()


def _read_limited(response: Any) -> bytes:
    content = response.read(MAX_LOG_BYTES + 1)
    if len(content) > MAX_LOG_BYTES:
        raise GitHubLogError(
            "The job log is too large to process safely (maximum is 10 MiB)."
        )
    return content


def _open_without_redirect(request: Request) -> Any:
    try:
        return build_opener(_NoRedirectHandler()).open(request)
    except HTTPError as error:
        if error.code in {301, 302, 303, 307, 308}:
            return error
        raise


def _clean_log_line(line: str) -> str:
    timestampless = re.sub(
        r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z\s+", "", line
    )
    return re.sub(r"^##\[(?:group|endgroup|debug|command)\]\s*", "", timestampless)


def _failure_window(lines: list[str], max_lines: int) -> tuple[int, int]:
    traceback_index = next(
        (
            index
            for index, line in enumerate(lines)
            if "Traceback (most recent call last):" in line
        ),
        None,
    )
    if traceback_index is not None:
        return traceback_index, min(len(lines), traceback_index + max_lines)
    anchor_index = _best_failure_anchor(lines)
    if anchor_index is None:
        return max(0, len(lines) - max_lines), len(lines)
    before = min(12, max_lines // 2)
    start = max(0, anchor_index - before)
    return start, min(len(lines), start + max_lines)


def _best_failure_anchor(lines: list[str]) -> int | None:
    scored_lines = [(_failure_score(line), index) for index, line in enumerate(lines)]
    positive_scores = [entry for entry in scored_lines if entry[0] > 0]
    if not positive_scores:
        return None
    return max(positive_scores)[1]


def _failure_score(line: str) -> int:
    lowered = line.lower()
    if "traceback (most recent call last):" in lowered:
        return 100
    if "##[error]" in lowered:
        return 90
    if re.search(r"\b(?:assertionerror|[a-z_]*error|exception):", line):
        return 80
    if re.search(r"\berror\[[a-z0-9]+\]:", lowered):
        return 80
    if line.startswith("FAILED ") or "build failed" in lowered:
        return 70
    if re.search(r"\bfatal(?: error)?:", lowered):
        return 70
    if " error:" in lowered or line.startswith("error:"):
        return 60
    if "failed" in lowered or "failure" in lowered:
        return 20
    return 0


def _truncate_context(context: str, max_characters: int) -> str:
    if len(context) <= max_characters:
        return context
    return (
        f"{context[:max_characters].rstrip()}\n[Etio truncated this failure context.]"
    )
