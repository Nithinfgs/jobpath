# Security policy

## Reporting a vulnerability

Please report security issues privately through GitHub's
[private vulnerability reporting](https://github.com/Nithinfgs/jobpath/security/advisories/new)
instead of opening a public issue. Expect a first response within a few days.

## What jobpath touches

- It only **reads** from the GitHub REST API (workflow runs and jobs). It never writes to GitHub.
- Your token is read from `GITHUB_TOKEN`, `GH_TOKEN` or `gh auth token`, and is sent only to `api.github.com`.
- `--save` writes raw run data (job and step names, timestamps, URLs) to a local file. It does not contain
  your token, but review it before attaching it to a public issue.
- No telemetry, no third-party network calls, no runtime dependencies.

A token with `actions: read` access is sufficient.
