import pytest

from etio.redact import REDACTED_VALUE, redact_sensitive_values


@pytest.mark.parametrize(
    ("secret", "text"),
    [
        (
            "ghp_abcdefghijklmnopqrstuvwxyz1234567890",
            "github=ghp_abcdefghijklmnopqrstuvwxyz1234567890",
        ),
        (
            "github_pat_abcdefghijklmnopqrstuvwxyz_1234567890",
            "github_pat_abcdefghijklmnopqrstuvwxyz_1234567890",
        ),
        (
            "gsk_abcdefghijklmnopqrstuvwxyz1234567890",
            "gsk_abcdefghijklmnopqrstuvwxyz1234567890",
        ),
        (
            "sk-abcdefghijklmnopqrstuvwxyz1234567890",
            "sk-abcdefghijklmnopqrstuvwxyz1234567890",
        ),
        (
            "xoxb-1234567890-abcdefghijklmnopqrstuvwxyz",
            "xoxb-1234567890-abcdefghijklmnopqrstuvwxyz",
        ),
        ("AKIAIOSFODNN7EXAMPLE", "AKIAIOSFODNN7EXAMPLE"),
        (
            "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.signature",
            "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.signature",
        ),
    ],
)
def test_redact_known_token_formats(secret: str, text: str) -> None:
    redacted = redact_sensitive_values(text)

    assert secret not in redacted
    assert REDACTED_VALUE in redacted


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "Connecting to https://admin:supersecret@database.example.com",
            "Connecting to https://admin:[REDACTED]@database.example.com",
        ),
        ("Authorization: Bearer lower-case-secret", "Authorization: [REDACTED]"),
        ("api_key=secret-value", "api_key=[REDACTED]"),
        ('"client_secret": "secret-value"', '"client_secret": "[REDACTED]"'),
        (
            "password='secret-value'; host=database",
            "password='[REDACTED]'; host=database",
        ),
        (
            "https://example.test/?token=secret-value",
            "https://example.test/?token=[REDACTED]",
        ),
    ],
)
def test_redact_named_and_embedded_credentials(text: str, expected: str) -> None:
    assert redact_sensitive_values(text) == expected


def test_redact_private_key_block() -> None:
    text = (
        "before\n"
        "-----BEGIN PRIVATE KEY-----\n"
        "super-secret-material\n"
        "-----END PRIVATE KEY-----\n"
        "after"
    )

    redacted = redact_sensitive_values(text)

    assert "super-secret-material" not in redacted
    assert redacted == f"before\n{REDACTED_VALUE}\nafter"


def test_redaction_is_idempotent_and_preserves_unrelated_text() -> None:
    text = "The patient tokenization test passed in 3 seconds."
    once = redact_sensitive_values(text)

    assert once == text
    assert redact_sensitive_values(once) == text
