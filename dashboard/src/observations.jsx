import { useEffect, useRef, useState } from "react";
import { Activity, RefreshCw } from "lucide-react";
import { api, formatPreciseDateTime } from "./api.js";
import { Badge, RangeSelect } from "./components.jsx";
import { CURRENT_KINDS, LEGACY_KINDS, OTHER_KINDS, isLegacyKind, kindLabel } from "./status.js";
import { EmptyState, ErrorState, LoadingState, PageHeader, Panel, TechnicalDetails } from "./ui.jsx";

/* Retired kinds are still stored and still readable, but the ingestion path
 * rejects them, so they are not offered until someone asks for them. */
const KIND_GROUPS = [
  ["all", "All kinds"],
  ...CURRENT_KINDS.map((kind) => [kind, kindLabel(kind)]),
  ...OTHER_KINDS.map((kind) => [kind, kindLabel(kind)]),
];
const LEGACY_KIND_OPTIONS = LEGACY_KINDS.map((kind) => [kind, `${kindLabel(kind)} (retired)`]);


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
    [follow, setFollow] = useState(true),
    [showLegacy, setShowLegacy] = useState(false);
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
        title="Observations"
        actions={
          <>
            <RangeSelect value={range} onChange={setRange} />
            <button className="icon-button" onClick={() => load()} aria-label="Refresh observations">
              <RefreshCw size={16} />
            </button>
          </>
        }
      />
      <Panel
        subtitle={`${observations.length.toLocaleString()} of ${total.toLocaleString()} rows`}
        action={
          <label className="check-field">
            <input type="checkbox" checked={follow} onChange={(e) => setFollow(e.target.checked)} />
            Follow new rows
          </label>
        }
      >
        <div className="event-filters">
          <label className="field">
            <span>Kind</span>
            <select
              value={filters.kind}
              onChange={(e) => {
                if (e.target.value === "__legacy__") { setShowLegacy(true); return; }
                change("kind", e.target.value);
              }}
            >
              {KIND_GROUPS.map(([value, label]) => (
                <option key={value} value={value}>{label}</option>
              ))}
              {showLegacy
                ? LEGACY_KIND_OPTIONS.map(([value, label]) => (
                  <option key={value} value={value}>{label}</option>))
                : <option value="__legacy__">Show retired kinds…</option>}
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
            <span>Worker</span>
            <select value={filters.job_id} onChange={(e) => change("job_id", e.target.value)}>
              <option value="all">All workers</option>
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
          <EmptyState tone="no-data" title="No observations match these filters" />
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
        {follow && nextCursor && (
          <p className="table-note">Turn off “Follow new rows” to page back through history.</p>
        )}
      </Panel>
    </>
  );
}

function ObservationRows({ observation: o, source, job, expanded, toggle }) {
  const isLegacy = isLegacyKind(o.kind);
  return (
    <>
      <tr>
        <td className="event-time-cell">
          <time dateTime={new Date(o.ts * 1000).toISOString()} title={`Epoch ts: ${o.ts}`}>
            {formatPreciseDateTime(o.ts)}
          </time>
        </td>
        <td><Badge tone={isLegacy ? "warning" : "violet"}>{kindLabel(o.kind)}</Badge></td>
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
