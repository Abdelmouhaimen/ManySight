# Source connections and credentials

ManySight coordinates source access but is not a video proxy. A worker running where the
camera or file is reachable opens the source and submits observations. The server never
connects to the feed.

A `Source` is the logical device plus non-secret connection configuration. A
`Credential` is separately protected authentication material. Normal source discovery
returns the former and only a redacted credential-status summary of the latter.

## Two independent choices

`connection_mode` describes where the worker runs (`agent_local` or `edge_gateway`).
`connection_management` describes where connection material is managed:

- `manysight_managed`: safe structured fields live on the source; usernames/passwords
  live in the separate `source_credentials` table as AES-256-GCM ciphertext.
- `external_secret`: the source exposes only `locator.local_secret_ref`; the worker
  resolves that name through its environment, keychain, or ignored configuration.

Supported managed configurations are webcam `device_index`; RTSP host/port/path,
transport, and optional username/password; HTTP(S)/MJPEG URL with optional Basic auth;
and a worker-local file path. URLs containing embedded user information are rejected.

Managed WebRTC, sensor, and custom connections are not implemented. Those source kinds
can use external-secret configuration where a worker-specific integration understands
the referenced value.

## Deployment keys

`MANYSIGHT_CREDENTIAL_KEY` must be URL-safe base64 that decodes to exactly 32 random
bytes. ManySight does not generate, persist, or fall back to an implicit key. Missing or
malformed configuration fails closed for credential writes and reads. Rotating this key
currently requires decrypting and re-encrypting credentials under controlled operator
tooling; changing it directly makes existing ciphertext unreadable.

`GET /api/v1/sources/{source_id}/connection` is the one endpoint that returns usable
connection material. It carries no access key of its own: ManySight is a local,
single-workspace deployment, and a second key protecting one route inside a workspace the
caller already reaches was ceremony rather than a boundary. `MANYSIGHT_API_KEY` guards it
along with the rest of the API, and it stays protected when `MANYSIGHT_PUBLIC_READS`
opens the ordinary read surface — enabling open reads never opens credentials.

The boundary that matters is which *operation* returns secrets, and that is unchanged:
only this explicit resolution does.

Normal source list/get responses include only safe configuration and
`credential_status.configured`. They never contain plaintext credentials or ciphertext.
Edits preserve credentials when the `credentials` field is omitted, replace them when a
new credentials object is supplied, and remove them only with `clear_credentials: true`
or a deliberate switch to external management.

Back up the encryption key separately from the SQLite database. A database backup
without its matching key cannot recover managed credentials; storing the key only beside
the database defeats the intended separation. The repository does not currently include
an automated key-rotation command.

## Worker resolution

The Python SDK resolves capture input in this order:

1. explicit `local_connection` passed by worker code;
2. a managed webcam's public device index, or managed resolution for other kinds;
3. an external `local_secret_ref` environment value.

Never log the resolution response or the assembled camera URL.

The SDK's normal worker flow is:

```python
client = ManySight(platform_url, api_key=api_key)
source = client.source(source_id)
capture = client.open_capture(source)
```

An explicit `local_connection` remains available for development and integration
overrides. `MANYSIGHT_SOURCE_CONNECTION` is accepted by the example argument parser as
such an override; it is not the preferred managed-source configuration mechanism.
