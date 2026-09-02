import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from etio.bisect import BisectError, build_failure_diff, find_last_passing_commit


class FakeResponse:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def read(self) -> bytes:
        return self.payload


def completed(returncode: int, stdout: str = "", stderr: str = "") -> Any:
    return subprocess.CompletedProcess(
        ["git"], returncode, stdout=stdout, stderr=stderr
    )


def test_find_last_passing_commit_uses_the_requested_workflow_and_branch() -> None:
    requests: list[Any] = []
    responses = iter(
        [
            FakeResponse(
                json.dumps(
                    {
                        "workflow_runs": [
                            {"head_sha": "not-ancestor", "conclusion": "success"},
                            {"head_sha": "passing-ancestor", "conclusion": "success"},
                        ]
                    }
                ).encode()
            )
        ]
    )

    def opener(request: Any) -> FakeResponse:
        requests.append(request)
        return next(responses)

    def git_runner(arguments: list[str], _: Path | None) -> Any:
        if arguments[2] == "not-ancestor":
            return completed(1)
        return completed(0)

    assert (
        find_last_passing_commit(
            "octo/etio",
            "feature/symptoms",
            "failing-head",
            "token",
            ".github/workflows/ci.yml",
            opener=opener,
            git_runner=git_runner,
        )
        == "passing-ancestor"
    )
    assert "/actions/workflows/ci.yml/runs?" in requests[0].full_url
    assert "branch=feature%2Fsymptoms" in requests[0].full_url
    assert "status=completed" in requests[0].full_url


def test_find_last_passing_commit_paginates_runs() -> None:
    first_page = [
        {"head_sha": f"sha-{index}", "conclusion": "failure"} for index in range(100)
    ]
    responses = iter(
        [
            FakeResponse(json.dumps({"workflow_runs": first_page}).encode()),
            FakeResponse(
                json.dumps(
                    {
                        "workflow_runs": [
                            {"head_sha": "passing-ancestor", "conclusion": "success"}
                        ]
                    }
                ).encode()
            ),
        ]
    )
    requests: list[Any] = []

    def opener(request: Any) -> FakeResponse:
        requests.append(request)
        return next(responses)

    assert (
        find_last_passing_commit(
            "octo/etio",
            "main",
            "failing-head",
            "token",
            opener=opener,
            git_runner=lambda *_: completed(0),
        )
        == "passing-ancestor"
    )
    assert "page=2" in requests[1].full_url


def test_find_last_passing_commit_returns_none_without_successful_ancestor() -> None:
    response = FakeResponse(
        json.dumps(
            {
                "workflow_runs": [
                    {"head_sha": "failing-head", "conclusion": "success"},
                    {"head_sha": "other-head", "conclusion": "failure"},
                ]
            }
        ).encode()
    )

    assert (
        find_last_passing_commit(
            "octo/etio",
            "main",
            "failing-head",
            "token",
            opener=lambda _: response,
            git_runner=lambda *_: completed(0),
        )
        is None
    )


def test_find_last_passing_commit_reports_unexpected_git_failure() -> None:
    response = FakeResponse(
        json.dumps(
            {"workflow_runs": [{"head_sha": "possible-base", "conclusion": "success"}]}
        ).encode()
    )

    with pytest.raises(BisectError, match="could not compare"):
        find_last_passing_commit(
            "octo/etio",
            "main",
            "failing-head",
            "token",
            opener=lambda _: response,
            git_runner=lambda *_: completed(128, stderr="unknown revision"),
        )


def test_build_failure_diff_uses_a_bounded_unified_diff() -> None:
    called_arguments: list[list[str]] = []

    def git_runner(arguments: list[str], _: Path | None) -> Any:
        called_arguments.append(arguments)
        return completed(0, "one\ntwo\nthree\nfour\n")

    diff = build_failure_diff("base", "head", 3, git_runner=git_runner)

    assert called_arguments == [["diff", "--no-ext-diff", "--unified=3", "base..head"]]
    assert diff.splitlines() == ["one", "two", "[Etio truncated this diff to 3 lines.]"]


def test_build_failure_diff_rejects_invalid_limit() -> None:
    with pytest.raises(ValueError, match="max_lines"):
        build_failure_diff("base", "head", 0, git_runner=lambda *_: completed(0))


def test_build_failure_diff_preserves_git_errors() -> None:
    with pytest.raises(BisectError, match="unknown revision"):
        build_failure_diff(
            "base",
            "head",
            10,
            git_runner=lambda *_: completed(128, stderr="unknown revision"),
        )
