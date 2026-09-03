import json
from collections.abc import Iterator
from typing import Any
from urllib.error import HTTPError

import pytest

from etio.models import Diagnosis
from etio.report import (
    ETIO_MARKER,
    GitHubReportError,
    ReportTarget,
    build_diagnosis_comment,
    post_diagnosis,
)


class FakeResponse:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def read(self) -> bytes:
        return self.payload


def diagnosis() -> Diagnosis:
    return Diagnosis(
        summary="The token=super-secret was removed before the assertion.",
        root_cause="The test now reads an uninitialized value.",
        suggested_patch="+ value = build_value()",
        confidence="high",
    )


def json_response(payload: object) -> FakeResponse:
    return FakeResponse(json.dumps(payload).encode("utf-8"))


def response_opener(responses: Iterator[FakeResponse], requests: list[Any]) -> Any:
    def open_request(request: Any, **_: object) -> FakeResponse:
        requests.append(request)
        return next(responses)

    return open_request


def test_post_diagnosis_creates_a_redacted_pull_request_comment() -> None:
    requests: list[Any] = []
    opener = response_opener(
        iter(
            [
                json_response({"id": 7, "login": "github-actions[bot]"}),
                json_response([]),
                json_response({"html_url": "https://github.test/comment/1"}),
            ]
        ),
        requests,
    )

    url = post_diagnosis(
        "octo/etio",
        ReportTarget("pull_request", 42),
        diagnosis(),
        "token",
        opener=opener,
    )

    assert url == "https://github.test/comment/1"
    create_request = requests[2]
    assert create_request.method == "POST"
    assert create_request.full_url.endswith("/issues/42/comments")
    body = json.loads(create_request.data.decode("utf-8"))["body"]
    assert ETIO_MARKER in body
    assert "<!-- etio-diagnosis:pull_request:42 -->" in body
    assert "super-secret" not in body
    assert "[REDACTED]" in body
    assert create_request.get_header("Authorization") == "Bearer token"


def test_post_diagnosis_updates_only_the_current_actor_scoped_comment() -> None:
    requests: list[Any] = []
    comments = [
        {
            "id": 900,
            "body": f"{ETIO_MARKER}\n<!-- etio-diagnosis:pull_request:42 -->",
            "user": {"id": 99, "login": "attacker"},
        },
        {
            "id": 123,
            "body": f"{ETIO_MARKER}\n<!-- etio-diagnosis:pull_request:42 -->",
            "user": {"id": 7, "login": "github-actions[bot]"},
        },
        {
            "id": 124,
            "body": f"{ETIO_MARKER}\n<!-- etio-diagnosis:commit:deadbeef -->",
            "user": {"id": 7, "login": "github-actions[bot]"},
        },
    ]
    opener = response_opener(
        iter(
            [
                json_response({"id": 7, "login": "github-actions[bot]"}),
                json_response(comments),
                json_response({"html_url": "https://github.test/comment/123"}),
            ]
        ),
        requests,
    )

    post_diagnosis(
        "octo/etio",
        ReportTarget("pull_request", 42),
        diagnosis(),
        "token",
        opener=opener,
    )

    update_request = requests[2]
    assert update_request.method == "PATCH"
    assert update_request.full_url.endswith("/issues/comments/123")


def test_post_diagnosis_paginates_and_updates_the_newest_owned_comment() -> None:
    requests: list[Any] = []
    first_page = [
        {"id": index, "body": "other", "user": {"id": 7}} for index in range(100)
    ]
    second_page = [
        {
            "id": 1001,
            "body": f"{ETIO_MARKER}\n<!-- etio-diagnosis:pull_request:42 -->",
            "user": {"id": 7},
        },
        {
            "id": 1002,
            "body": f"{ETIO_MARKER}\n<!-- etio-diagnosis:pull_request:42 -->",
            "user": {"id": 7},
        },
    ]
    opener = response_opener(
        iter(
            [
                json_response({"id": 7, "login": "github-actions[bot]"}),
                json_response(first_page),
                json_response(second_page),
                json_response({"html_url": "https://github.test/comment/1002"}),
            ]
        ),
        requests,
    )

    post_diagnosis(
        "octo/etio",
        ReportTarget("pull_request", 42),
        diagnosis(),
        "token",
        opener=opener,
    )

    assert "page=2" in requests[2].full_url
    assert requests[3].full_url.endswith("/issues/comments/1002")


def test_post_diagnosis_uses_commit_comment_routes() -> None:
    requests: list[Any] = []
    commit_sha = "0123456789abcdef0123456789abcdef01234567"
    opener = response_opener(
        iter(
            [
                json_response({"id": 7, "login": "github-actions[bot]"}),
                json_response(
                    [
                        {
                            "id": 88,
                            "body": (
                                f"{ETIO_MARKER}\n"
                                f"<!-- etio-diagnosis:commit:{commit_sha} -->"
                            ),
                            "user": {"id": 7},
                        }
                    ]
                ),
                json_response({"html_url": "https://github.test/comment/88"}),
            ]
        ),
        requests,
    )

    post_diagnosis(
        "octo/etio",
        ReportTarget("commit", commit_sha),
        diagnosis(),
        "token",
        opener=opener,
    )

    assert requests[1].full_url.endswith(
        f"/commits/{commit_sha}/comments?per_page=100&page=1"
    )
    assert requests[2].full_url.endswith("/comments/88")


def test_report_error_does_not_echo_sensitive_http_reason() -> None:
    def opener(*_: object, **__: object) -> FakeResponse:
        raise HTTPError(
            "https://api.github.com",
            403,
            "Bearer ghp_abcdefghijklmnopqrstuvwxyz1234567890",
            {},
            None,
        )

    with pytest.raises(GitHubReportError) as error:
        post_diagnosis(
            "octo/etio",
            ReportTarget("pull_request", 42),
            diagnosis(),
            "token",
            opener=opener,
        )

    assert "ghp_" not in str(error.value)
    assert "403" in str(error.value)


def test_build_diagnosis_comment_uses_a_safe_fence_and_neutralizes_mentions() -> None:
    existing_fence = chr(96) * 4
    comment = build_diagnosis_comment(
        Diagnosis(
            summary="Please ask @maintainer to rotate password=secret.",
            root_cause=f"A sequence has four fence characters: {existing_fence}.",
            suggested_patch=f"+ print({existing_fence})",
            confidence="high",
        ),
        ReportTarget("pull_request", 4),
    )

    assert "@maintainer" not in comment
    assert "@\u200bmaintainer" in comment
    assert "secret" not in comment
    assert chr(96) * 5 in comment


def test_build_diagnosis_comment_includes_only_a_valid_confirmed_commit() -> None:
    comment = build_diagnosis_comment(
        diagnosis(),
        ReportTarget("pull_request", 4),
        "a" * 40,
    )

    assert f"**Confirmed breaking commit:** `{'a' * 40}`" in comment
    with pytest.raises(ValueError, match="breaking_commit"):
        build_diagnosis_comment(diagnosis(), ReportTarget("pull_request", 4), "HEAD")
