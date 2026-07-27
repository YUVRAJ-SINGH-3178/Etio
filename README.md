# Etio

Etio is a GitHub composite Action that will investigate a failed CI job in the
repository where it runs. It is designed to locate relevant failure context,
compare the failure against recent commits, request a redacted diagnosis from
Groq, and publish the result back to GitHub.

Etio is currently in its scaffolding phase. The action validates its required
inputs and exposes stable placeholder outputs, but it does not yet retrieve
logs, call Groq, or post reports. Do not rely on it for CI diagnosis yet.

## Intended use

Add Etio in a diagnostic workflow that runs after a failure. The workflow must
grant only the permissions Etio needs:

```yaml
permissions:
  contents: read
  checks: read
  pull-requests: write

steps:
  - uses: YUVRAJ-SINGH-3178/Etio@v0
    with:
      github-token: ${{ secrets.GITHUB_TOKEN }}
      groq-api-key: ${{ secrets.GROQ_API_KEY }}
```

The `github-token` and `groq-api-key` inputs are required. Optional inputs are
`workflow-file`, `config-path` (default `.github/etio.yml`), `auto-pr` (default
`false`), and `max-diff-lines` (default `400`).

## Development

Etio requires Python 3.11 or later.

```bash
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
pytest
ruff check .
black --check .
```

Then install the hooks once:

```bash
pre-commit install
```

## Security

Future diagnosis requests will redact likely tokens, passwords, API keys, and
connection strings before logs or diffs leave the GitHub runner. Etio does not
auto-merge pull requests. Its future auto-PR capability will be opt-in and
require human review.

## License

Etio is distributed under the [MIT License](LICENSE).
