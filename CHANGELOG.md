# Changelog

All notable changes to Etio are documented here.

## [Unreleased]

### Added

- Opt-in workflow-dispatch real bisection with bounded candidate runs,
  first-parent confirmation, and exact-run polling.
- Cleanup of Etio-owned temporary refs after each dispatched candidate.

## [0.1.0] - 2026-07-27

### Added

- Initial pip-installable Etio package and composite Action plumbing.
- Local pytest, Ruff, Black, pre-commit, and GitHub Actions CI configuration.
- Actions log retrieval from an exact workflow attempt and local
  failure-context extraction without persisting raw logs.
- Last-successful workflow-run lookup and bounded local diff generation for
  cheap bisection.
- Environment-then-config YAML lookup, secret redaction, and validated
  structured Groq diagnosis.
- Idempotent, redacted pull-request and explicit commit diagnosis comments.
