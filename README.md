# Etio

Etio is a GitHub composite Action that will investigate a failed CI job in the
repository where it runs. It is designed to locate relevant failure context,
compare the failure against recent commits, request a redacted diagnosis from
Groq, and publish the result back to GitHub.

Etio currently retrieves a completed failed job's GitHub Actions log, extracts
an error-focused local context, and compares the failing revision with the
most recent successful ancestor of the same workflow. It sends only redacted
failure and diff context to Groq for a structured diagnosis, then posts an
idempotent comment to the associated pull request when one is available.

## Intended use

Add Etio in a downstream diagnostic job, after the failed job has completed.
It cannot reliably download the log of the job that is still running Etio.
The job must grant the action read access to Actions logs:

```yaml
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - run: exit 1

  diagnose:
    if: ${{ failure() }}
    needs: test
    runs-on: ubuntu-latest
    permissions:
      actions: read
      contents: read
      pull-requests: write
    steps:
      - uses: YUVRAJ-SINGH-3178/Etio@v0
        with:
          github-token: ${{ secrets.GITHUB_TOKEN }}
          groq-api-key: ${{ secrets.GROQ_API_KEY }}
```

The `github-token` and `groq-api-key` inputs are required. Optional inputs are
`workflow-file`, `config-path` (default `.github/etio.yml`), `auto-pr` (default
`false`), `max-diff-lines` (default `400`), and `failed-job-id`. When a run has
more than one failed job, set `failed-job-id` to the numeric job ID from the
Actions API. Etio refuses to guess which log to diagnose.

Etio uses `GITHUB_RUN_ID` and `GITHUB_RUN_ATTEMPT` to retrieve jobs from the
specific workflow attempt. It follows GitHub's temporary log-download URL
without forwarding the GitHub token, and does not write raw logs to disk.

For the cheap comparison, Etio lists completed runs on the current branch for
the configured `workflow-file` (or the current workflow when available). It
uses Git ancestry to ensure that the selected successful run actually precedes
the failing revision, then bounds the resulting diff to `max-diff-lines`.

## Configuration

Etio resolves settings in this order: an explicit action input, an `ETIO_*`
environment variable, then `.github/etio.yml` (or the configured `config-path`).
For example:

```yaml
groq-model: openai/gpt-oss-20b
diagnosis-timeout-seconds: 60
workflow-file: ci.yml
```

The default model is `openai/gpt-oss-20b`. Use a Groq model that supports JSON
Schema structured outputs if you override it. Etio requests a strict schema and
rejects malformed or unexpected model responses rather than guessing.

## Reporting

`report-mode` defaults to `auto`: Etio posts a pull-request comment only when
the event payload identifies exactly one pull request for the same repository.
Set `pr-number` when reporting from another event type, or set
`report-mode: pull-request` to require a PR target. Use `report-mode: none` to
produce a diagnosis without posting it.

Commit comments are an explicit `report-mode: commit` opt-in and require
`contents: write`, which is broader than the standard PR-reporting scope. Etio
does not infer a commit report from a branch or workflow run. It updates only a
comment created by the same GitHub token and carrying an immutable
target-specific Etio marker.

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

Etio redacts likely tokens, passwords, API keys, URL credentials, connection
strings, and private-key blocks before logs or diffs leave the GitHub runner.
Raw logs and diffs are kept in memory only and are not printed or exported as
action outputs. The Groq request asks for a JSON Schema-constrained diagnosis,
and Etio validates and redacts the response again before exposing it. Etio does
not auto-merge pull requests. Its future auto-PR capability will be opt-in and
require human review.

## License

Etio is distributed under the [MIT License](LICENSE).
