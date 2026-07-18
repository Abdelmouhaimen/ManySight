# StoreLens manual testing tutorial

StoreLens is the hosted observation and insight layer. It stores logical source records,
geometry, worker lifecycle, raw observations, derived analytics, and dashboard insight
definitions. It does **not** open a webcam/RTSP feed or store its URL and credentials.
The worker or Codex task opens the camera on the device where it runs.

## 1. Install and start the platform

```powershell
cd C:\Users\abdo-\Desktop\projects\storelens
python -m pip install -r requirements.txt
npm install --prefix dashboard
npm run build --prefix dashboard
python -m uvicorn server.app:app --host 127.0.0.1 --port 8000
```

Open <http://localhost:8000>. OpenAPI is at <http://localhost:8000/docs>, the agent
guide is at <http://localhost:8000/agent.md>, and discovery metadata is at
<http://localhost:8000/.well-known/storelens.json>.

## 2. Test a fresh workspace without demo data

Create a logical webcam source from the interactive docs (`POST /api/v1/sources`) or
PowerShell:

```powershell
$body = @{
  name = "Laptop webcam"
  kind = "webcam"
  locator = @{ device_index = 0 }
  capabilities = @("video")
  metadata = @{ purpose = "people count" }
} | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri http://localhost:8000/api/v1/sources -ContentType application/json -Body $body
```

Verify manually:

- **Sources** shows the new source as `never`, without a thumbnail, URL, password, or
  server-side Test frame button.
- `POST /api/v1/sources` rejects a locator containing `url`, `password`, `token`, or an
  `rtsp://`/`http://` value.
- `POST /api/v1/sources/{id}/snapshot` returns 405 because the hosted app cannot capture
  a frame.
- **Detections** is empty until a worker sends observations.
- **Insights** is empty until an insight definition is registered.

## 3. Connect Codex through MCP

For local stdio MCP, add this to `%USERPROFILE%\.codex\config.toml` and adjust the path:

```toml
[mcp_servers.storelens]
command = "python"
args = ["C:/Users/abdo-/Desktop/projects/storelens/mcp_server/server.py"]

[mcp_servers.storelens.env]
STORELENS_URL = "http://localhost:8000"
```

Restart Codex, then start a new task with a short prompt:

> Start with the StoreLens platform guide. Use my logical webcam source to count people
> in real time. Open the webcam locally, register and run a worker, send raw per-frame
> count observations labelled `person`, verify them, and register a line insight.

Codex should discover the source and platform contract over MCP. It may create the
worker code in its own workspace. Camera access stays on the Codex/worker machine.

## 4. What a correct worker must do

A worker should:

1. Resolve the camera locally, for example webcam device `0` or an RTSP URL stored in a
   local environment variable/keychain.
2. Register a job with the source ID and `count` event type.
3. Register a worker instance and heartbeat every 5–15 seconds.
4. Read frames and post observations in batches every 1–5 seconds.
5. Obey `stop`/`restart` returned by heartbeats and close open zone visits at shutdown.
6. Verify rows through `get_events` and analytics, then register an insight.

For a people-count curve, each event is a raw sample, not a precomputed chart:

```json
{
  "source_id": 1,
  "event_type": "count",
  "label": "person",
  "value": 2,
  "ts": 1784390400.125
}
```

After observations arrive, **Sources** changes from `never` to `active`, shows the
ingestion time and worker state, and increments the event count. **Detections** shows
timestamps with seconds and milliseconds. A line insight uses the `counts` dataset and
parameters such as `source_id`, `label`, aggregation, and bucket size.

## 5. Test source CRUD through MCP

Use `create_source`, `list_sources`, `get_source`, `update_source`, and `delete_source`.
A safe webcam source looks like:

```json
{
  "name": "Hall webcam",
  "kind": "webcam",
  "locator": {"device_index": 0},
  "capabilities": ["video"],
  "metadata": {"space": "main hall"}
}
```

An RTSP source should expose only a reference, for example
`{"local_secret_ref":"main_hall_rtsp"}`. The actual RTSP URL belongs in the worker's
secret store, never in StoreLens.

## 6. Connect to the hosted Cloudflare MCP transport

The deployment advertises its exact URLs through `/api/v1/platform-config` and
`/agent.md`. Keep the MCP bearer token in an environment variable and add this to Codex:

```toml
[mcp_servers.storelens_cloud]
url = "https://your-storelens-host.example/mcp"
bearer_token_env_var = "STORELENS_CLOUD_MCP_TOKEN"
```

Restart Codex after setting `STORELENS_CLOUD_MCP_TOKEN`. The hosted Worker validates the
Bearer token before forwarding MCP requests; StoreLens then uses its private REST key for
tool mutations. See `deploy/cloudflare/README.md` for deployment and persistence details.

## 7. Test geometry and derived analytics

Frames used for calibration are captured locally. Agents send only matching pixel/map
points and zone-view polygons to StoreLens.

- Heatmap: `detection` rows with map/pixel points.
- Count curve: `count` rows with `label` and per-frame `value`.
- Dwell: matching `zone_enter` and `zone_exit` rows with stable `track_id`.
- Flow: zone enters by stable track.
- State timeline: `state_change` only when the state flips, plus a startup anchor.

For a mattress, shelf, table, or conveyor, use a named planar projection surface. Do
not subtract height from map Y. StoreLens preserves the geometry revisions used when
each event was ingested.

## 8. Optional deterministic example data

The seed is optional and replaces local sources/zones with synthetic records:

```powershell
python scripts/seed_demo.py
```

It creates logical sources with no camera credentials or snapshots, raw historical
observations, and several insight definitions. It is useful for chart and analytics
checks but is not required for a real manual test.

## 9. Current production boundary

This implementation provides source decoupling, source CRUD over REST/MCP, discovery,
and a remote MCP transport foundation. Before serving unrelated customers, add tenant
isolation, user/agent OAuth, scoped authorization, encrypted deployment secrets,
retention/audit controls, rate limits, and a supervisor for edge workers. A Ginse
marketplace action also needs its signed `/run` and idempotency contract; it should
invoke this workflow, not move camera access into the hosted dashboard.
