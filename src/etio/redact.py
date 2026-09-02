"""Best-effort secret redaction before context leaves a GitHub runner."""

from __future__ import annotations

import re

REDACTED_VALUE = "[REDACTED]"

_PRIVATE_KEY = re.compile(
    r"-----BEGIN(?: [A-Z0-9]+)? PRIVATE KEY-----.*?"
    r"-----END(?: [A-Z0-9]+)? PRIVATE KEY-----",
    re.DOTALL,
)
_URL_CREDENTIALS = re.compile(
    r"(?P<prefix>\b[a-z][a-z0-9+.-]*://[^/\s:@]+:)" r"(?P<secret>[^@\s/]+)(?=@)",
    re.IGNORECASE,
)
_AUTHORIZATION = re.compile(
    r"(?P<prefix>\b(?:proxy-)?authorization\s*:\s*)" r"(?P<value>[^\r\n]+)",
    re.IGNORECASE,
)
_BEARER_TOKEN = re.compile(
    r"(?P<prefix>\bbearer\s+)(?P<value>[a-z0-9._~+/=-]{8,})",
    re.IGNORECASE,
)
_NAMED_SECRET = re.compile(
    r"(?P<key_quote>[\"']?)(?P<key>\b(?:api[-_ ]?key|apikey|"
    r"access[-_ ]?token|auth[-_ ]?token|token|password|passwd|pwd|secret|"
    r"client[-_ ]?secret|connection[-_ ]?string|"
    r"aws[-_ ]?secret[-_ ]?access[-_ ]?key|"
    r"aws[-_ ]?session[-_ ]?token|database[-_ ]?url)\b)"
    r"(?P=key_quote)"
    r"(?P<separator>\s*(?:=|:)\s*)"
    r"(?P<value>\"[^\r\n\"]*\"|'[^\r\n']*'|[^\s,;)&]+)",
    re.IGNORECASE,
)
_RAW_SECRET_PATTERNS = (
    re.compile(r"\bgh(?:p|o|u|s|r)_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bgsk_[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bxox(?:[abprs]|a)-[A-Za-z0-9-]{10,}\b"),
    re.compile(r"\bxapp-[A-Za-z0-9-]{10,}\b"),
    re.compile(r"\b(?:AKIA|ASIA|A3T[A-Z]|AGPA|AIDA|AROA)[A-Z0-9]{16}\b"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\b"),
)


def redact_sensitive_values(text: str) -> str:
    """Return text with likely credentials replaced by a stable placeholder."""
    if not text:
        return text
    redacted = _PRIVATE_KEY.sub(REDACTED_VALUE, text)
    redacted = _URL_CREDENTIALS.sub(_replace_url_secret, redacted)
    redacted = _AUTHORIZATION.sub(_replace_authorization, redacted)
    redacted = _BEARER_TOKEN.sub(_replace_bearer_token, redacted)
    redacted = _NAMED_SECRET.sub(_replace_named_secret, redacted)
    for pattern in _RAW_SECRET_PATTERNS:
        redacted = pattern.sub(REDACTED_VALUE, redacted)
    return redacted


def _replace_url_secret(match: re.Match[str]) -> str:
    return f"{match.group('prefix')}{REDACTED_VALUE}"


def _replace_authorization(match: re.Match[str]) -> str:
    return f"{match.group('prefix')}{REDACTED_VALUE}"


def _replace_bearer_token(match: re.Match[str]) -> str:
    return f"{match.group('prefix')}{REDACTED_VALUE}"


def _replace_named_secret(match: re.Match[str]) -> str:
    value = match.group("value")
    if value.startswith(('"', "'")):
        quote = value[0]
        replacement = f"{quote}{REDACTED_VALUE}{quote}"
    else:
        replacement = REDACTED_VALUE
    return (
        f"{match.group('key_quote')}{match.group('key')}"
        f"{match.group('key_quote')}{match.group('separator')}{replacement}"
    )
