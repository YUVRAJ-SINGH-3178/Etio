# Contributing to Etio

Thanks for improving Etio. Please open an issue before proposing a substantial
feature so its scope can be discussed before implementation.

## Development workflow

Use Python 3.11 or later. Install the project and local tooling, then run the
same checks used by CI:

```bash
python -m pip install -e .
python -m pip install pytest ruff black pre-commit
pre-commit install
pytest
ruff check .
black --check .
```

Keep changes focused. New behavior needs tests that describe externally visible
results. Use type hints on all function signatures and raise errors that tell a
user how to correct the problem.

## Pull requests

Use conventional commit prefixes such as `feat:`, `fix:`, `test:`, and
`refactor:`. Describe the user-facing effect and include test results. Do not
include secrets, live workflow logs, or API tokens in issues, commits, or pull
requests.

## Licensing and attribution

All contributions are submitted under the MIT License. Etio is original work;
it currently incorporates no copied implementation from external projects. If
you propose code inspired by another project, identify the source in the pull
request and confirm that its license is compatible with MIT before it is used.
