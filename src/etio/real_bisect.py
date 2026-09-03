"""Confirm a breaking commit by dispatching a repository-owned test workflow."""

from __future__ import annotations

import json
import re
import secrets
import subprocess
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlparse
from urllib.request import Request, urlopen

from etio.redact import redact_sensitive_values

GITHUB_API_URL = "https://api.github.com"
MAX_WORKFLOW_DISPATCH_INPUTS = 25
GitRunner = Callable[[list[str], Path | None], subprocess.CompletedProcess[str]]
UrlOpener = Callable[..., Any]
Sleep = Callable[[float], None]
Monotonic = Callable[[], float]
NonceFactory = Callable[[], str]
CandidateOutcome = Literal["passed", "failed"]


class RealBisectError(RuntimeError):
    """Raised when Etio cannot safely run or interpret a real bisection."""


class RealBisectInconclusiveError(RealBisectError):
    """Raised when a dispatched test cannot establish a reliable boundary."""


class _GitHubResponseError(RealBisectError):
    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True, slots=True)
class RealBisectResult:
    """The exact first failing revision, or an explicit incomplete result."""

    breaking_commit: str | None
    attempted_commits: tuple[str, ...]
    complete: bool
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class _GitHubResponse:
    status_code: int
    payload: Mapping[str, Any] | list[Any] | None


@dataclass(slots=True)
class _WorkflowDispatcher:
    repository: str
    workflow_file: str
    token: str
    workflow_inputs: Mapping[str, str]
    run_id: int
    run_attempt: int
    api_url: str
    opener: UrlOpener
    timeout_seconds: float
    poll_seconds: float
    sleep: Sleep
    monotonic: Monotonic
    nonce_factory: NonceFactory

    def run(self, commit_sha: str) -> CandidateOutcome:
        ref_name = _temporary_ref_name(
            self.run_id, self.run_attempt, commit_sha, self.nonce_factory()
        )
        _create_temporary_ref(
            self.repository, ref_name, commit_sha, self.token, self.api_url, self.opener
        )
        try:
            dispatch_response = _dispatch_workflow(
                self.repository,
                self.workflow_file,
                ref_name,
                self.workflow_inputs,
                self.token,
                self.api_url,
                self.opener,
            )
            outcome = _wait_for_workflow_outcome(
                self.repository,
                self.workflow_file,
                ref_name,
                commit_sha,
                _workflow_run_id(dispatch_response),
                self.token,
                self.api_url,
                self.opener,
                self.timeout_seconds,
                self.poll_seconds,
                self.sleep,
                self.monotonic,
            )
        except RealBisectError:
            _delete_after_failed_dispatch(
                self.repository, ref_name, self.token, self.api_url, self.opener
            )
            raise
        _delete_temporary_ref(
            self.repository, ref_name, self.token, self.api_url, self.opener
        )
        return outcome


def run_real_bisection(
    repository: str,
    base_sha: str,
    head_sha: str,
    workflow_file: str,
    token: str,
    workflow_inputs: Mapping[str, str],
    run_id: int,
    run_attempt: int,
    max_steps: int,
    timeout_seconds: float,
    poll_seconds: float,
    api_url: str = GITHUB_API_URL,
    repository_path: Path | None = None,
    opener: UrlOpener = urlopen,
    git_runner: GitRunner | None = None,
    sleep: Sleep = time.sleep,
    monotonic: Monotonic = time.monotonic,
    nonce_factory: NonceFactory = lambda: secrets.token_hex(5),
) -> RealBisectResult:
    """Run a bounded, first-parent bisection using temporary GitHub refs."""
    _validate_real_bisect_inputs(
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
    )
    revisions = _candidate_revisions(
        base_sha, head_sha, repository_path, git_runner or _run_git
    )
    dispatcher = _WorkflowDispatcher(
        repository,
        workflow_file,
        token,
        dict(workflow_inputs),
        run_id,
        run_attempt,
        _validated_api_url(api_url),
        opener,
        timeout_seconds,
        poll_seconds,
        sleep,
        monotonic,
        nonce_factory,
    )
    return confirm_breaking_commit(revisions, dispatcher.run, max_steps)


def confirm_breaking_commit(
    revisions: Sequence[str],
    run_candidate: Callable[[str], CandidateOutcome],
    max_steps: int,
) -> RealBisectResult:
    """Confirm a passing base and failing head before binary-searching revisions."""
    if len(revisions) < 2:
        raise ValueError(
            "real bisection needs a base revision and a later head revision."
        )
    if max_steps < 1:
        raise ValueError("max_steps must be a positive integer.")

    attempted: list[str] = []

    def run_at(index: int) -> CandidateOutcome | None:
        if len(attempted) >= max_steps:
            return None
        commit_sha = revisions[index]
        outcome = run_candidate(commit_sha)
        if outcome not in {"passed", "failed"}:
            raise RealBisectInconclusiveError(
                "The dispatched workflow returned an unsupported test outcome."
            )
        attempted.append(commit_sha)
        return outcome

    base_outcome = run_at(0)
    if base_outcome is None:
        return _incomplete_result(attempted)
    if base_outcome != "passed":
        raise RealBisectInconclusiveError(
            "The configured bisection workflow did not pass at the known-good base."
        )

    head_outcome = run_at(len(revisions) - 1)
    if head_outcome is None:
        return _incomplete_result(attempted)
    if head_outcome != "failed":
        raise RealBisectInconclusiveError(
            "The configured bisection workflow did not reproduce the failure at HEAD."
        )

    passing_index = 0
    failing_index = len(revisions) - 1
    while failing_index - passing_index > 1:
        midpoint = (passing_index + failing_index) // 2
        midpoint_outcome = run_at(midpoint)
        if midpoint_outcome is None:
            return _incomplete_result(attempted)
        if midpoint_outcome == "passed":
            passing_index = midpoint
        else:
            failing_index = midpoint
    return RealBisectResult(revisions[failing_index], tuple(attempted), True, None)


def _candidate_revisions(
    base_sha: str,
    head_sha: str,
    repository_path: Path | None,
    git_runner: GitRunner,
) -> tuple[str, ...]:
    _validate_sha(base_sha, "base_sha")
    _validate_sha(head_sha, "head_sha")
    ancestry = git_runner(
        ["merge-base", "--is-ancestor", base_sha, head_sha], repository_path
    )
    if ancestry.returncode == 1:
        raise RealBisectError("The known-good commit is not an ancestor of HEAD.")
    if ancestry.returncode != 0:
        detail = ancestry.stderr.strip() or "no error details were returned"
        raise RealBisectError(f"Git could not verify the bisection ancestry: {detail}.")
    revisions = git_runner(
        [
            "rev-list",
            "--reverse",
            "--first-parent",
            "--ancestry-path",
            f"{base_sha}..{head_sha}",
        ],
        repository_path,
    )
    if revisions.returncode != 0:
        detail = revisions.stderr.strip() or "no error details were returned"
        raise RealBisectError(f"Git could not list bisection candidates: {detail}.")
    candidates = tuple(
        line.strip() for line in revisions.stdout.splitlines() if line.strip()
    )
    if not candidates:
        raise RealBisectError(
            "There are no commits to bisect after the known-good base."
        )
    if candidates[-1] != head_sha:
        raise RealBisectError(
            "Git did not produce HEAD as the final first-parent bisection candidate."
        )
    for candidate in candidates:
        _validate_sha(candidate, "candidate commit")
    return (base_sha, *candidates)


def _incomplete_result(attempted: list[str]) -> RealBisectResult:
    return RealBisectResult(
        None,
        tuple(attempted),
        False,
        "The configured maximum number of workflow runs was reached.",
    )


def _create_temporary_ref(
    repository: str,
    ref_name: str,
    commit_sha: str,
    token: str,
    api_url: str,
    opener: UrlOpener,
) -> None:
    response = _github_request(
        f"{api_url}/repos/{repository}/git/refs",
        token,
        opener,
        method="POST",
        payload={"ref": f"refs/heads/{ref_name}", "sha": commit_sha},
    )
    if response.status_code not in {200, 201}:
        raise RealBisectError("GitHub did not create the temporary bisection ref.")


def _dispatch_workflow(
    repository: str,
    workflow_file: str,
    ref_name: str,
    workflow_inputs: Mapping[str, str],
    token: str,
    api_url: str,
    opener: UrlOpener,
) -> _GitHubResponse:
    workflow_id = quote(Path(workflow_file).name, safe="")
    response = _github_request(
        f"{api_url}/repos/{repository}/actions/workflows/{workflow_id}/dispatches",
        token,
        opener,
        method="POST",
        payload={"ref": ref_name, "inputs": dict(workflow_inputs)},
    )
    if response.status_code not in {200, 204}:
        raise RealBisectError("GitHub did not accept the workflow-dispatch request.")
    return response


def _wait_for_workflow_outcome(
    repository: str,
    workflow_file: str,
    ref_name: str,
    commit_sha: str,
    workflow_run_id: int | None,
    token: str,
    api_url: str,
    opener: UrlOpener,
    timeout_seconds: float,
    poll_seconds: float,
    sleep: Sleep,
    monotonic: Monotonic,
) -> CandidateOutcome:
    deadline = monotonic() + timeout_seconds
    while True:
        workflow_run = _dispatched_workflow_run(
            repository,
            workflow_file,
            ref_name,
            commit_sha,
            workflow_run_id,
            token,
            api_url,
            opener,
        )
        if workflow_run is not None and workflow_run.get("status") == "completed":
            conclusion = workflow_run.get("conclusion")
            if conclusion == "success":
                return "passed"
            if conclusion == "failure":
                return "failed"
            raise RealBisectInconclusiveError(
                "A dispatched bisection workflow completed without a test failure "
                f"or success ({redact_sensitive_values(str(conclusion))})."
            )
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise RealBisectInconclusiveError(
                "A dispatched bisection workflow did not complete before the timeout."
            )
        sleep(min(poll_seconds, remaining))


def _dispatched_workflow_run(
    repository: str,
    workflow_file: str,
    ref_name: str,
    commit_sha: str,
    workflow_run_id: int | None,
    token: str,
    api_url: str,
    opener: UrlOpener,
) -> Mapping[str, Any] | None:
    if workflow_run_id is not None:
        return _workflow_run_by_id(repository, workflow_run_id, token, api_url, opener)
    return _workflow_run_for_temporary_ref(
        repository,
        workflow_file,
        ref_name,
        commit_sha,
        token,
        api_url,
        opener,
    )


def _workflow_run_by_id(
    repository: str,
    workflow_run_id: int,
    token: str,
    api_url: str,
    opener: UrlOpener,
) -> Mapping[str, Any] | None:
    try:
        response = _github_request(
            f"{api_url}/repos/{repository}/actions/runs/{workflow_run_id}",
            token,
            opener,
        )
    except _GitHubResponseError as error:
        if error.status_code == 404:
            return None
        raise
    return _mapping_payload(response.payload, "workflow-run")


def _workflow_run_for_temporary_ref(
    repository: str,
    workflow_file: str,
    ref_name: str,
    commit_sha: str,
    token: str,
    api_url: str,
    opener: UrlOpener,
) -> Mapping[str, Any] | None:
    workflow_id = quote(Path(workflow_file).name, safe="")
    query = urlencode(
        {
            "branch": ref_name,
            "event": "workflow_dispatch",
            "per_page": 100,
        }
    )
    response = _github_request(
        f"{api_url}/repos/{repository}/actions/workflows/{workflow_id}/runs?{query}",
        token,
        opener,
    )
    payload = _mapping_payload(response.payload, "workflow-runs")
    workflow_runs = payload.get("workflow_runs")
    if not isinstance(workflow_runs, list):
        raise RealBisectError("GitHub returned a workflow-runs response without runs.")
    matches = [
        workflow_run
        for workflow_run in workflow_runs
        if isinstance(workflow_run, Mapping)
        and workflow_run.get("head_branch") == ref_name
        and workflow_run.get("head_sha") == commit_sha
        and workflow_run.get("event") == "workflow_dispatch"
        and isinstance(workflow_run.get("id"), int)
    ]
    return max(matches, key=lambda workflow_run: int(workflow_run["id"]), default=None)


def _workflow_run_id(response: _GitHubResponse) -> int | None:
    if not isinstance(response.payload, Mapping):
        return None
    run_id = response.payload.get("workflow_run_id")
    if run_id is None:
        return None
    if not isinstance(run_id, int) or run_id < 1:
        raise RealBisectError(
            "GitHub returned an invalid workflow run id for the dispatch."
        )
    return run_id


def _delete_temporary_ref(
    repository: str,
    ref_name: str,
    token: str,
    api_url: str,
    opener: UrlOpener,
) -> None:
    try:
        _github_request(
            f"{api_url}/repos/{repository}/git/refs/heads/{quote(ref_name, safe='/')}",
            token,
            opener,
            method="DELETE",
        )
    except _GitHubResponseError as error:
        if error.status_code == 404:
            return
        raise RealBisectError(
            "Etio could not delete its temporary bisection ref."
        ) from error


def _delete_after_failed_dispatch(
    repository: str,
    ref_name: str,
    token: str,
    api_url: str,
    opener: UrlOpener,
) -> None:
    try:
        _delete_temporary_ref(repository, ref_name, token, api_url, opener)
    except RealBisectError as cleanup_error:
        raise RealBisectError(
            "Etio could not finish real bisection and could not delete its "
            "temporary ref."
        ) from cleanup_error


def _github_request(
    url: str,
    token: str,
    opener: UrlOpener,
    method: str = "GET",
    payload: Mapping[str, Any] | None = None,
) -> _GitHubResponse:
    request_body = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "Etio-GitHub-Action",
    }
    if request_body is not None:
        headers["Content-Type"] = "application/json"
    request = Request(url, data=request_body, headers=headers, method=method)
    try:
        with opener(request, timeout=30) as response:
            raw_response = response.read()
            status_code = _response_status_code(response)
    except HTTPError as error:
        raise _GitHubResponseError(
            error.code,
            f"GitHub rejected the real-bisection request ({error.code}).",
        ) from error
    except (TimeoutError, URLError) as error:
        raise RealBisectError(
            "GitHub could not be reached during real bisection."
        ) from error
    if not raw_response:
        return _GitHubResponse(status_code, None)
    try:
        decoded_response = json.loads(raw_response.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RealBisectError(
            "GitHub returned invalid JSON during real bisection."
        ) from error
    if not isinstance(decoded_response, (Mapping, list)):
        raise RealBisectError("GitHub returned an unexpected real-bisection response.")
    return _GitHubResponse(status_code, decoded_response)


def _response_status_code(response: Any) -> int:
    status_code = getattr(response, "status", None)
    if isinstance(status_code, int):
        return status_code
    getcode = getattr(response, "getcode", None)
    if callable(getcode):
        candidate = getcode()
        if isinstance(candidate, int):
            return candidate
    return 200


def _mapping_payload(
    payload: Mapping[str, Any] | list[Any] | None, response_name: str
) -> Mapping[str, Any]:
    if not isinstance(payload, Mapping):
        raise RealBisectError(
            f"GitHub returned an invalid {response_name} response during "
            "real bisection."
        )
    return payload


def _temporary_ref_name(
    run_id: int, run_attempt: int, commit_sha: str, nonce: str
) -> str:
    safe_nonce = re.sub(r"[^A-Za-z0-9]", "", nonce)[:20]
    if not safe_nonce:
        raise RealBisectError("Etio could not create a safe temporary ref name.")
    return f"etio/bisect/{run_id}-{run_attempt}-{commit_sha[:12]}-{safe_nonce}"


def _validate_real_bisect_inputs(
    repository: str,
    base_sha: str,
    head_sha: str,
    workflow_file: str,
    token: str,
    workflow_inputs: Mapping[str, str],
    run_id: int,
    run_attempt: int,
    max_steps: int,
    timeout_seconds: float,
    poll_seconds: float,
) -> None:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise ValueError("repository must be an owner/name GitHub repository.")
    _validate_sha(base_sha, "base_sha")
    _validate_sha(head_sha, "head_sha")
    if not isinstance(workflow_file, str) or not Path(workflow_file).name:
        raise ValueError("workflow_file must name a workflow file.")
    if not token:
        raise ValueError("token must not be empty.")
    if not isinstance(run_id, int) or run_id < 1:
        raise ValueError("run_id must be a positive integer.")
    if not isinstance(run_attempt, int) or run_attempt < 1:
        raise ValueError("run_attempt must be a positive integer.")
    if not isinstance(max_steps, int) or max_steps < 1:
        raise ValueError("max_steps must be a positive integer.")
    if (
        isinstance(timeout_seconds, bool)
        or isinstance(poll_seconds, bool)
        or not isinstance(timeout_seconds, (int, float))
        or not isinstance(poll_seconds, (int, float))
        or timeout_seconds <= 0
        or poll_seconds <= 0
    ):
        raise ValueError("real-bisection timeouts must be positive.")
    _validate_workflow_inputs(workflow_inputs)


def _validate_workflow_inputs(workflow_inputs: Mapping[str, str]) -> None:
    if not isinstance(workflow_inputs, Mapping):
        raise ValueError("workflow_inputs must be a mapping.")
    if len(workflow_inputs) > MAX_WORKFLOW_DISPATCH_INPUTS:
        raise ValueError(
            "workflow_inputs cannot contain more than "
            f"{MAX_WORKFLOW_DISPATCH_INPUTS} values."
        )
    for key, value in workflow_inputs.items():
        if not isinstance(key, str) or not re.fullmatch(
            r"[A-Za-z_][A-Za-z0-9_-]{0,63}", key
        ):
            raise ValueError("workflow_inputs contains an invalid input name.")
        if not isinstance(value, str):
            raise ValueError("workflow_inputs values must be strings.")


def _validate_sha(value: str, name: str) -> None:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Fa-f0-9]{7,64}", value):
        raise ValueError(f"{name} must be a hexadecimal commit SHA.")


def _validated_api_url(api_url: str) -> str:
    parsed = urlparse(api_url)
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("api_url must be an HTTPS GitHub API base URL.")
    return api_url.rstrip("/")


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
        raise RealBisectError("Git is required for real bisection.") from error
