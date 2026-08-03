# StoreLens manual testing tutorial

StoreLens is the hosted observation and analytics layer. It stores logical source records,
geometry, worker lifecycle, raw observations (detection/measurement/state), derived
analytics, and saved dashboard analyses. It does **not** open a webcam/RTSP feed or store
its URL and credentials. The worker or Codex task opens the camera on the device where it runs.

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
- **Observations** is empty until a worker submits detection/measurement/state rows.
- **Analytics** is empty until a saved analysis is created.

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
> in real time. Open the webcam locally, register and run a worker, submit raw
> per-interval measurement observations named `people_present`, verify them, and save
> a line analysis.

Codex should discover the source and platform contract over MCP. It may create the
worker code in its own workspace. Camera access stays on the Codex/worker machine.

## 4. What a correct worker must do

A worker should:

1. Resolve the camera locally, for example webcam device `0` or an RTSP URL stored in a
   local environment variable/keychain.
2. Register a job with the source ID and `measurement` event type.
3. Register a worker instance and heartbeat every 5–15 seconds.
4. Read frames and submit observations in batches every 1–5 seconds.
5. Obey `stop`/`restart` returned by heartbeats. Never resolve a zone, pair an enter/exit,
   or compute a state change — the worker submits only detection/measurement/state.
6. Verify rows through `get_latest_observations`/`query_analytics`, then save an analysis.

For a people-count curve, each observation is a raw sample, not a precomputed chart:

```json
{
  "schema_version": 2,
  "observation_id": "cam1-1784390400125",
  "kind": "measurement",
  "source_id": 1,
  "name": "people_present",
  "value": 2,
  "value_kind": "gauge",
  "timestamp": 1784390400.125
}
```

After observations arrive, **Sources** changes from `never` to `active`, shows the
ingestion time and worker state, and increments the event count. **Observations** shows
timestamps with seconds and milliseconds. A line analysis uses `subject: "measurement"`
with `measures: ["latest"]` (or `"average"`), filtered by `measurement_names` and grouped
by time with a chosen bucket size.

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

## 6. Test geometry and derived analytics

Frames used for calibration are captured locally. Agents send only matching pixel/map
points and zone-view polygons to StoreLens.

- Heatmap / density: `detection` rows with a map/pixel point.
- Measurement curve: `measurement` rows with `name` and a per-interval `value`.
- Dwell / visits: ordinary tracked `detection` rows in a zone — no enter/exit pair needed;
  StoreLens groups consecutive same-zone detections per entity into a visit itself.
- Flow / transitions: the same tracked `detection` rows, read as a per-entity zone sequence.
- State timeline: `state` rows sent on every sample, including repeats — StoreLens
  coalesces consecutive identical samples into intervals itself.

For a mattress, shelf, table, or conveyor, use a named planar projection surface. Do
not subtract height from map Y. StoreLens preserves the geometry revisions used when
each observation was ingested.

## 7. Optional deterministic example data

The seed is optional and replaces local sources/zones with synthetic records:

```powershell
python scripts/seed_demo.py
```

It creates logical sources with no camera credentials or snapshots, raw historical
observations (detection/measurement/state only — no legacy zone-entry or state-change
rows), and several saved analyses. It is useful for chart and analytics checks but is
not required for a real manual test.

## 8. Current boundary

This implementation provides source decoupling, source CRUD over REST/MCP, discovery,
and a remote MCP transport foundation. Anyone self-hosting beyond a single workspace
should add authorization scoping, retention/audit controls, rate limits, and a
supervisor for edge workers — none of that is implemented today.
