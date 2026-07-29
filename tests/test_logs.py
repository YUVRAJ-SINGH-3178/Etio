import json
from collections.abc import Iterator
from typing import Any
from urllib.error import HTTPError

import pytest

from etio.logs import (
    GitHubLogError,
    WorkflowJob,
    extract_failure_context,
    fetch_job_logs,
    list_attempt_jobs,
    select_failed_job,
)


class FakeResponse:
    def __init__(
        self,
        payload: bytes,
        status: int = 200,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.payload = payload
        self.status = status
        self.headers = headers or {}

    def read(self, size: int = -1) -> bytes:
        return self.payload if size < 0 else self.payload[:size]

    def getcode(self) -> int:
        return self.status

    def close(self) -> None:
        return None


def response_opener(responses: Iterator[FakeResponse], requests: list[Any]) -> Any:
    def open_request(request: Any) -> FakeResponse:
        requests.append(request)
        return next(responses)

    return open_request


def test_list_attempt_jobs_paginates_the_requested_attempt() -> None:
    first_page = [
        {"id": index, "name": f"job-{index}", "status": "completed"}
        for index in range(100)
    ]
    second_page = [
        {"id": 101, "name": "failed", "status": "completed", "conclusion": "failure"}
    ]
    requests: list[Any] = []
    opener = response_opener(
        iter(
            [
                FakeResponse(json.dumps({"jobs": first_page}).encode()),
                FakeResponse(json.dumps({"jobs": second_page}).encode()),
            ]
        ),
        requests,
    )

    jobs = list_attempt_jobs("octo/etio", 42, 3, "secret", opener=opener)

    assert len(jobs) == 101
    assert "attempts/3/jobs" in requests[0].full_url
    assert "page=2" in requests[1].full_url


def test_select_failed_job_requires_an_unambiguous_failure() -> None:
    jobs = [
        WorkflowJob(1, "test", "completed", "failure"),
        WorkflowJob(2, "lint", "completed", "timed_out"),
    ]

    assert select_failed_job(jobs) is None
    assert select_failed_job(jobs, 2) == jobs[1]


def test_select_failed_job_rejects_a_non_failed_requested_job() -> None:
    jobs = [WorkflowJob(1, "test", "in_progress", None)]

    with pytest.raises(GitHubLogError, match="has not completed with a failure"):
        select_failed_job(jobs, 1)


def test_fetch_job_logs_follows_redirect_without_authorization() -> None:
    requests: list[Any] = []
    opener = response_opener(
        iter(
            [
                FakeResponse(b"", 302, {"Location": "https://logs.example.test/log"}),
                FakeResponse(b"test output"),
            ]
        ),
        requests,
    )

    assert fetch_job_logs("octo/etio", 17, "secret", opener=opener) == "test output"
    assert requests[0].get_header("Authorization") == "Bearer secret"
    assert requests[1].get_header("Authorization") is None


def test_fetch_job_logs_retries_an_expired_temporary_url() -> None:
    requests: list[Any] = []

    def open_request(request: Any) -> FakeResponse:
        requests.append(request)
        if len(requests) == 1:
            return FakeResponse(
                b"", 302, {"Location": "https://logs.example.test/first"}
            )
        if len(requests) == 2:
            raise HTTPError(request.full_url, 403, "Forbidden", {}, None)
        if len(requests) == 3:
            return FakeResponse(
                b"", 302, {"Location": "https://logs.example.test/second"}
            )
        return FakeResponse(b"retried log")

    assert (
        fetch_job_logs("octo/etio", 17, "secret", opener=open_request) == "retried log"
    )
    assert len(requests) == 4


def test_fetch_job_logs_rejects_an_insecure_redirect() -> None:
    opener = response_opener(
        iter([FakeResponse(b"", 302, {"Location": "http://logs.example.test/log"})]),
        [],
    )

    with pytest.raises(GitHubLogError, match="insecure"):
        fetch_job_logs("octo/etio", 17, "secret", opener=opener)


def test_extract_failure_context_keeps_traceback() -> None:
    raw_logs = """2026-07-27T10:00:00Z starting tests
2026-07-27T10:00:01Z Traceback (most recent call last):
2026-07-27T10:00:01Z   File "app.py", line 9, in <module>
2026-07-27T10:00:01Z     raise ValueError("missing patient id")
2026-07-27T10:00:01Z ValueError: missing patient id
2026-07-27T10:00:02Z ##[error]Process completed with exit code 1.
"""

    context = extract_failure_context(raw_logs)

    assert "Traceback" in context
    assert "ValueError: missing patient id" in context
    assert "starting tests" not in context


def test_extract_failure_context_uses_failed_test_window() -> None:
    raw_logs = "\n".join(
        ["setup complete"] * 40
        + [
            "E       AssertionError: expected 200 but received 500",
            "FAILED tests/test_api.py",
        ]
    )

    context = extract_failure_context(raw_logs)

    assert "AssertionError" in context
    assert "FAILED tests/test_api.py" in context


def test_extract_failure_context_rejects_invalid_bounds() -> None:
    with pytest.raises(ValueError, match="max_lines"):
        extract_failure_context("error", max_lines=0)
