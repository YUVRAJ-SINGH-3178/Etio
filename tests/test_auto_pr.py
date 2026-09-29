import json
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from etio.auto_pr import AutoPRError, AutoPRResult, _validated_patch, open_auto_pr
from etio.models import Diagnosis

PATCH = (
    "diff --git a/src/app.py b/src/app.py\n"
    "--- a/src/app.py\n"
    "+++ b/src/app.py\n"
    "@@ -1 +1 @@\n"
    "-value = 1\n"
    "+value = 2\n"
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


def json_response(payload: object) -> FakeResponse:
    return FakeResponse(json.dumps(payload).encode("utf-8"))


def response_opener(responses: Iterator[FakeResponse], requests: list[Any]) -> Any:
    def open_request(request: Any, **_: object) -> FakeResponse:
        requests.append(request)
        return next(responses)

    return open_request


def diagnosis() -> Diagnosis:
    return Diagnosis(
        summary="Initialize the setting before the test reads it.",
        root_cause="A recent change removed the value initialization.",
        suggested_patch=PATCH,
        confidence="high",
    )


def completed(
    arguments: list[str],
    returncode: int = 0,
    stdout: str = "",
    stderr: str = "",
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        ["git", *arguments], returncode, stdout=stdout, stderr=stderr
    )


def fake_git_runner(root: Path, calls: list[tuple[list[str], object, object]]) -> Any:
    def run(
        arguments: list[str],
        repository_path: Path | None,
        input_data: str | None = None,
        environment: object = None,
    ) -> subprocess.CompletedProcess[str]:
        calls.append((arguments, input_data, environment))
        if arguments == ["rev-parse", "--show-toplevel"]:
            return completed(arguments, stdout=str(root))
        if arguments == ["rev-parse", "HEAD"]:
            return completed(arguments, stdout="a" * 40)
        if arguments == ["status", "--porcelain", "--untracked-files=no"]:
            return completed(arguments)
        if arguments[0] == "ls-remote":
            return completed(arguments, returncode=2)
        if arguments == ["remote", "get-url", "origin"]:
            return completed(
                arguments,
                stdout="https://github.com/octo/etio.git\n",
            )
        if arguments == ["diff", "--name-only"]:
            return completed(arguments, stdout="src/app.py\n")
        return completed(arguments)

    return run


def test_validated_patch_accepts_only_existing_repository_files(tmp_path: Path) -> None:
    source_path = tmp_path / "src" / "app.py"
    source_path.parent.mkdir()
    source_path.write_text("value = 1\n", encoding="utf-8")

    patch = _validated_patch(PATCH, tmp_path)

    assert patch.paths == ("src/app.py",)
    assert patch.text == PATCH


@pytest.mark.parametrize(
    "patch",
    [
        PATCH.replace("src/app.py", "../outside.py"),
        PATCH.replace("src/app.py", "action.yml"),
        PATCH.replace("src/app.py", ".github/workflows/ci.yml"),
        PATCH.replace("src/app.py", "missing.py"),
        PATCH.replace(
            "--- a/src/app.py\n+++ b/src/app.py",
            "new file mode 100644\n--- /dev/null\n+++ b/src/app.py",
        ),
    ],
)
def test_validated_patch_rejects_unsafe_file_operations(
    tmp_path: Path, patch: str
) -> None:
    with pytest.raises(AutoPRError):
        _validated_patch(patch, tmp_path)


def test_open_auto_pr_pushes_a_draft_branch_and_requests_a_draft_pr(
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "src" / "app.py"
    source_path.parent.mkdir()
    source_path.write_text("value = 1\n", encoding="utf-8")
    git_calls: list[tuple[list[str], object, object]] = []
    requests: list[Any] = []
    opener = response_opener(
        iter(
            [
                json_response([]),
                json_response({"html_url": "https://github.test/pull/18"}),
            ]
        ),
        requests,
    )

    result = open_auto_pr(
        "octo/etio",
        "main",
        "a" * 40,
        42,
        1,
        diagnosis(),
        "secret-token",
        repository_path=tmp_path,
        opener=opener,
        git_runner=fake_git_runner(tmp_path, git_calls),
    )

    assert result == AutoPRResult(
        "https://github.test/pull/18",
        "etio/auto-fix/42-1-aaaaaaaaaaaa",
        True,
    )
    push_call = next(call for call in git_calls if call[0][0] == "push")
    assert push_call[2] == {
        "GIT_CONFIG_COUNT": "2",
        "GIT_CONFIG_KEY_0": "http.https://github.com/.extraheader",
        "GIT_CONFIG_VALUE_0": "",
        "GIT_CONFIG_KEY_1": "http.https://github.com/.extraheader",
        "GIT_CONFIG_VALUE_1": "AUTHORIZATION: basic "
        "eC1hY2Nlc3MtdG9rZW46c2VjcmV0LXRva2Vu",
    }
    assert all("secret-token" not in " ".join(call[0]) for call in git_calls)
    assert requests[0].method == "GET"
    assert requests[0].full_url.endswith(
        "/pulls?state=open&head=octo%3Aetio%2Fauto-fix%2F42-1-aaaaaaaaaaaa"
        "&base=main&per_page=100"
    )
    assert requests[1].method == "POST"
    pr_payload = json.loads(requests[1].data.decode("utf-8"))
    assert pr_payload["draft"] is True
    assert pr_payload["base"] == "main"
    assert pr_payload["head"] == "etio/auto-fix/42-1-aaaaaaaaaaaa"
    assert "<!-- etio-auto-pr -->" in pr_payload["body"]
    assert "merge" not in pr_payload


def test_open_auto_pr_returns_an_existing_draft_without_mutating_git(
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "src" / "app.py"
    source_path.parent.mkdir()
    source_path.write_text("value = 1\n", encoding="utf-8")
    git_calls: list[tuple[list[str], object, object]] = []
    opener = response_opener(
        iter(
            [
                json_response(
                    [
                        {
                            "html_url": "https://github.test/pull/18",
                            "body": (
                                "<!-- etio-auto-pr -->\n"
                                "<!-- etio-auto-pr:etio/auto-fix/"
                                "42-1-aaaaaaaaaaaa -->"
                            ),
                            "head": {
                                "ref": "etio/auto-fix/42-1-aaaaaaaaaaaa",
                                "repo": {"full_name": "octo/etio"},
                            },
                            "base": {"ref": "main"},
                        }
                    ]
                )
            ]
        ),
        [],
    )

    result = open_auto_pr(
        "octo/etio",
        "main",
        "a" * 40,
        42,
        1,
        diagnosis(),
        "token",
        repository_path=tmp_path,
        opener=opener,
        git_runner=fake_git_runner(tmp_path, git_calls),
    )

    assert result.created is False
    assert result.url == "https://github.test/pull/18"
    assert git_calls == []


def test_open_auto_pr_refuses_low_confidence_diagnoses(tmp_path: Path) -> None:
    low_confidence = Diagnosis("Maybe", "Uncertain", PATCH, confidence="medium")

    with pytest.raises(AutoPRError, match="high-confidence"):
        open_auto_pr(
            "octo/etio",
            "main",
            "a" * 40,
            42,
            1,
            low_confidence,
            "token",
            repository_path=tmp_path,
        )
