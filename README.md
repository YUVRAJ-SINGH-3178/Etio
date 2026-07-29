# Etio

Etio is a GitHub composite Action that will investigate a failed CI job in the
repository where it runs. It is designed to locate relevant failure context,
compare the failure against recent commits, request a redacted diagnosis from
Groq, and publish the result back to GitHub.

Etio currently retrieves a completed failed job's GitHub Actions log and
extracts an error-focused local context. It does not yet locate the breaking
commit, call Groq, or post reports, so it is not yet a complete CI diagnosis
tool.

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
connection strings before logs or diffs leave the GitHub runner. Raw logs are
kept in memory only and are not printed or exported as action outputs. Etio
does not auto-merge pull requests. Its future auto-PR capability will be
opt-in and require human review.

## License

Etio is distributed under the [MIT License](LICENSE).
