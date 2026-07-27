from pathlib import Path

import pytest

from etio.cli import run_action, validate_action_inputs


def test_validate_action_inputs_requires_tokens() -> None:
    with pytest.raises(ValueError, match="github_token, groq_api_key"):
        validate_action_inputs({})


def test_run_action_writes_scaffold_outputs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    output_path = tmp_path / "github-output"
    monkeypatch.setenv("INPUT_GITHUB_TOKEN", "token")
    monkeypatch.setenv("INPUT_GROQ_API_KEY", "key")
    monkeypatch.setenv("GITHUB_OUTPUT", str(output_path))

    assert run_action() == 0
    assert output_path.read_text(encoding="utf-8") == (
        "status=scaffolded\\n" "breaking-commit=\\n" "report-url=\\n" "diagnosis=\\n"
    )
