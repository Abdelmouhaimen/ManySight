import { useEffect, useRef, useState } from "react";
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

const KINDS = [
  ["all", "all kinds"],
  ["detection", "detection"],
  ["measurement", "measurement"],
  ["state", "state"],
  ["zone_enter", "zone enter (legacy)"],
  ["zone_exit", "zone exit (legacy)"],
  ["zone_dwell", "zone dwell (deprecated)"],
  ["state_change", "state change (legacy)"],
  ["count", "count (legacy)"],
  ["transition", "transition"],
  ["custom", "custom"],
];

const COLUMN_DOCS = [
  ["Time", "When the worker observed it (`ts`), displayed through milliseconds. Expand the row for the exact epoch value."],
  ["Kind", "detection | measurement | state, or a legacy kind kept for historical audit — see the glossary below."],
  ["Source", "The camera or sensor the observation came from (`source_id`)."],
  ["Zone", "Assigned by the platform from geometry when the observation carries spatial evidence — never sent by the worker."],
  ["Entity / name", "`entity_id` (opaque per-track id) for detections, or `name` (the metric/state key) for measurements and states."],
  ["Value / label", "The numeric sample for a measurement, or the categorical value for a state/detection label."],
  ["Job", "The registered analysis job that posted the observation."],
  ["Details (expand)", "The complete observation evidence: pixel/map point, bbox, keypoints or mask, point meaning, projection plane, zone-assignment method, geometry revisions, and worker attributes."],
];

const TYPE_DOCS = [
  ["detection", "One observed entity at one moment, with spatial evidence (point/bbox/keypoints/mask). ~1–2 per second per entity powers the heatmap, presence, visits, and dwell — all derived by the platform, never sent by the worker."],
  ["measurement", "A directly observed numeric sample (name, value, value_kind: gauge/delta/cumulative). Never post a time-aggregated or precomputed total."],
  ["state", "A directly observed current categorical value (name, label). Send it on every sample, including repeats — the platform coalesces repeats into intervals and derives transitions/durations itself."],
  ["zone_enter / zone_exit / zone_dwell / state_change / count", "Legacy, platform-derived kinds from the previous contract. Still stored for historical audit; POST /observations/batch rejects a client that tries to send these now (error: legacy_derived_observation)."],
  ["transition / custom", "Free-form observations for special analyses."],
];

export function ObservationsPage({ liveTick = 0 }) {
  const [range, setRange] = useState(86400);
  const [filters, setFilters] = useState({
    kind: "all", source_id: "all", zone_id: "all", job_id: "all", entity_id: "", label: "",
  });
  const [context, setContext] = useState({ sources: [], zones: [], jobs: [] });
  const [observations, setObservations] = useState([]),
    [total, setTotal] = useState(0),
    [nextCursor, setNextCursor] = useState(null),
    [loading, setLoading] = useState(true),
    [loadingMore, setLoadingMore] = useState(false),
    [error, setError] = useState(null);
  const [expanded, setExpanded] = useState(null),
    [follow, setFollow] = useState(true);
  const requestIdRef = useRef(0);
  const windowSinceRef = useRef(Date.now() / 1000 - range);

  useEffect(() => {
    Promise.all([api.get("/sources"), api.get("/zones"), api.get("/jobs")])
      .then(([sources, zones, jobs]) => setContext({ sources, zones, jobs }))
      .catch(() => {});
  }, []);

  const buildParams = (since) => {
    const params = new URLSearchParams({ limit: "100", since: String(since) });
    Object.entries(filters).forEach(([name, value]) => {
      if (value !== "all" && value !== "")
        params.set(name, typeof value === "string" ? value.trim() : value);
    });
    return params;
  };

  const load = async (quiet = false) => {
    const requestId = ++requestIdRef.current;
    const since = Date.now() / 1000 - range;
    if (!quiet) setLoading(true);
    setError(null);
    try {
      const result = await api.get(`/observations?${buildParams(since).toString()}`);
      if (requestId !== requestIdRef.current) return;
      windowSinceRef.current = since;
      setObservations(result.observations);
      setTotal(result.total);
      setNextCursor(result.next_cursor);
    } catch (err) {
      if (requestId === requestIdRef.current) setError(err);
    } finally {
      if (requestId === requestIdRef.current) setLoading(false);
    }
  };
  const loadMore = async () => {
    if (!nextCursor) return;
    const requestId = requestIdRef.current;
    setLoadingMore(true);
    try {
      const params = buildParams(windowSinceRef.current);
      params.set("cursor", nextCursor);
      const result = await api.get(`/observations?${params.toString()}`);
      if (requestId !== requestIdRef.current) return;
      setObservations((current) => [...current, ...result.observations]);
      setNextCursor(result.next_cursor);
    } catch (err) {
      if (requestId === requestIdRef.current) setError(err);
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

  const change = (name, value) => setFilters((current) => ({ ...current, [name]: value }));

  return (
    <>
      <PageHeader
        eyebrow="Raw observations"
        title="Every observation workers submitted"
        description="Detection, measurement, and state rows — the evidence behind every analysis, not conclusions. The platform derives zones, visits, dwell, transitions, and state intervals from these rows."
        actions={<RangeSelect value={range} onChange={setRange} />}
      />
      <Panel
        title="Observations"
        subtitle={`${observations.length.toLocaleString()} of ${total.toLocaleString()} matching rows loaded`}
        action={
          <div className="event-refresh">
            <label>
              <input type="checkbox" checked={follow} onChange={(e) => setFollow(e.target.checked)} />{" "}
              Follow live observations
            </label>
            <button className="icon-button" onClick={() => load()} aria-label="Refresh observations">
              <RefreshCw size={15} />
            </button>
          </div>
        }
      >
        <div className="event-filters">
          <label className="field">
            <span>Kind</span>
            <select value={filters.kind} onChange={(e) => change("kind", e.target.value)}>
              {KINDS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
            </select>
          </label>
          <label className="field">
            <span>Source</span>
            <select value={filters.source_id} onChange={(e) => change("source_id", e.target.value)}>
              <option value="all">All sources</option>
              {context.sources.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
            </select>
          </label>
          <label className="field">
            <span>Zone</span>
            <select value={filters.zone_id} onChange={(e) => change("zone_id", e.target.value)}>
              <option value="all">All zones</option>
              {context.zones.map((z) => <option key={z.id} value={z.id}>{z.name}</option>)}
            </select>
          </label>
          <label className="field">
            <span>Analysis job</span>
            <select value={filters.job_id} onChange={(e) => change("job_id", e.target.value)}>
              <option value="all">All jobs</option>
              {context.jobs.map((j) => <option key={j.id} value={j.id}>{j.name}</option>)}
            </select>
          </label>
          <label className="field">
            <span>Entity ID</span>
            <input value={filters.entity_id} onChange={(e) => change("entity_id", e.target.value)} placeholder="t42" />
          </label>
          <label className="field">
            <span>Label</span>
            <input value={filters.label} onChange={(e) => change("label", e.target.value)} placeholder="open" />
          </label>
        </div>
        {error && <ErrorState error={error} retry={() => load()} />}
        {loading ? (
          <LoadingState label="Loading observations…" />
        ) : !observations.length ? (
          <EmptyState title="No matching observations">
            Change the filters or time range, or run a worker that submits observation batches.
          </EmptyState>
        ) : (
          <>
            <div className="raw-event-table table-scroll">
              <table>
                <thead>
                  <tr>
                    <th>Time</th><th>Kind</th><th>Source</th><th>Zone</th>
                    <th>Entity / name</th><th>Value / label</th><th>Job</th>
                    <th><span className="sr-only">Details</span></th>
                  </tr>
                </thead>
                <tbody>
                  {observations.map((observation) => (
                    <ObservationRows
                      key={observation.id}
                      observation={observation}
                      source={context.sources.find((s) => s.id === observation.source_id)}
                      job={context.jobs.find((j) => j.id === observation.job_id)}
                      expanded={expanded === observation.id}
                      toggle={() => setExpanded(expanded === observation.id ? null : observation.id)}
                    />
                  ))}
                </tbody>
              </table>
            </div>
            {nextCursor && (
              <div className="load-more-row">
                <button className="button button-secondary" onClick={loadMore} disabled={loadingMore}>
                  {loadingMore ? "Loading…" : `Load ${Math.min(100, total - observations.length)} more`}
                </button>
              </div>
            )}
          </>
        )}
        <p className="definition-note">
          Rows are raw worker submissions after server-side projection and zone assignment.
          Following live observations resets pagination — pause it to page through history.
        </p>
      </Panel>
      <Panel title="How to read this table" subtitle="The observation contract between workers and the platform">
        <details className="docs-toggle">
          <summary>Column glossary, kinds, and enrichment pipeline</summary>
          <div className="docs-glossary">
            <h3>Enrichment pipeline</h3>
            <p>
              Workers submit only detection, measurement, or state observations —
              never a zone ID, a zone name, or a derived event. On ingestion the
              platform preserves the original evidence (bbox, keypoints, or mask),
              then projects a representative point through the floor or a named
              plane, matches it against camera-specific zone views, and tests it
              against the global zone polygons. The row records which method and
              geometry revisions were used, so a later geometry edit never rewrites
              history. Every analysis is derived from these rows.
            </p>
            <h3>Columns</h3>
            <div className="table-scroll">
              <table><tbody>
                {COLUMN_DOCS.map(([name, doc]) => <tr key={name}><th>{name}</th><td>{doc}</td></tr>)}
              </tbody></table>
            </div>
            <h3>Kinds</h3>
            <div className="table-scroll">
              <table><tbody>
                {TYPE_DOCS.map(([name, doc]) => <tr key={name}><th>{name}</th><td>{doc}</td></tr>)}
              </tbody></table>
            </div>
            <p>
              Observations in → queries and dashboards out. Generated dashboards are shown on the{" "}
              <a href="#dashboard">Dashboard page</a>; see the full contract in the{" "}
              <a href="/docs" target="_blank" rel="noreferrer">interactive API documentation</a>.
            </p>
          </div>
        </details>
      </Panel>
    </>
  );
}

function ObservationRows({ observation: o, source, job, expanded, toggle }) {
  const isLegacy = !["detection", "measurement", "state"].includes(o.kind);
  return (
    <>
      <tr>
        <td className="event-time-cell">
          <time dateTime={new Date(o.ts * 1000).toISOString()} title={`Epoch ts: ${o.ts}`}>
            {formatPreciseDateTime(o.ts)}
          </time>
        </td>
        <td><Badge tone={isLegacy ? "warning" : "violet"}>{o.kind.replaceAll("_", " ")}</Badge></td>
        <td>{source?.name || (o.source_id ? `#${o.source_id}` : "—")}</td>
        <td>{o.zone_name || "—"}</td>
        <td>{o.entity_id || o.name || "—"}</td>
        <td>{o.value ?? o.label ?? "—"}</td>
        <td>{job?.name || (o.job_id ? `#${o.job_id}` : "—")}</td>
        <td>
          <button className="icon-button" onClick={toggle} aria-expanded={expanded}
                 aria-label={`${expanded ? "Hide" : "Show"} observation ${o.id} payload`}>
            <Activity size={14} />
          </button>
        </td>
      </tr>
      {expanded && (
        <tr className="event-payload-row">
          <td colSpan="8">
            <div>
              <span>
                Observation #{o.id} · job {o.job_id || "unregistered"} · observed {formatPreciseDateTime(o.ts)} ·
                ingested {formatPreciseDateTime(o.created_at)}
              </span>
              <pre>{JSON.stringify(o, null, 2)}</pre>
            </div>
          </td>
        </tr>
      )}
    </>
  );
}
