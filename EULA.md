# Etio End User License Agreement

Effective date: 2026-10-11

This agreement describes use of the Etio GitHub Action, maintained by Yuvraj
Singh. By configuring or running Etio, you agree to these use terms on behalf
of yourself or the organization you represent. Do not run Etio if you lack
authority to process and disclose the relevant repository data to its
configured providers.

## License and use

Etio's source code is provided under the [MIT License](LICENSE). This document
describes use of the Action and its data flows; it does not remove or narrow
rights granted by the MIT License for the source code. GitHub's terms apply to
GitHub Actions and the GitHub Marketplace. Groq's applicable terms govern use
of Groq services.

Use Etio only where you have authority to access and process the relevant
workflow logs, repository content, and diff. You are responsible for providing
notices and obtaining permissions or consents required for that processing,
including disclosure of selected redacted context to Groq. Do not submit data
unlawfully or use Etio to make automated decisions about a person's
employment, legal status, credit, healthcare, or other significant rights.

## AI output and review

Etio sends selected, best-effort-redacted failure context and diff to Groq to
generate an AI diagnosis. Redaction can miss sensitive information. AI output
can be inaccurate, incomplete, or unsafe. Review diagnoses and suggested
patches before relying on them or applying changes. Auto-PR creates drafts
only; Etio does not merge them. Etio is not professional, legal, medical,
security, or other expert advice.

## Credentials and permissions

Supply credentials through GitHub Actions secrets or another secure mechanism,
grant only the GitHub permissions required for enabled features, and protect
or revoke credentials if compromised. Etio uses the GitHub token for GitHub
API calls and the Groq key for Groq authentication. Do not place credentials
in workflow inputs, logs, source code, or public issues.

## Third-party services and data

Etio runs in the GitHub Actions runner selected by the repository owner and
uses GitHub and Groq to provide its functionality. See the [Privacy Notice](PRIVACY.md)
for data flows, sharing, and provider retention information. Do not run Etio
on data you lack authority to disclose to these providers.

## Availability and responsibility

To the maximum extent permitted by applicable law, Etio is provided on an
as-is and as-available basis. Nothing in this agreement excludes liability
that cannot lawfully be excluded, or overrides the GitHub Marketplace
Developer Agreement, the MIT License, or applicable law. This agreement does
not replace or amend the GitHub Marketplace Developer Agreement or Groq's
terms.

For support or product feedback, use the [Etio issue tracker](https://github.com/YUVRAJ-SINGH-3178/Etio/issues)
without including secrets or personal data. For privacy requests, email
[Yuvraj Singh](mailto:yuvraj.lee3178@gmail.com).
