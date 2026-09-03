"""Load Etio configuration from action environment variables and repository YAML."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

DEFAULT_GROQ_MODEL = "openai/gpt-oss-20b"
DEFAULT_DIAGNOSIS_TIMEOUT_SECONDS = 60.0
DEFAULT_BISECT_MAX_STEPS = 10
DEFAULT_BISECT_TIMEOUT_SECONDS = 900.0
DEFAULT_BISECT_POLL_SECONDS = 10.0
MAX_WORKFLOW_DISPATCH_INPUTS = 25


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
    real_bisect: bool = False
    bisect_max_steps: int = DEFAULT_BISECT_MAX_STEPS
    bisect_timeout_seconds: float = DEFAULT_BISECT_TIMEOUT_SECONDS
    bisect_poll_seconds: float = DEFAULT_BISECT_POLL_SECONDS
    bisect_workflow_inputs: Mapping[str, str] = field(default_factory=dict)


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
        real_bisect=_boolean_setting(
            environment,
            settings,
            "INPUT_REAL_BISECT",
            "ETIO_REAL_BISECT",
            "real-bisect",
            False,
        ),
        bisect_max_steps=_positive_integer_setting(
            environment,
            settings,
            "INPUT_BISECT_MAX_STEPS",
            "ETIO_BISECT_MAX_STEPS",
            "bisect-max-steps",
            DEFAULT_BISECT_MAX_STEPS,
        ),
        bisect_timeout_seconds=_positive_float_setting(
            environment,
            settings,
            "INPUT_BISECT_TIMEOUT_SECONDS",
            "ETIO_BISECT_TIMEOUT_SECONDS",
            "bisect-timeout-seconds",
            DEFAULT_BISECT_TIMEOUT_SECONDS,
        ),
        bisect_poll_seconds=_positive_float_setting(
            environment,
            settings,
            "INPUT_BISECT_POLL_SECONDS",
            "ETIO_BISECT_POLL_SECONDS",
            "bisect-poll-seconds",
            DEFAULT_BISECT_POLL_SECONDS,
        ),
        bisect_workflow_inputs=_workflow_inputs_setting(
            environment,
            settings,
            "INPUT_BISECT_WORKFLOW_INPUTS",
            "ETIO_BISECT_WORKFLOW_INPUTS",
            "bisect-workflow-inputs",
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


def _positive_integer_setting(
    environment: Mapping[str, str],
    settings: Mapping[str, Any],
    input_name: str,
    environment_name: str,
    config_name: str,
    default: int,
) -> int:
    value = _setting(
        environment, settings, input_name, environment_name, config_name, default
    )
    if isinstance(value, bool):
        raise ConfigurationError(f"{config_name} must be a positive integer.")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as error:
        raise ConfigurationError(
            f"{config_name} must be a positive integer."
        ) from error
    if parsed < 1 or str(parsed) != str(value).strip():
        raise ConfigurationError(f"{config_name} must be a positive integer.")
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


def _workflow_inputs_setting(
    environment: Mapping[str, str],
    settings: Mapping[str, Any],
    input_name: str,
    environment_name: str,
    config_name: str,
) -> Mapping[str, str]:
    value = _setting(
        environment, settings, input_name, environment_name, config_name, {}
    )
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as error:
            raise ConfigurationError(
                f"{config_name} must be a JSON object when set as an input."
            ) from error
        if not isinstance(value, Mapping):
            raise ConfigurationError(
                f"{config_name} must be a JSON object when set as an input."
            )
    if not isinstance(value, Mapping):
        raise ConfigurationError(f"{config_name} must be a mapping.")
    if len(value) > MAX_WORKFLOW_DISPATCH_INPUTS:
        raise ConfigurationError(
            f"{config_name} cannot contain more than "
            f"{MAX_WORKFLOW_DISPATCH_INPUTS} inputs."
        )
    workflow_inputs: dict[str, str] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not re.fullmatch(
            r"[A-Za-z_][A-Za-z0-9_-]{0,63}", key
        ):
            raise ConfigurationError(
                f"{config_name} has an invalid workflow input name."
            )
        if isinstance(item, (str, int, float, bool)):
            workflow_inputs[key] = (
                str(item).lower() if isinstance(item, bool) else str(item)
            )
        else:
            raise ConfigurationError(
                f"{config_name} values must be strings, numbers, or booleans."
            )
    return workflow_inputs
