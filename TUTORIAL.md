# ManySight dashboard POC — launch and test tutorial

## 1. Install, build, and launch

```powershell
cd C:\Users\abdo-\Desktop\projects\storelens
python -m pip install -r requirements.txt
npm install --prefix dashboard
npm run build --prefix dashboard
python -m uvicorn server.app:app --host 127.0.0.1 --port 8000
```

Open <http://localhost:8000>. Every product workflow now runs in the React dashboard.
API docs are at <http://localhost:8000/docs>.

## 2. Fast empty-state test

With a fresh database, verify:

- Header says **ManySight**, workspace identity, `Setup incomplete`, and event-stream status.
- Sidebar contains Overview, Insights, Review, Detections, Streams, and Configure.
- Overview defines `Tracked visits` as distinct anonymous track IDs.
- Queue metric asks for a checkout or queue zone instead of inventing a number.
- Insights is honestly empty: "No insights registered yet" with an Add button.
- Detections shows an empty observation table plus its documentation panel.
- Review explains that signals require human review.
- Streams explains that snapshot status is not continuous health monitoring.
- Configure shows the Streams → Zones → Calibration → Analysis setup sequence.

## 3. Load deterministic example data

Warning: the seed replaces local sources and zones and marks the workspace as
`Example data`.

```powershell
python scripts/seed_demo.py
```

Reload the dashboard and choose **Last 24 hours**. Expected:

- Overview metric cards, a visitor-traffic curve, and two pinned insight cards
  (presence over time and the activity heatmap).
- Insights shows five registered example cards: presence line, activity heatmap,
  dwell by zone, zone-to-zone flow, and the fridge state timeline. Each card states
  its limitations.
- Detections lists the seeded raw observations (detections, zone enter/exit pairs,
  state changes) with working filters and a Load more button.
- Reviewable example signals under Review.
- Three example streams and setup state under Streams/Configure.

Note: the seed posts **raw observations only** — no precomputed dwell. Every dwell
number you see is derived by the platform from enter/exit pairs.

With the server running on the seeded database, the API-level checks (pagination,
derive-only dwell, derived alerts, insight registry) can be run in one go:

```bash
bash scripts/smoke_test.sh
```

Add live simulated traffic:

```powershell
python examples/simulate_shoppers.py --shoppers 8 --minutes 5
```

The event stream should remain green and the dashboard will refresh as batches arrive.

## 4. Test the human-review workflow

1. Open **Review**.
2. Select a new signal.
3. Change status to **In review**.
4. Add a note describing what was checked.
5. Save, then change the signal to **Resolved** or **Dismissed**.

This workflow intentionally uses neutral signal language. It does not label a person
or event as wrongdoing.

## 4b. Inspect raw detections

1. Open **Detections**.
2. Filter by event type, camera, zone, analysis job, track ID, or label; change the
   time range.
3. Expand a row to see its pixel point, projected map point, and attributes.
4. Pause **Follow live events**, then use **Load more** to page through history
   (following live resets pagination by design).
5. Open the "How to read this table" panel — it documents every column, every event
   type, and the enrichment pipeline (bbox → feet point → homography → zone).

## 4c. Curate the Insights catalogue

1. Open **Insights → Add insight** — templates reflect the data actually present.
2. Register one (e.g. a queue-presence metric), edit its title and limitations.
3. Pin it: it appears on Overview. Reorder cards with the arrow buttons.
4. Delete it or mark it hidden when done. An agent can do the same over MCP with
   `register_insight`.

## 5. Connect a real camera

1. Open **Streams → Add stream**.
2. Choose RTSP, HTTP, webcam, file, or WebRTC.
3. Enter the connection information and save.
4. Click **Test frame**.
5. Open **Configure → Space & zones**.
6. Choose **Place camera**, select the source, and click its location on the map.
7. Select the camera, adjust direction and field of view, then choose **Calibrate camera**.
8. Match at least four fixed floor points between the frame and map, save, and test several projected points.

The current Online/Offline status reflects the last snapshot test. It is not yet a
continuous heartbeat, FPS, or latency monitor.

## 6. Configure a space workflow

In **Configure**:

1. Set workspace name and data state.
2. Choose the space type and draw named polygon zones such as an entrance, checkout,
   main hall, classroom, meeting room, or equipment area.
3. Calibrate required streams.
4. Confirm the analysis job appears after its worker registers.
5. Add a narrow occupancy or dwell threshold.

Use `Live pilot` only when real camera streams and workers are connected. Keep seeded
or synthetic data marked `Example data`.

## 7. Connect Codex through MCP

Add to `%USERPROFILE%\.codex\config.toml`:

```toml
[mcp_servers.storelens]
command = "python"
args = ["C:/Users/abdo-/Desktop/projects/storelens/mcp_server/server.py"]

[mcp_servers.storelens.env]
STORELENS_URL = "http://localhost:8000"
```

Example bounded request:

> Inspect the entrance camera and build anonymous traffic and occupancy insights for
> the existing retail zones. Register the job, run the worker, post raw observations
> (detections and zone enter/exit pairs — the platform derives dwell), verify the
> resulting analytics, and register the insight cards so they appear on the dashboard.
> Explain limitations and do not infer identity or sensitive traits.

Codex can inspect snapshots and create workers, but registered job status does not yet
prove the process is still running. Runtime heartbeat/restart management remains POC
work.

Example school request:

> Inspect the main-hall camera and create a time curve for the number of children in
> the hall. Choose a suitable child/person classifier, register the analysis, run it
> outside the dashboard, submit labelled `count` events, verify the curve, and explain
> the validation limits.

Adding a camera source stores access metadata and enables snapshots. Codex can inspect
that source through the API/MCP and write a subscriber worker, but the dashboard does
not itself keep RTSP connections or model processes alive. The worker must run where
it can reach the camera and continuously post events.

## 8. Test API authentication

```powershell
$env:STORELENS_API_KEY
python -m uvicorn server.app:app --port 8000
```

Open **Configure → Technical details**, enter the same API key, and save. Workers and
the MCP server need the same key.

## 9. Responsive and failure checks

Test at desktop and approximately 390px mobile width:

- Mobile menu opens and routes correctly.
- No horizontal page overflow.
- Metric cards collapse to two columns.
- Stream cards and configuration forms collapse to one column.
- Keyboard focus remains visible.

Also test offline camera, empty zones, uncalibrated stream, API failure, no worker, no
signals, and live SSE reconnect states.

## 10. Known POC limitations

- Distinct track IDs are not automatically validated visits.
- Queue-zone dwell is platform-derived from enter/exit pairs (worker-reported dwell
  values are no longer trusted), but it is still not validated wait time.
- Ongoing alerts (loitering without an exit, door still open) evaluate only when new
  events are ingested; a silent zone re-alerts on the next batch.
- Source health is based on snapshot checks, not continuous telemetry.
- Job state is registration metadata, not worker process supervision.
- Insight lifecycle states (collecting/validating/degraded) are displayed but not yet
  automatically transitioned.
- Demo and live records still share the same SQLite database.
- Authentication, roles, tenants, retention policy, and audit controls are not
  production-ready.
