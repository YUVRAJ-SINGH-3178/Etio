from pathlib import Path

import pytest

from etio.config import DEFAULT_GROQ_MODEL, ConfigurationError, load_config


def test_load_config_uses_environment_before_repository_settings(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / ".github" / "etio.yml"
    config_path.parent.mkdir()
    config_path.write_text(
        "groq-model: file-model\n"
        "diagnosis-timeout-seconds: 10\n"
        "workflow-file: ci.yml\n"
        "report-mode: commit\n"
        "auto-pr: true\n",
        encoding="utf-8",
    )

    config = load_config(
        ".github/etio.yml",
        {
            "ETIO_GROQ_MODEL": "environment-model",
            "ETIO_DIAGNOSIS_TIMEOUT_SECONDS": "25",
            "ETIO_REPORT_MODE": "pull-request",
        },
        tmp_path,
    )

    assert config.groq_model == "environment-model"
    assert config.diagnosis_timeout_seconds == 25
    assert config.workflow_file == "ci.yml"
    assert config.report_mode == "pull-request"
    assert config.auto_pr is True


def test_load_config_uses_defaults_without_a_file(tmp_path: Path) -> None:
    config = load_config(".github/etio.yml", {}, tmp_path)

    assert config.groq_model == DEFAULT_GROQ_MODEL
    assert config.report_mode == "auto"
    assert config.auto_pr is False


def test_load_config_parses_opt_in_real_bisection_settings(tmp_path: Path) -> None:
    config_path = tmp_path / ".github" / "etio.yml"
    config_path.parent.mkdir()
    config_path.write_text(
        "real-bisect: true\n"
        "bisect-max-steps: 7\n"
        "bisect-timeout-seconds: 120\n"
        "bisect-poll-seconds: 5\n"
        "bisect-workflow-inputs:\n"
        "  test-command: pytest tests/test_symptoms.py\n"
        "  retries: 2\n",
        encoding="utf-8",
    )

    config = load_config(".github/etio.yml", {}, tmp_path)

    assert config.real_bisect is True
    assert config.bisect_max_steps == 7
    assert config.bisect_timeout_seconds == 120
    assert config.bisect_poll_seconds == 5
    assert config.bisect_workflow_inputs == {
        "test-command": "pytest tests/test_symptoms.py",
        "retries": "2",
    }


def test_load_config_parses_bisection_input_json_from_the_action(
    tmp_path: Path,
) -> None:
    config = load_config(
        ".github/etio.yml",
        {
            "INPUT_BISECT_WORKFLOW_INPUTS": '{"test-command":"pytest -q"}',
            "INPUT_REAL_BISECT": "true",
        },
        tmp_path,
    )

    assert config.real_bisect is True
    assert config.bisect_workflow_inputs == {"test-command": "pytest -q"}


def test_load_config_rejects_paths_outside_repository(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="stay within"):
        load_config("../etio.yml", {}, tmp_path)


def test_load_config_rejects_invalid_values(tmp_path: Path) -> None:
    config_path = tmp_path / "etio.yml"
    config_path.write_text("auto-pr: not-a-boolean\n", encoding="utf-8")

    with pytest.raises(ConfigurationError, match="true or false"):
        load_config("etio.yml", {}, tmp_path)


def test_load_config_rejects_non_object_bisection_inputs(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="JSON object"):
        load_config(
            ".github/etio.yml",
            {"INPUT_BISECT_WORKFLOW_INPUTS": "[]"},
            tmp_path,
        )
