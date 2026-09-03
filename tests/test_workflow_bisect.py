import json
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from urllib.error import HTTPError

import pytest

from etio.real_bisect import (
    RealBisectError,
    RealBisectInconclusiveError,
    confirm_breaking_commit,
    run_real_bisection,
)

BASE = "0" * 40
MIDDLE = "1" * 40
HEAD = "2" * 40


class FakeResponse:
    def __init__(self, payload: bytes, status: int = 200) -> None:
        self.payload = payload
        self.status = status

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def read(self) -> bytes:
        return self.payload


def json_response(payload: object, status: int = 200) -> FakeResponse:
    return FakeResponse(json.dumps(payload).encode("utf-8"), status)


def empty_response(status: int) -> FakeResponse:
    return FakeResponse(b"", status)


def completed(returncode: int, stdout: str = "", stderr: str = "") -> Any:
    return subprocess.CompletedProcess(
        ["git"], returncode, stdout=stdout, stderr=stderr
    )


def response_opener(
    responses: Iterator[FakeResponse | HTTPError], requests: list[Any]
) -> Any:
    def open_request(request: Any, **_: object) -> FakeResponse:
        requests.append(request)
        response = next(responses)
        if isinstance(response, HTTPError):
            raise response
        return response

    return open_request


def first_parent_runner(arguments: list[str], _: Path | None) -> Any:
    if arguments[0] == "merge-base":
        return completed(0)
    return completed(0, f"{MIDDLE}\n{HEAD}\n")


def test_confirm_breaking_commit_proves_both_bounds_before_binary_search() -> None:
    candidates = (BASE, "3" * 40, "4" * 40, MIDDLE, HEAD)
    outcomes = {
        BASE: "passed",
        "3" * 40: "passed",
        "4" * 40: "passed",
        MIDDLE: "failed",
        HEAD: "failed",
    }
    calls: list[str] = []

    def run_candidate(commit_sha: str) -> str:
        calls.append(commit_sha)
        return outcomes[commit_sha]

    result = confirm_breaking_commit(candidates, run_candidate, max_steps=5)

    assert result.breaking_commit == MIDDLE
    assert result.complete is True
    assert calls == [BASE, HEAD, "4" * 40, MIDDLE]


def test_confirm_breaking_commit_returns_an_explicit_budget_limit() -> None:
    calls: list[str] = []

    result = confirm_breaking_commit(
        (BASE, HEAD),
        lambda commit_sha: calls.append(commit_sha) or "passed",
        max_steps=1,
    )

    assert result.breaking_commit is None
    assert result.complete is False
    assert (
        result.reason == "The configured maximum number of workflow runs was reached."
    )
    assert calls == [BASE]


def test_confirm_breaking_commit_rejects_a_non_reproducing_head() -> None:
    with pytest.raises(RealBisectInconclusiveError, match="did not reproduce"):
        confirm_breaking_commit(
            (BASE, HEAD),
            lambda _: "passed",
            max_steps=2,
        )


def test_real_bisection_dispatches_each_candidate_on_a_temporary_ref() -> None:
    requests: list[Any] = []
    opener = response_opener(
        iter(
            [
                json_response({}, 201),
                json_response({"workflow_run_id": 101}, 200),
                json_response({"status": "completed", "conclusion": "success"}),
                empty_response(204),
                json_response({}, 201),
                json_response({"workflow_run_id": 102}, 200),
                json_response({"status": "completed", "conclusion": "failure"}),
                empty_response(204),
                json_response({}, 201),
                json_response({"workflow_run_id": 103}, 200),
                json_response({"status": "completed", "conclusion": "failure"}),
                empty_response(204),
            ]
        ),
        requests,
    )
    nonces = iter(["base", "head", "middle"])

    result = run_real_bisection(
        "octo/etio",
        BASE,
        HEAD,
        ".github/workflows/bisect.yml",
        "token",
        {"test-command": "pytest tests/test_symptoms.py"},
        42,
        1,
        3,
        30,
        1,
        opener=opener,
        git_runner=first_parent_runner,
        nonce_factory=lambda: next(nonces),
    )

    assert result.breaking_commit == MIDDLE
    assert result.attempted_commits == (BASE, HEAD, MIDDLE)
    assert result.complete is True
    created_refs = [
        json.loads(request.data.decode("utf-8"))
        for request in requests
        if request.method == "POST" and request.full_url.endswith("/git/refs")
    ]
    assert [ref["sha"] for ref in created_refs] == [BASE, HEAD, MIDDLE]
    assert all(
        ref["ref"].startswith("refs/heads/etio/bisect/42-1-") for ref in created_refs
    )
    dispatches = [
        request
        for request in requests
        if request.method == "POST" and "/dispatches" in request.full_url
    ]
    assert len(dispatches) == 3
    assert all(
        json.loads(request.data.decode("utf-8"))["inputs"]
        == {"test-command": "pytest tests/test_symptoms.py"}
        for request in dispatches
    )
    assert sum(request.method == "DELETE" for request in requests) == 3


def test_real_bisection_falls_back_to_a_unique_temporary_ref_lookup() -> None:
    requests: list[Any] = []
    base_ref = f"etio/bisect/42-1-{BASE[:12]}-base"
    head_ref = f"etio/bisect/42-1-{HEAD[:12]}-head"
    opener = response_opener(
        iter(
            [
                json_response({}, 201),
                empty_response(204),
                json_response(
                    {
                        "workflow_runs": [
                            {
                                "id": 101,
                                "event": "workflow_dispatch",
                                "head_branch": base_ref,
                                "head_sha": BASE,
                                "status": "completed",
                                "conclusion": "success",
                            }
                        ]
                    }
                ),
                empty_response(204),
                json_response({}, 201),
                empty_response(204),
                json_response(
                    {
                        "workflow_runs": [
                            {
                                "id": 102,
                                "event": "workflow_dispatch",
                                "head_branch": head_ref,
                                "head_sha": HEAD,
                                "status": "completed",
                                "conclusion": "failure",
                            }
                        ]
                    }
                ),
                empty_response(204),
            ]
        ),
        requests,
    )

    result = run_real_bisection(
        "octo/etio",
        BASE,
        HEAD,
        "bisect.yml",
        "token",
        {},
        42,
        1,
        2,
        30,
        1,
        opener=opener,
        git_runner=lambda arguments, path: (
            completed(0) if arguments[0] == "merge-base" else completed(0, f"{HEAD}\n")
        ),
        nonce_factory=iter(["base", "head"]).__next__,
    )

    assert result.breaking_commit == HEAD
    lookup_requests = [
        request
        for request in requests
        if request.method == "GET"
        and "/actions/workflows/bisect.yml/runs?" in request.full_url
    ]
    assert len(lookup_requests) == 2
    assert all(
        "event=workflow_dispatch" in request.full_url for request in lookup_requests
    )


def test_real_bisection_deletes_a_created_ref_when_dispatch_is_rejected() -> None:
    requests: list[Any] = []
    opener = response_opener(
        iter(
            [
                json_response({}, 201),
                HTTPError(
                    "https://api.github.com",
                    422,
                    "gsk_sensitive-value",
                    {},
                    None,
                ),
                empty_response(204),
            ]
        ),
        requests,
    )

    with pytest.raises(RealBisectError) as error:
        run_real_bisection(
            "octo/etio",
            BASE,
            HEAD,
            "bisect.yml",
            "token",
            {},
            42,
            1,
            2,
            30,
            1,
            opener=opener,
            git_runner=lambda arguments, path: (
                completed(0)
                if arguments[0] == "merge-base"
                else completed(0, f"{HEAD}\n")
            ),
            nonce_factory=lambda: "base",
        )

    assert "gsk_" not in str(error.value)
    assert requests[-1].method == "DELETE"
