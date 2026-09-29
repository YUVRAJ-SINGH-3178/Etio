"""Create a human-reviewed draft pull request from a validated diagnosis patch."""

from __future__ import annotations

import base64
import json
import os
import re
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

from etio.models import Diagnosis
from etio.redact import redact_sensitive_values

GITHUB_API_URL = "https://api.github.com"
MAX_PATCH_CHARACTERS = 20_000
ETIO_AUTO_PR_MARKER = "<!-- etio-auto-pr -->"
GitRunner = Callable[..., subprocess.CompletedProcess[str]]
UrlOpener = Callable[..., Any]


class AutoPRError(RuntimeError):
    """Raised when Etio cannot safely prepare or open its draft pull request."""


@dataclass(frozen=True, slots=True)
class AutoPRResult:
    """The draft pull request URL and its generated source branch."""

    url: str
    branch: str
    created: bool


def open_auto_pr(
    repository: str,
    base_branch: str,
    head_sha: str,
    run_id: int,
    run_attempt: int,
    diagnosis: Diagnosis,
    token: str,
    repository_path: Path | None = None,
    server_url: str = "https://github.com",
    api_url: str = GITHUB_API_URL,
    opener: UrlOpener = urlopen,
    git_runner: GitRunner | None = None,
) -> AutoPRResult:
    """Validate a model patch, push a dedicated branch, and open a draft PR."""
    _validate_inputs(
        repository, base_branch, head_sha, run_id, run_attempt, diagnosis, token
    )
    api_base = _validated_https_url(api_url, "api_url")
    server_base = _validated_https_url(server_url, "server_url")
    branch = _branch_name(run_id, run_attempt, head_sha)
    existing = _find_open_pull_request(
        repository, branch, base_branch, token, api_base, opener
    )
    if existing is not None:
        return AutoPRResult(existing, branch, False)

    runner = git_runner or _run_git
    root = _repository_root(repository_path, runner)
    source_commit = runner(["cat-file", "-e", f"{head_sha}^{{commit}}"], root)
    if source_commit.returncode != 0:
        raise AutoPRError(
            "Etio could not find the commit to use as the draft-PR branch base."
        )
    validated_patch = _validated_patch(
        redact_sensitive_values(diagnosis.suggested_patch or ""), root
    )
    _apply_patch_on_branch(
        branch,
        base_branch,
        head_sha,
        validated_patch,
        root,
        repository,
        server_base,
        runner,
    )
    _push_branch(server_base, branch, token, root, runner)
    pull_request_url = _create_draft_pull_request(
        repository,
        branch,
        base_branch,
        diagnosis,
        token,
        api_base,
        opener,
    )
    return AutoPRResult(pull_request_url, branch, True)


def _apply_patch_on_branch(
    branch: str,
    base_branch: str,
    source_sha: str,
    patch: _ValidatedPatch,
    root: Path,
    repository: str,
    server_url: str,
    runner: GitRunner,
) -> None:
    _run_git_checked(
        runner,
        ["check-ref-format", "--branch", base_branch],
        root,
        "Etio cannot use the configured base branch name.",
    )
    _require_clean_worktree(root, runner)
    _check_remote(repository, server_url, root, runner)
    _check_branch_available("origin", branch, root, runner)
    _run_git_checked(
        runner,
        ["switch", "--detach", source_sha],
        root,
        "Etio could not check out the failed commit for its draft PR.",
    )
    _run_git_checked(
        runner,
        ["apply", "--check", "-"],
        root,
        "Git rejected the suggested patch.",
        input_data=patch.text,
    )
    _run_git_checked(
        runner,
        ["switch", "-c", branch],
        root,
        "Etio could not create its draft-PR branch.",
    )
    _run_git_checked(
        runner,
        ["apply", "-"],
        root,
        "Etio could not apply the validated suggested patch.",
        input_data=patch.text,
    )
    _run_git_checked(
        runner, ["diff", "--check"], root, "The suggested patch has whitespace errors."
    )
    changed_paths = _changed_paths(root, runner)
    if changed_paths != patch.paths:
        raise AutoPRError("Git changed files outside the validated suggested patch.")
    _run_git_checked(
        runner,
        ["add", "--", *changed_paths],
        root,
        "Etio could not stage the suggested patch.",
    )
    _run_git_checked(
        runner,
        [
            "-c",
            "user.name=Etio",
            "-c",
            "user.email=etio[bot]@users.noreply.github.com",
            "commit",
            "-m",
            "fix: apply Etio's suggested CI patch",
        ],
        root,
        "Etio could not commit the suggested patch.",
    )


@dataclass(frozen=True, slots=True)
class _ValidatedPatch:
    text: str
    paths: tuple[str, ...]


def _validated_patch(patch: str, repository_root: Path) -> _ValidatedPatch:
    if not patch or len(patch) > MAX_PATCH_CHARACTERS:
        raise AutoPRError("The suggested patch is empty or too large to apply.")
    lines = patch.splitlines()
    if any(
        line.startswith(
            (
                "GIT binary patch",
                "Binary files ",
                "new file mode ",
                "deleted file mode ",
                "old mode ",
                "new mode ",
                "rename from ",
                "rename to ",
                "copy from ",
                "copy to ",
            )
        )
        for line in lines
    ):
        raise AutoPRError(
            "Etio only applies text changes to existing files; this patch "
            "contains a file operation that needs manual review."
        )
    path_pairs = _patch_path_pairs(lines)
    if not path_pairs:
        raise AutoPRError("The suggested patch does not contain a unified diff.")

    paths: list[str] = []
    for old_path, new_path in path_pairs:
        if old_path != new_path:
            raise AutoPRError(
                "Etio does not apply file additions, deletions, or renames."
            )
        path = _validated_repository_path(old_path, repository_root)
        relative_path = path.relative_to(repository_root)
        current_path = repository_root
        contains_symlink = False
        for part in relative_path.parts:
            current_path = current_path / part
            contains_symlink = contains_symlink or current_path.is_symlink()
        if contains_symlink or not path.is_file():
            raise AutoPRError(
                "Etio only applies patches to existing regular repository files."
            )
        paths.append(old_path)
    if len(paths) != len(set(paths)):
        raise AutoPRError("The suggested patch contains duplicate file sections.")
    return _ValidatedPatch(patch, tuple(sorted(paths)))


def _patch_path_pairs(lines: list[str]) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    current_old_path: str | None = None
    for line in lines:
        if line.startswith("--- "):
            if current_old_path is not None:
                raise AutoPRError("The suggested patch has malformed file headers.")
            current_old_path = _diff_header_path(line[4:], "a/")
        elif line.startswith("+++ "):
            if current_old_path is None:
                raise AutoPRError("The suggested patch has malformed file headers.")
            new_path = _diff_header_path(line[4:], "b/")
            pairs.append((current_old_path, new_path))
            current_old_path = None
    if current_old_path is not None:
        raise AutoPRError("The suggested patch has malformed file headers.")
    return pairs


def _diff_header_path(header: str, prefix: str) -> str:
    path = header.split("\t", maxsplit=1)[0]
    if path == "/dev/null" or not path.startswith(prefix):
        raise AutoPRError("The suggested patch contains an unsupported file path.")
    relative_path = path[len(prefix) :]
    if (
        not relative_path
        or "\\" in relative_path
        or relative_path.startswith("/")
        or any(part in {"", ".", ".."} for part in relative_path.split("/"))
    ):
        raise AutoPRError("The suggested patch contains an unsafe file path.")
    return relative_path


def _validated_repository_path(relative_path: str, root: Path) -> Path:
    candidate = root.joinpath(*relative_path.split("/"))
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as error:
        raise AutoPRError(
            "The suggested patch refers to a file that is not present."
        ) from error
    if not resolved.is_relative_to(root):
        raise AutoPRError("The suggested patch refers outside the repository.")
    if relative_path.startswith(".git/") or relative_path == ".git":
        raise AutoPRError("Etio cannot patch Git's internal files.")
    if relative_path == "action.yml" or relative_path.startswith(".github/workflows/"):
        raise AutoPRError("Etio cannot change action or workflow definitions.")
    return candidate


def _repository_root(path: Path | None, runner: GitRunner) -> Path:
    completed = runner(["rev-parse", "--show-toplevel"], path)
    if completed.returncode != 0:
        raise AutoPRError("Etio could not locate the checked-out Git repository.")
    root = Path(completed.stdout.strip()).resolve()
    if not root.exists() or not root.is_dir():
        raise AutoPRError("Etio could not access the checked-out Git repository.")
    return root


def _require_clean_worktree(root: Path, runner: GitRunner) -> None:
    completed = runner(["status", "--porcelain", "--untracked-files=no"], root)
    if completed.returncode != 0:
        raise AutoPRError("Etio could not check the current Git worktree.")
    if completed.stdout.strip():
        raise AutoPRError("Etio requires a clean tracked worktree before auto-PR.")


def _changed_paths(root: Path, runner: GitRunner) -> tuple[str, ...]:
    completed = runner(["diff", "--name-only"], root)
    if completed.returncode != 0:
        raise AutoPRError("Etio could not verify the files changed by the patch.")
    return tuple(sorted(line for line in completed.stdout.splitlines() if line))


def _check_branch_available(
    remote: str, branch: str, root: Path, runner: GitRunner
) -> None:
    completed = runner(
        ["ls-remote", "--exit-code", "--heads", remote, f"refs/heads/{branch}"],
        root,
    )
    if completed.returncode == 0:
        raise AutoPRError(
            "The Etio draft-PR branch already exists without an open pull request."
        )
    if completed.returncode != 2:
        raise AutoPRError("Etio could not check whether its draft-PR branch exists.")


def _check_remote(
    repository: str, server_url: str, root: Path, runner: GitRunner
) -> None:
    completed = runner(["remote", "get-url", "origin"], root)
    if completed.returncode != 0:
        raise AutoPRError("Etio could not read the repository's origin URL.")
    remote = urlparse(completed.stdout.strip())
    server = urlparse(server_url)
    remote_path = remote.path.rstrip("/").removesuffix(".git")
    expected_path = f"/{repository}"
    if (
        remote.scheme != "https"
        or remote.hostname != server.hostname
        or remote.username
        or remote.password
        or remote_path.casefold() != expected_path.casefold()
    ):
        raise AutoPRError(
            "Etio's origin URL does not match the configured GitHub repository."
        )


def _push_branch(
    server_url: str,
    branch: str,
    token: str,
    root: Path,
    runner: GitRunner,
) -> None:
    server = urlparse(server_url)
    credential = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    environment = {
        "GIT_CONFIG_COUNT": "2",
        "GIT_CONFIG_KEY_0": f"http.https://{server.netloc}/.extraheader",
        "GIT_CONFIG_VALUE_0": "",
        "GIT_CONFIG_KEY_1": f"http.https://{server.netloc}/.extraheader",
        "GIT_CONFIG_VALUE_1": f"AUTHORIZATION: basic {credential}",
    }
    _run_git_checked(
        runner,
        [
            "push",
            "--set-upstream",
            "origin",
            f"refs/heads/{branch}:refs/heads/{branch}",
        ],
        root,
        "Etio could not push the draft-PR branch.",
        environment=environment,
    )


def _find_open_pull_request(
    repository: str,
    branch: str,
    base_branch: str,
    token: str,
    api_url: str,
    opener: UrlOpener,
) -> str | None:
    owner = repository.split("/", maxsplit=1)[0]
    query = urlencode(
        {
            "state": "open",
            "head": f"{owner}:{branch}",
            "base": base_branch,
            "per_page": 100,
        }
    )
    payload = _github_json(f"{api_url}/repos/{repository}/pulls?{query}", token, opener)
    if not isinstance(payload, list):
        raise AutoPRError("GitHub returned an invalid pull-request list.")
    marker = _auto_pr_scope_marker(branch)
    for pull_request in payload:
        if not isinstance(pull_request, Mapping):
            continue
        url = pull_request.get("html_url")
        body = pull_request.get("body")
        head = pull_request.get("head")
        base = pull_request.get("base")
        head_repository = head.get("repo") if isinstance(head, Mapping) else None
        if (
            isinstance(url, str)
            and url
            and isinstance(body, str)
            and marker in body
            and isinstance(head, Mapping)
            and head.get("ref") == branch
            and isinstance(head_repository, Mapping)
            and head_repository.get("full_name") == repository
            and isinstance(base, Mapping)
            and base.get("ref") == base_branch
        ):
            return url
    return None


def _create_draft_pull_request(
    repository: str,
    branch: str,
    base_branch: str,
    diagnosis: Diagnosis,
    token: str,
    api_url: str,
    opener: UrlOpener,
) -> str:
    title = _safe_pr_title(diagnosis.summary)
    body = (
        f"{ETIO_AUTO_PR_MARKER}\n{_auto_pr_scope_marker(branch)}\n\n"
        "Etio prepared this patch from a high-confidence CI diagnosis. "
        "Review the changes and run the repository's checks before merging.\n\n"
        f"**Root cause**\n\n{_safe_markdown(diagnosis.root_cause)}\n\n"
        "This draft was generated automatically and has not been merged."
    )
    payload = _github_json(
        f"{api_url}/repos/{repository}/pulls",
        token,
        opener,
        method="POST",
        body={
            "title": title,
            "head": branch,
            "base": base_branch,
            "draft": True,
            "maintainer_can_modify": True,
            "body": body,
        },
    )
    if not isinstance(payload, Mapping):
        raise AutoPRError("GitHub returned an invalid draft pull-request response.")
    html_url = payload.get("html_url")
    if not isinstance(html_url, str) or not html_url:
        raise AutoPRError("GitHub returned a draft pull request without a URL.")
    return html_url


def _github_json(
    url: str,
    token: str,
    opener: UrlOpener,
    method: str = "GET",
    body: Mapping[str, Any] | None = None,
) -> Mapping[str, Any] | list[Any]:
    encoded_body = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "Etio-GitHub-Action",
    }
    if encoded_body is not None:
        headers["Content-Type"] = "application/json"
    request = Request(url, data=encoded_body, headers=headers, method=method)
    try:
        with opener(request, timeout=30) as response:
            raw = response.read()
    except HTTPError as error:
        raise AutoPRError(
            f"GitHub rejected the Etio draft pull-request request ({error.code})."
        ) from error
    except (TimeoutError, URLError) as error:
        raise AutoPRError(
            "GitHub could not be reached to create the draft PR."
        ) from error
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise AutoPRError(
            "GitHub returned invalid JSON for the draft pull request."
        ) from error
    if not isinstance(payload, (Mapping, list)):
        raise AutoPRError("GitHub returned an unexpected draft pull-request response.")
    return payload


def _run_git_checked(
    runner: GitRunner,
    arguments: list[str],
    root: Path,
    failure_message: str,
    input_data: str | None = None,
    environment: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    completed = runner(
        arguments,
        root,
        input_data=input_data,
        environment=environment,
    )
    if completed.returncode != 0:
        raise AutoPRError(failure_message)
    return completed


def _run_git(
    arguments: list[str],
    repository_path: Path | None,
    input_data: str | None = None,
    environment: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    process_environment = os.environ.copy()
    if environment:
        process_environment.update(environment)
    try:
        return subprocess.run(
            ["git", *arguments],
            cwd=repository_path,
            env=process_environment,
            input=input_data,
            capture_output=True,
            check=False,
            encoding="utf-8",
            errors="replace",
        )
    except OSError as error:
        raise AutoPRError(
            "Git is required to create Etio's draft pull request."
        ) from error


def _branch_name(run_id: int, run_attempt: int, head_sha: str) -> str:
    return f"etio/auto-fix/{run_id}-{run_attempt}-{head_sha[:12].lower()}"


def _auto_pr_scope_marker(branch: str) -> str:
    return f"<!-- etio-auto-pr:{branch} -->"


def _safe_pr_title(summary: str) -> str:
    normalized = _safe_markdown(redact_sensitive_values(summary))
    title = f"Etio: {normalized}".replace("\r", " ").replace("\n", " ").strip()
    return title[:250].rstrip()


def _safe_markdown(text: str) -> str:
    return redact_sensitive_values(text).replace("@", "@\u200b")


def _validate_inputs(
    repository: str,
    base_branch: str,
    head_sha: str,
    run_id: int,
    run_attempt: int,
    diagnosis: Diagnosis,
    token: str,
) -> None:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise ValueError("repository must be an owner/name GitHub repository.")
    if (
        not isinstance(base_branch, str)
        or not base_branch
        or base_branch.startswith("-")
        or any(character.isspace() for character in base_branch)
    ):
        raise ValueError("base_branch must be a valid Git branch name.")
    if not re.fullmatch(r"[A-Fa-f0-9]{7,64}", head_sha):
        raise ValueError("head_sha must be a hexadecimal commit SHA.")
    if (
        isinstance(run_id, bool)
        or isinstance(run_attempt, bool)
        or not isinstance(run_id, int)
        or not isinstance(run_attempt, int)
        or run_id < 1
        or run_attempt < 1
    ):
        raise ValueError("run_id and run_attempt must be positive integers.")
    if diagnosis.confidence != "high" or not diagnosis.suggested_patch:
        raise AutoPRError(
            "Etio only opens a draft PR for a high-confidence diagnosis with a patch."
        )
    if not token:
        raise ValueError("token must not be empty.")


def _validated_https_url(value: str, name: str) -> str:
    parsed = urlparse(value)
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(f"{name} must be an HTTPS URL without credentials.")
    return value.rstrip("/")
