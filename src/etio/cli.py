"""Command-line entry point for the Etio composite action."""

import os
from collections.abc import Mapping


def validate_action_inputs(environment: Mapping[str, str]) -> None:
    """Fail early when required action inputs are absent."""
    missing = [
        name
        for name in ("INPUT_GITHUB_TOKEN", "INPUT_GROQ_API_KEY")
        if not environment.get(name)
    ]
    if missing:
        names = ", ".join(name.removeprefix("INPUT_").lower() for name in missing)
        raise ValueError(f"Missing required Etio action input(s): {names}.")


def write_placeholder_outputs(environment: Mapping[str, str]) -> None:
    output_path = environment.get("GITHUB_OUTPUT")
    if not output_path:
        return
    with open(output_path, "a", encoding="utf-8") as output_file:
        output_file.write("status=scaffolded\\n")
        output_file.write("breaking-commit=\\n")
        output_file.write("report-url=\\n")
        output_file.write("diagnosis=\\n")


def run_action() -> int:
    """Validate phase-one plumbing and publish placeholder action outputs."""
    validate_action_inputs(os.environ)
    write_placeholder_outputs(os.environ)
    print("Etio scaffolding is installed and ready for failure-context extraction.")
    return 0


if __name__ == "__main__":
    raise SystemExit(run_action())
