"""Locate a known-good workflow commit and produce bounded diff context."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

GITHUB_API_URL = "https://api.github.com"
GitRunner = Callable[[list[str], Path | None], subprocess.CompletedProcess[str]]
UrlOpener = Callable[[Request], Any]


class BisectError(RuntimeError):
    """Raised when Etio cannot establish a usable known-good commit."""


def find_last_passing_commit(
    repository: str,
    branch: str,
    head_sha: str,
    token: str,
    workflow_file: str | None = None,
    api_url: str = GITHUB_API_URL,
    repository_path: Path | None = None,
    opener: UrlOpener = urlopen,
    git_runner: GitRunner | None = None,
) -> str | None:
    """Return the newest successful workflow SHA that is an ancestor of HEAD."""
    if not repository:
        raise ValueError("repository must not be empty.")
    if not branch:
        raise ValueError("branch must not be empty.")
    if not head_sha:
        raise ValueError("head_sha must not be empty.")

    runner = git_runner or _run_git
    for workflow_run in _list_completed_workflow_runs(
        repository, branch, token, workflow_file, api_url, opener
    ):
        candidate_sha = workflow_run.get("head_sha")
        if (
            workflow_run.get("conclusion") == "success"
            and isinstance(candidate_sha, str)
            and candidate_sha != head_sha
            and _is_ancestor(candidate_sha, head_sha, repository_path, runner)
        ):
            return candidate_sha
    return None


def build_failure_diff(
    base_sha: str,
    head_sha: str,
    max_lines: int,
    repository_path: Path | None = None,
    git_runner: GitRunner | None = None,
) -> str:
    """Build bounded unified diff context from a known-good commit to HEAD."""
    if max_lines < 1:
        raise ValueError("max_lines must be at least 1.")
    if not base_sha:
        raise ValueError("base_sha must not be empty.")
    if not head_sha:
        raise ValueError("head_sha must not be empty.")

    runner = git_runner or _run_git
    completed = runner(
        ["diff", "--no-ext-diff", "--unified=3", f"{base_sha}..{head_sha}"],
        repository_path,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or "no error details were returned"
        raise BisectError(f"Git could not build the failure diff: {detail}.")
    return _truncate_diff(completed.stdout, max_lines)


def _list_completed_workflow_runs(
    repository: str,
    branch: str,
    token: str,
    workflow_file: str | None,
    api_url: str,
    opener: UrlOpener,
) -> list[Mapping[str, Any]]:
    path = _workflow_runs_path(repository, workflow_file)
    runs: list[Mapping[str, Any]] = []
    page = 1
    while True:
        query = urlencode(
            {"branch": branch, "status": "completed", "per_page": 100, "page": page}
        )
        payload = _github_json(f"{api_url}{path}?{query}", token, opener)
        page_runs = payload.get("workflow_runs")
        if not isinstance(page_runs, list):
            raise BisectError("GitHub returned a workflow-runs response without runs.")
        runs.extend(run for run in page_runs if isinstance(run, Mapping))
        if len(page_runs) < 100:
            return runs
        page += 1


def _workflow_runs_path(repository: str, workflow_file: str | None) -> str:
    if workflow_file:
        workflow_id = quote(Path(workflow_file).name, safe="")
        return f"/repos/{repository}/actions/workflows/{workflow_id}/runs"
    return f"/repos/{repository}/actions/runs"


def _github_json(url: str, token: str, opener: UrlOpener) -> Mapping[str, Any]:
    request = Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with opener(request) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        raise BisectError(
            f"GitHub could not list workflow runs ({error.code} {error.reason})."
        ) from error
    except URLError as error:
        raise BisectError("GitHub workflow runs could not be reached.") from error
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise BisectError("GitHub returned invalid workflow-run JSON.") from error
    if not isinstance(payload, Mapping):
        raise BisectError("GitHub returned an unexpected workflow-runs response.")
    return payload


def _is_ancestor(
    candidate_sha: str,
    head_sha: str,
    repository_path: Path | None,
    git_runner: GitRunner,
) -> bool:
    completed = git_runner(
        ["merge-base", "--is-ancestor", candidate_sha, head_sha], repository_path
    )
    if completed.returncode in {0, 1}:
        return completed.returncode == 0
    detail = completed.stderr.strip() or "no error details were returned"
    raise BisectError(
        f"Git could not compare successful commit {candidate_sha}: {detail}."
    )


def _run_git(
    arguments: list[str], repository_path: Path | None
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["git", *arguments],
            cwd=repository_path,
            capture_output=True,
            check=False,
            encoding="utf-8",
            errors="replace",
        )
    except OSError as error:
        raise BisectError("Git is required to compare the workflow history.") from error


def _truncate_diff(diff: str, max_lines: int) -> str:
    lines = diff.splitlines()
    if len(lines) <= max_lines:
        return diff
    marker = f"[Etio truncated this diff to {max_lines} lines.]"
    if max_lines == 1:
        return marker
    return "\n".join([*lines[: max_lines - 1], marker])
