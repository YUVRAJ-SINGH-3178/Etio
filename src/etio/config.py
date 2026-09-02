"""Load Etio configuration from action environment variables and repository YAML."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

DEFAULT_GROQ_MODEL = "openai/gpt-oss-20b"
DEFAULT_DIAGNOSIS_TIMEOUT_SECONDS = 60.0


class ConfigurationError(ValueError):
    """Raised when Etio configuration is invalid or unsafe to read."""


@dataclass(frozen=True, slots=True)
class EtioConfig:
    """Configuration values that are safe to use during an Etio action run."""

    groq_model: str = DEFAULT_GROQ_MODEL
    diagnosis_timeout_seconds: float = DEFAULT_DIAGNOSIS_TIMEOUT_SECONDS
    workflow_file: str | None = None
    report_mode: str = "auto"
    auto_pr: bool = False


def load_config(
    config_path: str | None,
    environment: Mapping[str, str],
    repository_path: Path | None = None,
) -> EtioConfig:
    """Resolve inputs from environment first, then an optional repository config."""
    settings = _read_config_file(config_path, repository_path)
    return EtioConfig(
        groq_model=_string_setting(
            environment,
            settings,
            "INPUT_GROQ_MODEL",
            "ETIO_GROQ_MODEL",
            "groq-model",
            DEFAULT_GROQ_MODEL,
        ),
        diagnosis_timeout_seconds=_positive_float_setting(
            environment,
            settings,
            "INPUT_DIAGNOSIS_TIMEOUT_SECONDS",
            "ETIO_DIAGNOSIS_TIMEOUT_SECONDS",
            "diagnosis-timeout-seconds",
            DEFAULT_DIAGNOSIS_TIMEOUT_SECONDS,
        ),
        workflow_file=_optional_string_setting(
            environment,
            settings,
            "INPUT_WORKFLOW_FILE",
            "ETIO_WORKFLOW_FILE",
            "workflow-file",
        ),
        report_mode=_report_mode_setting(
            environment,
            settings,
            "INPUT_REPORT_MODE",
            "ETIO_REPORT_MODE",
            "report-mode",
        ),
        auto_pr=_boolean_setting(
            environment,
            settings,
            "INPUT_AUTO_PR",
            "ETIO_AUTO_PR",
            "auto-pr",
            False,
        ),
    )


def _read_config_file(
    config_path: str | None, repository_path: Path | None
) -> Mapping[str, Any]:
    if not config_path:
        return {}
    root = (repository_path or Path.cwd()).resolve()
    candidate = (root / config_path).resolve()
    if not candidate.is_relative_to(root):
        raise ConfigurationError(
            "config-path must stay within the checked-out repository."
        )
    if not candidate.exists():
        return {}
    if not candidate.is_file():
        raise ConfigurationError("config-path must point to a file.")
    try:
        parsed = yaml.safe_load(candidate.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as error:
        raise ConfigurationError(
            "Etio could not read its configuration file."
        ) from error
    if parsed is None:
        return {}
    if not isinstance(parsed, Mapping):
        raise ConfigurationError("Etio configuration must be a YAML mapping.")
    return parsed


def _setting(
    environment: Mapping[str, str],
    settings: Mapping[str, Any],
    input_name: str,
    environment_name: str,
    config_name: str,
    default: Any,
) -> Any:
    for name in (input_name, environment_name):
        value = environment.get(name)
        if value:
            return value
    return settings.get(config_name, default)


def _string_setting(
    environment: Mapping[str, str],
    settings: Mapping[str, Any],
    input_name: str,
    environment_name: str,
    config_name: str,
    default: str,
) -> str:
    value = _setting(
        environment, settings, input_name, environment_name, config_name, default
    )
    if not isinstance(value, str) or not value.strip():
        raise ConfigurationError(f"{config_name} must be a non-empty string.")
    return value.strip()


def _optional_string_setting(
    environment: Mapping[str, str],
    settings: Mapping[str, Any],
    input_name: str,
    environment_name: str,
    config_name: str,
) -> str | None:
    value = _setting(
        environment, settings, input_name, environment_name, config_name, None
    )
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise ConfigurationError(f"{config_name} must be a string.")
    return value.strip() or None


def _positive_float_setting(
    environment: Mapping[str, str],
    settings: Mapping[str, Any],
    input_name: str,
    environment_name: str,
    config_name: str,
    default: float,
) -> float:
    value = _setting(
        environment, settings, input_name, environment_name, config_name, default
    )
    if isinstance(value, bool):
        raise ConfigurationError(f"{config_name} must be a positive number.")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as error:
        raise ConfigurationError(f"{config_name} must be a positive number.") from error
    if parsed <= 0:
        raise ConfigurationError(f"{config_name} must be a positive number.")
    return parsed


def _boolean_setting(
    environment: Mapping[str, str],
    settings: Mapping[str, Any],
    input_name: str,
    environment_name: str,
    config_name: str,
    default: bool,
) -> bool:
    value = _setting(
        environment, settings, input_name, environment_name, config_name, default
    )
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes"}:
            return True
        if normalized in {"false", "0", "no"}:
            return False
    raise ConfigurationError(f"{config_name} must be true or false.")


def _report_mode_setting(
    environment: Mapping[str, str],
    settings: Mapping[str, Any],
    input_name: str,
    environment_name: str,
    config_name: str,
) -> str:
    value = _setting(
        environment, settings, input_name, environment_name, config_name, "auto"
    )
    if not isinstance(value, str) or value not in {
        "auto",
        "pull-request",
        "commit",
        "none",
    }:
        raise ConfigurationError(
            f"{config_name} must be auto, pull-request, commit, or none."
        )
    return value
