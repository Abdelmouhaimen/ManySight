# Source connections and credentials

StoreLens coordinates source access but is not a video proxy. A worker running where the
camera or file is reachable opens the source and submits observations. The server never
connects to the feed.

## Two independent choices

`connection_mode` describes where the worker runs (`agent_local` or `edge_gateway`).
`connection_management` describes where connection material is managed:

- `storelens_managed`: safe structured fields live on the source; usernames/passwords
  live in the separate `source_credentials` table as AES-256-GCM ciphertext.
- `external_secret`: the source exposes only `locator.local_secret_ref`; the worker
  resolves that name through its environment, keychain, or ignored configuration.

Supported managed configurations are webcam `device_index`; RTSP host/port/path,
transport, and optional username/password; HTTP(S)/MJPEG URL with optional Basic auth;
and a worker-local file path. URLs containing embedded user information are rejected.

## Deployment keys

`STORELENS_CREDENTIAL_KEY` must be URL-safe base64 that decodes to exactly 32 random
bytes. StoreLens does not generate, persist, or fall back to an implicit key. Missing or
malformed configuration fails closed for credential writes and reads. Rotating this key
currently requires decrypting and re-encrypting credentials under controlled operator
tooling; changing it directly makes existing ciphertext unreadable.

`STORELENS_CREDENTIAL_ACCESS_KEY` protects
`GET /api/v1/sources/{source_id}/connection`. This endpoint is header-only
(`X-StoreLens-Credential-Key`), remains protected when public reads are enabled, and does
not accept query-string credentials. If the dedicated key is absent, an explicitly
configured `STORELENS_API_KEY` is the compatibility fallback; with neither configured,
resolution is disabled.

Normal source list/get responses include only safe configuration and
`credential_status.configured`. They never contain plaintext credentials or ciphertext.
Edits preserve credentials when the `credentials` field is omitted, replace them when a
new credentials object is supplied, and remove them only with `clear_credentials: true`
or a deliberate switch to external management.

## Worker resolution

The Python SDK resolves capture input in this order:

1. explicit `local_connection` passed by worker code;
2. privileged StoreLens-managed resolution;
3. an external `local_secret_ref` environment value.

Set `STORELENS_CREDENTIAL_ACCESS_KEY` only for workers that are authorized to resolve
managed connections. Never log the resolution response or assembled camera URL.
