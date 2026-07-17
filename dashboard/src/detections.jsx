import { useEffect, useState } from "react";
import { Activity, RefreshCw } from "lucide-react";
import { api, formatPreciseDateTime } from "./api.js";
import {
  Badge,
  EmptyState,
  ErrorState,
  LoadingState,
  PageHeader,
  Panel,
  RangeSelect,
} from "./components.jsx";

const EVENT_TYPES = [
  ["all", "all types"],
  ["detection", "detection"],
  ["zone_enter", "zone enter"],
  ["zone_exit", "zone exit"],
  ["zone_dwell", "zone dwell (deprecated)"],
  ["transition", "transition"],
  ["state_change", "state change"],
  ["count", "count"],
  ["custom", "custom"],
];

const COLUMN_DOCS = [
  [
    "Time",
    "When the worker observed it (event `ts`, not ingestion time), displayed through milliseconds. Expand the row for the exact epoch value.",
  ],
  ["Type", "The event_type posted by the worker — see the glossary below."],
  ["Source", "The camera the observation came from (`source_id`)."],
  [
    "Zone",
    "Explicit zone from the worker, or auto-assigned by the platform when the projected map point falls inside a zone polygon.",
  ],
  [
    "Track / label",
    "`track_id` (stable per-object ID from the worker's tracker) or, for state and count events, the `label`.",
  ],
  [
    "Value",
    "The numeric sample for count events. For deprecated zone_dwell rows this is the worker's claim — the platform ignores it.",
  ],
  ["Job", "The registered analysis job that posted the event."],
  [
    "Details (expand)",
    "The complete observation evidence: pixel/map point, bbox, keypoints or mask, point meaning, projection plane, zone-assignment method, geometry revisions, and free worker attributes.",
  ],
];

const TYPE_DOCS = [
  [
    "detection",
    "One observed object at one moment, with a position. ~1–2 per second per track powers the heatmap and occupancy.",
  ],
  [
    "zone_enter / zone_exit",
    "A track crossed a zone boundary. The platform pairs them to derive dwell time and flow — these are the raw substrate for every dwell metric.",
  ],
  [
    "zone_dwell",
    "Deprecated. Stored for backward compatibility, but its value is ignored — the platform derives dwell from enter/exit pairs instead.",
  ],
  [
    "state_change",
    "Equipment or scene flipped state (label = new state, e.g. \"open\"). Durations are derived from consecutive timestamps; worker-posted durations are ignored.",
  ],
  [
    "count",
    "A per-frame population sample (value = how many, label = of what). The platform averages samples per interval — never post cumulative totals.",
  ],
  ["transition / custom", "Free-form observations for special analyses."],
];

export function DetectionsPage({ liveTick = 0 }) {
  const [range, setRange] = useState(86400);
  const [filters, setFilters] = useState({
    event_type: "all",
    source_id: "all",
    zone_id: "all",
    job_id: "all",
    track_id: "",
    label: "",
  });
  const [context, setContext] = useState({ sources: [], zones: [], jobs: [] });
  const [events, setEvents] = useState([]),
    [total, setTotal] = useState(0),
    [nextCursor, setNextCursor] = useState(null),
    [loading, setLoading] = useState(true),
    [loadingMore, setLoadingMore] = useState(false),
    [error, setError] = useState(null);
  const [expanded, setExpanded] = useState(null),
    [follow, setFollow] = useState(true);

  useEffect(() => {
    Promise.all([api.get("/sources"), api.get("/zones"), api.get("/jobs")])
      .then(([sources, zones, jobs]) => setContext({ sources, zones, jobs }))
      .catch(() => {});
  }, []);

  // The window is computed fresh on every load so "follow live" sees new rows;
  // keyset cursors stay stable even when newer events arrive above them.
  const buildParams = () => {
    const params = new URLSearchParams({
      limit: "100",
      since: String(Date.now() / 1000 - range),
    });
    Object.entries(filters).forEach(([name, value]) => {
      if (value !== "all" && value !== "")
        params.set(name, typeof value === "string" ? value.trim() : value);
    });
    return params;
  };

  const load = async (quiet = false) => {
    if (!quiet) setLoading(true);
    setError(null);
    try {
      const result = await api.get(`/events?${buildParams().toString()}`);
      setEvents(result.events);
      setTotal(result.total);
      setNextCursor(result.next_cursor);
    } catch (err) {
      setError(err);
    } finally {
      setLoading(false);
    }
  };
  const loadMore = async () => {
    if (!nextCursor) return;
    setLoadingMore(true);
    try {
      const params = buildParams();
      params.set("cursor", nextCursor);
      const result = await api.get(`/events?${params.toString()}`);
      setEvents((current) => [...current, ...result.events]);
      setNextCursor(result.next_cursor);
    } catch (err) {
      setError(err);
    } finally {
      setLoadingMore(false);
    }
  };
  useEffect(() => {
    load();
  }, [filters, range]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (!follow || !liveTick) return;
    const timer = window.setTimeout(() => load(true), 500);
    return () => window.clearTimeout(timer);
  }, [liveTick]); // eslint-disable-line react-hooks/exhaustive-deps

  const change = (name, value) =>
    setFilters((current) => ({ ...current, [name]: value }));

  return (
    <>
      <PageHeader
        eyebrow="Raw observations"
        title="Every event workers posted"
        description="Enriched raw observations — the evidence behind every insight, not conclusions. The platform derives dwell, durations, and analytics from these rows."
        actions={<RangeSelect value={range} onChange={setRange} />}
      />
      <Panel
        title="Detections & observations"
        subtitle={`${events.length.toLocaleString()} of ${total.toLocaleString()} matching events loaded`}
        action={
          <div className="event-refresh">
            <label>
              <input
                type="checkbox"
                checked={follow}
                onChange={(event) => setFollow(event.target.checked)}
              />{" "}
              Follow live events
            </label>
            <button
              className="icon-button"
              onClick={() => load()}
              aria-label="Refresh events"
            >
              <RefreshCw size={15} />
            </button>
          </div>
        }
      >
        <div className="event-filters">
          <label className="field">
            <span>Event type</span>
            <select
              value={filters.event_type}
              onChange={(event) => change("event_type", event.target.value)}
            >
              {EVENT_TYPES.map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            <span>Camera</span>
            <select
              value={filters.source_id}
              onChange={(event) => change("source_id", event.target.value)}
            >
              <option value="all">All cameras</option>
              {context.sources.map((source) => (
                <option key={source.id} value={source.id}>
                  {source.name}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            <span>Zone</span>
            <select
              value={filters.zone_id}
              onChange={(event) => change("zone_id", event.target.value)}
            >
              <option value="all">All zones</option>
              {context.zones.map((zone) => (
                <option key={zone.id} value={zone.id}>
                  {zone.name}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            <span>Analysis job</span>
            <select
              value={filters.job_id}
              onChange={(event) => change("job_id", event.target.value)}
            >
              <option value="all">All jobs</option>
              {context.jobs.map((job) => (
                <option key={job.id} value={job.id}>
                  {job.name}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            <span>Track ID</span>
            <input
              value={filters.track_id}
              onChange={(event) => change("track_id", event.target.value)}
              placeholder="t42"
            />
          </label>
          <label className="field">
            <span>Label</span>
            <input
              value={filters.label}
              onChange={(event) => change("label", event.target.value)}
              placeholder="open"
            />
          </label>
        </div>
        {error && <ErrorState error={error} retry={() => load()} />}
        {loading ? (
          <LoadingState label="Loading observations…" />
        ) : !events.length ? (
          <EmptyState title="No matching events">
            Change the filters or time range, or run an analysis worker that
            submits event batches.
          </EmptyState>
        ) : (
          <>
            <div className="raw-event-table table-scroll">
              <table>
                <thead>
                  <tr>
                    <th>Time</th>
                    <th>Type</th>
                    <th>Source</th>
                    <th>Zone</th>
                    <th>Track / label</th>
                    <th>Value</th>
                    <th>Job</th>
                    <th>
                      <span className="sr-only">Details</span>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {events.map((event) => (
                    <EventRows
                      key={event.id}
                      event={event}
                      source={context.sources.find(
                        (source) => source.id === event.source_id,
                      )}
                      job={context.jobs.find((job) => job.id === event.job_id)}
                      expanded={expanded === event.id}
                      toggle={() =>
                        setExpanded(expanded === event.id ? null : event.id)
                      }
                    />
                  ))}
                </tbody>
              </table>
            </div>
            {nextCursor && (
              <div className="load-more-row">
                <button
                  className="button button-secondary"
                  onClick={loadMore}
                  disabled={loadingMore}
                >
                  {loadingMore
                    ? "Loading…"
                    : `Load ${Math.min(100, total - events.length)} more`}
                </button>
              </div>
            )}
          </>
        )}
        <p className="definition-note">
          Rows are raw worker outputs after server-side projection and zone
          assignment. Following live events resets pagination — pause it to page
          through history.
        </p>
      </Panel>
      <Panel
        title="How to read this table"
        subtitle="The observation contract between workers and the platform"
      >
        <details className="docs-toggle">
          <summary>Column glossary, event types, and enrichment pipeline</summary>
          <div className="docs-glossary">
            <h3>Enrichment pipeline</h3>
            <p>
              Workers post what their model saw. On ingestion the platform
              preserves the original <code>bbox</code>, keypoints, or mask, then
              enriches the row. A point may be projected through the floor or a
              named horizontal plane such as a mattress. Camera-specific zone
              views can assign a zone by point, bounding-box overlap, or
              keypoints before the resulting map point is tested against the
              global floor polygon. The row records which method and geometry
              revisions were used, so a later geometry edit never rewrites
              history. Analytics, alerts, and insights are derived from these
              enriched rows; workers never post computed aggregates.
            </p>
            <h3>Columns</h3>
            <div className="table-scroll">
              <table>
                <tbody>
                  {COLUMN_DOCS.map(([name, doc]) => (
                    <tr key={name}>
                      <th>{name}</th>
                      <td>{doc}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <h3>Event types</h3>
            <div className="table-scroll">
              <table>
                <tbody>
                  {TYPE_DOCS.map(([name, doc]) => (
                    <tr key={name}>
                      <th>{name}</th>
                      <td>{doc}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p>
              Observations in → insights out: register what these rows should
              answer in the <a href="#insights">Insights catalogue</a>, or see
              the full API contract in the{" "}
              <a href="/docs" target="_blank" rel="noreferrer">
                interactive API documentation
              </a>
              .
            </p>
          </div>
        </details>
      </Panel>
    </>
  );
}

function EventRows({ event, source, job, expanded, toggle }) {
  return (
    <>
      <tr>
        <td className="event-time-cell">
          <time
            dateTime={new Date(event.ts * 1000).toISOString()}
            title={`Epoch ts: ${event.ts}`}
          >
            {formatPreciseDateTime(event.ts)}
          </time>
        </td>
        <td>
          <Badge tone={event.event_type === "zone_dwell" ? "warning" : "violet"}>
            {event.event_type.replaceAll("_", " ")}
          </Badge>
        </td>
        <td>
          {source?.name || (event.source_id ? `#${event.source_id}` : "—")}
        </td>
        <td>{event.zone_name || "—"}</td>
        <td>{event.track_id || event.label || "—"}</td>
        <td>{event.value ?? "—"}</td>
        <td>{job?.name || (event.job_id ? `#${event.job_id}` : "—")}</td>
        <td>
          <button
            className="icon-button"
            onClick={toggle}
            aria-expanded={expanded}
            aria-label={`${expanded ? "Hide" : "Show"} event ${event.id} payload`}
          >
            <Activity size={14} />
          </button>
        </td>
      </tr>
      {expanded && (
        <tr className="event-payload-row">
          <td colSpan="8">
            <div>
              <span>
                Event #{event.id} · job {event.job_id || "unregistered"} ·
                observed {formatPreciseDateTime(event.ts)} · ingested{" "}
                {formatPreciseDateTime(event.created_at)}
              </span>
              <pre>
                {JSON.stringify(
                  {
                    timestamp: {
                      ts: event.ts,
                      iso: new Date(event.ts * 1000).toISOString(),
                    },
                    ingestion_timestamp: {
                      ts: event.created_at,
                      iso: new Date(event.created_at * 1000).toISOString(),
                    },
                    bbox: event.bbox,
                    keypoints: event.keypoints,
                    mask: event.mask,
                    point_px:
                      event.x_px == null
                        ? null
                        : { x: event.x_px, y: event.y_px },
                    point_map:
                      event.x_map == null
                        ? null
                        : { x: event.x_map, y: event.y_map },
                    point_kind: event.point_kind,
                    projection: {
                      method: event.projection_method,
                      surface_id: event.projection_surface_id,
                      calibration_revision: event.calibration_revision,
                      surface_revision: event.surface_revision,
                    },
                    zone_assignment: {
                      method: event.zone_assignment_method,
                      zone_view_id: event.zone_view_id,
                      zone_revision: event.zone_revision,
                      zone_view_revision: event.zone_view_revision,
                    },
                    attributes: event.attributes,
                  },
                  null,
                  2,
                )}
              </pre>
            </div>
          </td>
        </tr>
      )}
    </>
  );
}
