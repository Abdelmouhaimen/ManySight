# Security policy

## Reporting a vulnerability

Do not disclose a vulnerability, credential, private stream URL, camera address,
recording, database, or sensitive site geometry in a public issue.

**Maintainer action required:** this repository does not currently publish a verified
private vulnerability-reporting channel. Before public release, enable the hosting
provider's private vulnerability reporting feature or publish a monitored security
contact. Until then, use an existing private channel to the maintainers if one is
available; otherwise withhold sensitive details rather than filing them publicly.

General bugs that do not contain sensitive information may use the public issue tracker.

## Security scope and deployment responsibility

StoreLens can store managed source credentials encrypted with
`STORELENS_CREDENTIAL_KEY`. Operators must:

- generate, store, rotate, and back up the encryption key separately from the database;
- restrict `STORELENS_CREDENTIAL_ACCESS_KEY` to workers and agents authorized to open
  sources;
- protect API and MCP endpoints with appropriate authentication, TLS, network policy,
  and host/origin restrictions;
- keep resolved credentials in worker memory and out of logs, exceptions, and telemetry;
- protect database backups and understand that they require the matching encryption key
  to recover managed credentials;
- avoid placing source credentials in URLs, source locators, tracked configuration,
  issues, or command-line query parameters.

The general API accepts a query-string API key for browser SSE and protected-media
compatibility. Prefer the `X-API-Key` header elsewhere and configure proxies not to log
sensitive query strings. Managed source-connection resolution is always header-only.

The current optional API key is not a multi-user authorization system. StoreLens does
not attest to worker code or model output, and cooperative worker controls require an
external process supervisor. Deployers are responsible for camera authorization,
network access, retention, privacy, and compliance obligations.

Guided-demo media remains local and is served only from an allowlisted NVIDIA sample
directory. Demo session responses do not expose temporary database paths. Setup
promotion launches one fixed synchronized-stream module with validated paths; it is not
an arbitrary command runner. Do not expose the loopback-only demo stream service as a
production source gateway.

Security support applies to the current default branch. Historical releases do not have
a published support window yet.
