# Etio Privacy Notice

Last updated: 2026-10-11

This notice describes the Etio GitHub Action maintained by Yuvraj Singh
([repository](https://github.com/YUVRAJ-SINGH-3178/Etio)). Etio is distributed
as source code and runs in the GitHub Actions runner selected by the repository
owner. The maintainer does not operate a hosted Etio service or receive a copy
of diagnosis requests on a maintainer-controlled server.

## Information Etio processes

When invoked, Etio uses the supplied GitHub token to retrieve the selected
workflow run and failed-job logs, repository history, and relevant code diffs.
These sources can contain source code, job output, workflow and repository
names, commit metadata, usernames, email addresses, or other personal or
confidential information. The GitHub token is used for GitHub API requests and
is not sent to Groq.

Before inference, Etio selects failure-focused context and applies
best-effort redaction for likely credentials. Redaction is not anonymization
and may miss personal data, proprietary code, or secrets. The selected,
redacted failure context and diff are sent to Groq's chat-completions API to
generate a diagnosis. The Groq API key is sent to Groq for request
authentication and is not included in the prompt. Groq returns the diagnosis
to the runner. If reporting is configured, Etio may publish the diagnosis and
an optional suggested patch in a GitHub pull-request or commit comment.
Optional real bisection and auto-PR features can create temporary branches or
workflow runs, and draft pull requests, when enabled.

## Providers, location, and retention

Etio does not maintain its own persistent database. Runtime data is held in
memory on the runner for the action's execution. GitHub processes workflow
logs, repository data, comments, branches, and workflow runs under the
repository owner's GitHub configuration and applicable retention controls.
The repository owner selects the runner and its location.

Groq processes inference requests as a third-party provider. According to
[Groq's data documentation](https://console.groq.com/docs/your-data), inference
inputs and outputs are not retained by default, but may be temporarily retained
for reliability troubleshooting or abuse investigations for up to 30 days.
Groq says retained customer data is stored in the United States. Zero Data
Retention is an account-level setting and is **not assumed to be enabled** for
Etio users. Review Groq's current [Data Processing Addendum](https://console.groq.com/docs/legal/customer-data-processing-addendum)
and [data controls](https://console.groq.com/docs/your-data) before use; provider
terms and controls may change.

Etio does not use failure context to advertise products or sell personal data.
Etio does not intentionally make employment, legal, credit, health, or other
decisions about individuals. AI output is advisory and requires human review.

## Choices and requests

Do not enable Etio if you do not authorize this processing. Repository owners
are responsible for choosing which repositories and workflows use Etio,
providing required notices, obtaining consent where required, and ensuring
they have authority to send log and code context to Groq. Avoid processing
sensitive personal data. Stop future processing by removing Etio from the
workflow and revoke credentials through GitHub and Groq. Data already written
to GitHub comments or logs is governed by GitHub retention controls; Groq
retention is governed by the account settings and terms described above.

For privacy/data requests, contact Yuvraj Singh at
[yuvraj.lee3178@gmail.com](mailto:yuvraj.lee3178@gmail.com). For ordinary
product feedback, use the [Etio GitHub issue tracker](https://github.com/YUVRAJ-SINGH-3178/Etio/issues).
Do **not** include personal data, credentials, or private logs in public
issues. Privacy requests will be handled in accordance with applicable law.

This notice describes Etio's current design and providers. Users should review
the applicable GitHub and Groq terms and ensure their use of Etio complies with
the privacy laws and policies that apply to their repositories.
