"""Publish redacted Etio diagnoses as idempotent GitHub comments."""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Literal
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

from etio.models import Diagnosis
from etio.redact import redact_sensitive_values

GITHUB_API_URL = "https://api.github.com"
ETIO_MARKER = "<!-- etio-diagnosis -->"
MAX_COMMENT_CHARACTERS = 60_000
UrlOpener = Callable[..., Any]
ReportKind = Literal["pull_request", "commit"]


class GitHubReportError(RuntimeError):
    """Raised when Etio cannot safely publish a GitHub report."""


@dataclass(frozen=True, slots=True)
class ReportTarget:
    """A GitHub resource that can receive an Etio diagnosis comment."""

    kind: ReportKind
    identifier: int | str


@dataclass(frozen=True, slots=True)
class CommentRoutes:
    """GitHub API routes used to create, list, and update a comment target."""

    create_url: str
    update_url_prefix: str


@dataclass(frozen=True, slots=True)
class GitHubActor:
    """The immutable identity used to select Etio's own comments."""

    id: int
    login: str


def post_diagnosis(
    repository: str,
    target: ReportTarget,
    diagnosis: Diagnosis,
    token: str,
    api_url: str = GITHUB_API_URL,
    opener: UrlOpener = urlopen,
) -> str:
    """Create or update Etio's own comment on a PR or commit."""
    safe_api_url = _validated_api_url(api_url)
    _validate_repository(repository)
    if not token:
        raise ValueError("token must not be empty.")
    routes = _comment_routes(repository, target, safe_api_url)
    actor = _authenticated_actor(safe_api_url, token, opener)
    existing_comment = _find_owned_etio_comment(
        _list_comments(routes.create_url, token, opener),
        actor,
        _scope_marker(target),
    )
    body = build_diagnosis_comment(diagnosis, target)
    if existing_comment is None:
        response = _github_request(
            routes.create_url,
            token,
            opener,
            data=json.dumps({"body": body}).encode("utf-8"),
            method="POST",
        )
    else:
        comment_id = existing_comment.get("id")
        if not isinstance(comment_id, int):
            raise GitHubReportError("GitHub returned an Etio comment without an id.")
        response = _github_request(
            f"{routes.update_url_prefix}/{comment_id}",
            token,
            opener,
            data=json.dumps({"body": body}).encode("utf-8"),
            method="PATCH",
        )
    return _comment_url(response)


def post_diagnosis_comment(
    repository: str,
    issue_number: int,
    diagnosis: Diagnosis,
    token: str,
    api_url: str = GITHUB_API_URL,
    opener: UrlOpener = urlopen,
) -> str:
    """Backwards-compatible convenience wrapper for a pull-request comment."""
    return post_diagnosis(
        repository,
        ReportTarget("pull_request", issue_number),
        diagnosis,
        token,
        api_url,
        opener,
    )


def post_commit_diagnosis_comment(
    repository: str,
    commit_sha: str,
    diagnosis: Diagnosis,
    token: str,
    api_url: str = GITHUB_API_URL,
    opener: UrlOpener = urlopen,
) -> str:
    """Post an explicit commit comment, which needs contents: write permission."""
    return post_diagnosis(
        repository,
        ReportTarget("commit", commit_sha),
        diagnosis,
        token,
        api_url,
        opener,
    )


def build_diagnosis_comment(
    diagnosis: Diagnosis, target: ReportTarget | None = None
) -> str:
    """Render a diagnosis after redacting every externally visible field."""
    summary = _comment_text(diagnosis.summary, 3_000)
    root_cause = _comment_text(diagnosis.root_cause, 12_000)
    body = [
        "## Etio CI Diagnosis",
        ETIO_MARKER,
        _scope_marker(target) if target is not None else "",
        "",
        f"**Confidence:** {diagnosis.confidence}",
        "",
        "**Summary**",
        summary,
        "",
        "**Root cause**",
        root_cause,
    ]
    if diagnosis.suggested_patch:
        patch = _limited_text(
            redact_sensitive_values(diagnosis.suggested_patch), 40_000
        )
        fence = _code_fence(patch)
        body.extend(["", "**Suggested patch**", fence, patch, fence])
    else:
        body.extend(["", "Etio did not suggest an automated patch."])
    return _limited_text("\n".join(body), MAX_COMMENT_CHARACTERS)


def _comment_routes(
    repository: str, target: ReportTarget, api_url: str
) -> CommentRoutes:
    if target.kind == "pull_request":
        if not isinstance(target.identifier, int) or target.identifier < 1:
            raise ValueError("A pull-request report target needs a positive number.")
        return CommentRoutes(
            f"{api_url}/repos/{repository}/issues/{target.identifier}/comments",
            f"{api_url}/repos/{repository}/issues/comments",
        )
    if target.kind == "commit":
        if not isinstance(target.identifier, str) or not re.fullmatch(
            r"[A-Fa-f0-9]{7,64}", target.identifier
        ):
            raise ValueError("A commit report target needs a hexadecimal SHA.")
        return CommentRoutes(
            f"{api_url}/repos/{repository}/commits/{target.identifier}/comments",
            f"{api_url}/repos/{repository}/comments",
        )
    raise ValueError(f"Unsupported Etio report target kind: {target.kind}.")


def _authenticated_actor(api_url: str, token: str, opener: UrlOpener) -> GitHubActor:
    payload = _github_request(f"{api_url}/user", token, opener)
    if not isinstance(payload, Mapping):
        raise GitHubReportError(
            "GitHub returned an invalid authenticated-user response."
        )
    login = payload.get("login")
    actor_id = payload.get("id")
    if not isinstance(login, str) or not login or not isinstance(actor_id, int):
        raise GitHubReportError("GitHub did not identify the token's comment author.")
    return GitHubActor(actor_id, login)


def _list_comments(
    comment_url: str, token: str, opener: UrlOpener
) -> Iterable[Mapping[str, Any]]:
    page = 1
    while True:
        query = urlencode({"per_page": 100, "page": page})
        payload = _github_request(f"{comment_url}?{query}", token, opener)
        if not isinstance(payload, list):
            raise GitHubReportError("GitHub returned an invalid comments response.")
        yield from (comment for comment in payload if isinstance(comment, Mapping))
        if len(payload) < 100:
            return
        page += 1


def _find_owned_etio_comment(
    comments: Iterable[Mapping[str, Any]],
    actor: GitHubActor,
    scope_marker: str,
) -> Mapping[str, Any] | None:
    candidates: list[Mapping[str, Any]] = []
    for comment in comments:
        body = comment.get("body")
        user = comment.get("user")
        if (
            not isinstance(body, str)
            or ETIO_MARKER not in body
            or scope_marker not in body
        ):
            continue
        if not isinstance(user, Mapping) or user.get("id") != actor.id:
            continue
        if isinstance(comment.get("id"), int):
            candidates.append(comment)
    return max(candidates, key=lambda comment: int(comment["id"]), default=None)


def _github_request(
    url: str,
    token: str,
    opener: UrlOpener,
    data: bytes | None = None,
    method: str = "GET",
) -> Mapping[str, Any] | list[Any]:
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "Etio-GitHub-Action",
    }
    if data is not None:
        headers["Content-Type"] = "application/json"
    request = Request(url, data=data, headers=headers, method=method)
    try:
        with opener(request, timeout=30) as response:
            raw_response = response.read()
    except HTTPError as error:
        reason = redact_sensitive_values(str(error.reason))
        raise GitHubReportError(
            f"GitHub could not publish the Etio report ({error.code} {reason})."
        ) from error
    except (TimeoutError, URLError) as error:
        raise GitHubReportError(
            "GitHub could not be reached to publish the Etio report."
        ) from error
    try:
        payload = json.loads(raw_response.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise GitHubReportError(
            "GitHub returned invalid JSON while publishing Etio's report."
        ) from error
    if not isinstance(payload, (Mapping, list)):
        raise GitHubReportError("GitHub returned an unexpected report response.")
    return payload


def _comment_url(payload: Mapping[str, Any] | list[Any]) -> str:
    if not isinstance(payload, Mapping):
        raise GitHubReportError("GitHub returned an invalid comment response.")
    html_url = payload.get("html_url")
    if not isinstance(html_url, str) or not html_url:
        raise GitHubReportError("GitHub returned a comment response without a URL.")
    return html_url


def _code_fence(text: str) -> str:
    marker = chr(96)
    longest_run = max(
        (len(match.group(0)) for match in re.finditer(f"{re.escape(marker)}+", text)),
        default=0,
    )
    return marker * max(3, longest_run + 1)


def _scope_marker(target: ReportTarget | None) -> str:
    if target is None:
        return ""
    return f"<!-- etio-diagnosis:{target.kind}:{target.identifier} -->"


def _comment_text(text: str, maximum_length: int) -> str:
    return _limited_text(redact_sensitive_values(text), maximum_length).replace(
        "@", "@\u200b"
    )


def _limited_text(text: str, maximum_length: int) -> str:
    if len(text) <= maximum_length:
        return text
    suffix = "\n[Etio truncated this report.]"
    return f"{text[: maximum_length - len(suffix)].rstrip()}{suffix}"


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


def _validate_repository(repository: str) -> None:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise ValueError("repository must be an owner/name GitHub repository.")
