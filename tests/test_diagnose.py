import json
from typing import Any
from urllib.error import HTTPError

import pytest

from etio.diagnose import (
    DiagnosisError,
    GroqAPIError,
    diagnose_failure,
    parse_diagnosis,
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


def completion(content: dict[str, Any]) -> bytes:
    return json.dumps(
        {"choices": [{"message": {"content": json.dumps(content)}}]}
    ).encode()


def diagnosis_payload(
    summary: str = "The test expected an initialized value.",
    root_cause: str = "The diff removed the initialization before the assertion.",
    suggested_patch: str | None = None,
    confidence: str = "medium",
) -> dict[str, Any]:
    return {
        "summary": summary,
        "root_cause": root_cause,
        "suggested_patch": suggested_patch,
        "confidence": confidence,
    }


def test_diagnose_failure_redacts_evidence_and_uses_structured_output() -> None:
    requests: list[Any] = []

    def opener(request: Any, timeout: float) -> FakeResponse:
        requests.append((request, timeout))
        return FakeResponse(completion(diagnosis_payload()))

    diagnosis = diagnose_failure(
        "Failure ghp_abcdefghijklmnopqrstuvwxyz1234567890",
        "API_KEY=super-secret-value",
        "gsk_this_is_never_sent_as_prompt",
        opener=opener,
    )

    request, timeout = requests[0]
    payload = json.loads(request.data.decode("utf-8"))
    prompt = payload["messages"][1]["content"]
    assert diagnosis.confidence == "medium"
    assert timeout == 60.0
    assert "ghp_" not in prompt
    assert "super-secret-value" not in prompt
    assert "gsk_this_is_never_sent_as_prompt" not in prompt
    assert "[REDACTED]" in prompt
    assert payload["store"] is False
    assert payload["response_format"]["type"] == "json_schema"


def test_diagnose_failure_never_returns_a_low_confidence_patch() -> None:
    diagnosis = diagnose_failure(
        "AssertionError",
        "diff",
        "key",
        opener=lambda *_args, **_kwargs: FakeResponse(
            completion(
                diagnosis_payload(
                    suggested_patch="diff --git a/app.py b/app.py",
                    confidence="low",
                )
            )
        ),
    )

    assert diagnosis.suggested_patch is None


def test_diagnose_failure_reports_a_sanitized_http_error() -> None:
    def opener(*_: object, **__: object) -> FakeResponse:
        raise HTTPError(
            "https://api.groq.com",
            429,
            "Bearer gsk_this_is_an_error_secret_123456",
            {},
            None,
        )

    with pytest.raises(GroqAPIError) as error:
        diagnose_failure("error", "diff", "key", opener=opener)

    assert "gsk_" not in str(error.value)
    assert "429" in str(error.value)


def test_parse_diagnosis_rejects_wrong_types_and_unexpected_fields() -> None:
    wrong_type = diagnosis_payload(summary=123)  # type: ignore[arg-type]
    unexpected = {**diagnosis_payload(), "extra": "field"}

    with pytest.raises(DiagnosisError, match="invalid summary"):
        parse_diagnosis(json.dumps(wrong_type))
    with pytest.raises(DiagnosisError, match="unexpected schema"):
        parse_diagnosis(json.dumps(unexpected))


def test_parse_diagnosis_does_not_echo_untrusted_response_text() -> None:
    secret_response = "invalid ghp_abcdefghijklmnopqrstuvwxyz1234567890"

    with pytest.raises(DiagnosisError) as error:
        parse_diagnosis(secret_response)

    assert "ghp_" not in str(error.value)


def test_parse_diagnosis_redacts_model_output_before_returning_it() -> None:
    diagnosis = parse_diagnosis(
        json.dumps(
            diagnosis_payload(
                summary="Set token=super-secret before tests.",
                confidence="high",
                suggested_patch="+ password=another-secret",
            )
        )
    )

    assert "super-secret" not in diagnosis.summary
    assert diagnosis.suggested_patch is not None
    assert "another-secret" not in diagnosis.suggested_patch
