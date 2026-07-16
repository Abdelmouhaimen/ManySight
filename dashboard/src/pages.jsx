import { useEffect, useState } from "react";
import {
  Activity,
  AlertTriangle,
  ArrowRight,
  BellRing,
  CheckCircle2,
  ChevronRight,
  CircleDot,
  Code2,
  Eye,
  Gauge,
  Map,
  Pause,
  Play,
  Plus,
  RefreshCw,
  Save,
  Settings2,
  Trash2,
  UsersRound,
  Wrench,
} from "lucide-react";
import { api, assetUrl, formatDateTime, formatDuration } from "./api.js";
import {
  ActivityMap,
  Badge,
  BarChart,
  EmptyState,
  ErrorState,
  LineChart,
  LoadingState,
  MetricCard,
  Modal,
  PageHeader,
  Panel,
  SignalRow,
} from "./components.jsx";
import { SpaceWorkbench } from "./space-workbench.jsx";
import { ConnectionModal, TechnicalConfig } from "./technical-config.jsx";

const RANGE_OPTIONS = [
  ["1 hour", 3600],
  ["6 hours", 21600],
  ["24 hours", 86400],
  ["7 days", 604800],
  ["30 days", 2592000],
];

function RangeSelect({ value, onChange }) {
  return (
    <label className="select-control">
      <span className="sr-only">Time range</span>
      <select
        value={value}
        onChange={(event) => onChange(Number(event.target.value))}
      >
        {RANGE_OPTIONS.map(([label, seconds]) => (
          <option key={seconds} value={seconds}>
            Last {label}
          </option>
        ))}
      </select>
    </label>
  );
}

function useDashboardData(range, liveTick = 0) {
  const [state, setState] = useState({
    loading: true,
    data: null,
    error: null,
  });
  const refresh = async () => {
    setState((current) => ({
      ...current,
      loading: !current.data,
      error: null,
    }));
    const until = Date.now() / 1000,
      since = until - range,
      query = `since=${since}&until=${until}`;
    try {
      const [
        summary,
        store,
        zones,
        sources,
        heat,
        dwell,
        occupancy,
        counts,
        transitions,
        states,
        alerts,
        jobs,
      ] = await Promise.all([
        api.get(`/analytics/summary?${query}`),
        api.get("/store"),
        api.get("/zones"),
        api.get("/sources"),
        api.get(`/analytics/heatmap?${query}`),
        api.get(`/analytics/dwell?${query}`),
        api.get(`/analytics/occupancy?${query}`),
        api.get(`/analytics/counts?${query}`),
        api.get(`/analytics/transitions?${query}`),
        api.get(`/analytics/states?${query}`),
        api.get("/alerts?limit=60"),
        api.get("/jobs"),
      ]);
      setState({
        loading: false,
        data: {
          summary,
          store,
          zones,
          sources,
          heat,
          dwell,
          occupancy,
          counts,
          transitions,
          states,
          alerts,
          jobs,
          since,
          until,
        },
        error: null,
      });
    } catch (error) {
      setState((current) => ({ ...current, loading: false, error }));
    }
  };
  useEffect(() => {
    refresh();
  }, [range]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (!liveTick) return;
    const timer = window.setTimeout(refresh, 500);
    return () => window.clearTimeout(timer);
  }, [liveTick]); // eslint-disable-line react-hooks/exhaustive-deps
  return { ...state, refresh };
}

export function OverviewPage({ liveTick = 0, openSignal }) {
  const [range, setRange] = useState(86400);
  const remote = useDashboardData(range, liveTick);
  if (remote.loading && !remote.data) return <LoadingState />;
  if (remote.error && !remote.data)
    return <ErrorState error={remote.error} retry={remote.refresh} />;
  const d = remote.data;
  const queueZoneIds = new Set(
    d.zones
      .filter((zone) => ["checkout", "queue"].includes(zone.ztype))
      .map((zone) => zone.id),
  );
  const queueRows = d.dwell.rows.filter((row) => queueZoneIds.has(row.zone_id));
  const queueVisits = queueRows.reduce((sum, row) => sum + row.visits, 0);
  const avgWait = queueVisits
    ? queueRows.reduce((sum, row) => sum + row.avg_s * row.visits, 0) /
      queueVisits
    : null;
  const peakOccupancy = Math.max(
    ...d.occupancy.series.map((point) => point.count),
    0,
  );
  const newSignals = d.alerts.filter(
    (alert) =>
      (alert.status || (alert.acknowledged ? "resolved" : "new")) === "new",
  );
  return (
    <>
      <PageHeader
        eyebrow="Workspace overview"
        title={new Date().toLocaleDateString([], {
          weekday: "long",
          day: "numeric",
          month: "long",
        })}
        description="Operational signals from the selected period. POC metrics remain explicitly defined."
        actions={
          <>
            <RangeSelect value={range} onChange={setRange} />
            <button
              className="icon-button"
              onClick={remote.refresh}
              aria-label="Refresh"
            >
              <RefreshCw size={16} />
            </button>
          </>
        }
      />
      {remote.error && (
        <div className="inline-warning">
          <AlertTriangle size={16} /> Showing the latest available data. Refresh
          failed: {remote.error.message}
        </div>
      )}
      <div className="metric-grid">
        <MetricCard
          primary
          label="Tracked visits"
          value={d.summary.tracks.toLocaleString()}
          note="Distinct anonymous track IDs"
        />
        <MetricCard
          label="Average queue presence"
          value={avgWait == null ? "—" : formatDuration(avgWait)}
          note={
            avgWait == null
              ? "Define a checkout or queue zone"
              : `${queueVisits} observed zone visits`
          }
        />
        <MetricCard
          label="Peak occupancy"
          value={peakOccupancy.toLocaleString()}
          note="Highest tracked bucket"
        />
        <MetricCard
          tone={newSignals.length ? "warning" : ""}
          label="To review"
          value={newSignals.length.toString().padStart(2, "0")}
          note="New human-review signals"
        />
      </div>
      <div className="overview-grid">
        <Panel
          className="traffic-panel"
          title="Visitor traffic"
          subtitle="Distinct tracked people by time bucket"
          action={<Badge tone="violet">POC definition</Badge>}
        >
          <LineChart
            points={d.occupancy.series}
            unit=" people"
            empty="Tracking events with stable IDs will populate this chart."
          />
        </Panel>
        <Panel
          className="map-panel"
          title="Activity map"
          subtitle="Position density in the selected period"
          action={
            <a
              className="round-link"
              href="#insights"
              aria-label="Open insight details"
            >
              <ArrowRight size={16} />
            </a>
          }
        >
          <ActivityMap
            compact
            store={d.store}
            zones={d.zones}
            sources={d.sources}
            points={d.heat.points}
          />
        </Panel>
      </div>
      <Panel
        title="Recent signals"
        subtitle="Model outputs remain reviewable and traceable"
        action={
          <a className="text-link" href="#events">
            View all <ArrowRight size={14} />
          </a>
        }
      >
        <div className="signal-list">
          {d.alerts.slice(0, 5).map((signal) => (
            <SignalRow
              key={signal.id}
              signal={signal}
              onClick={() => openSignal?.(signal)}
            />
          ))}
          {!d.alerts.length && (
            <EmptyState title="Nothing needs review">
              New threshold or event signals will appear here.
            </EmptyState>
          )}
        </div>
      </Panel>
      <p className="definition-note">
        * Tracked visits are distinct worker track IDs in the selected period.
        They are not yet a validated entry-count metric.
      </p>
    </>
  );
}

export function InsightsPage({ liveTick = 0 }) {
  const [range, setRange] = useState(86400);
  const [tab, setTab] = useState("traffic");
  const remote = useDashboardData(range, liveTick);
  if (remote.loading && !remote.data)
    return <LoadingState label="Loading insights…" />;
  if (remote.error && !remote.data)
    return <ErrorState error={remote.error} retry={remote.refresh} />;
  const d = remote.data;
  const queueZoneIds = new Set(
    d.zones
      .filter((zone) => ["checkout", "queue"].includes(zone.ztype))
      .map((zone) => zone.id),
  );
  const queueRows = d.dwell.rows
    .filter((row) => queueZoneIds.has(row.zone_id))
    .map((row) => ({
      label: row.zone_name,
      value: row.avg_s,
      detail: `${row.visits} visits`,
    }));
  const dwellRows = d.dwell.rows.map((row) => ({
    label: row.zone_name,
    value: row.avg_s,
    detail: `${row.visits} visits`,
  }));
  const countSeries = d.counts.series[0];
  return (
    <>
      <PageHeader
        eyebrow="Insights"
        title="Understand the operating pattern"
        description="Focused views for traffic, queues, anonymous flow, dwell, and spatial activity."
        actions={<RangeSelect value={range} onChange={setRange} />}
      />
      <div
        className="feature-tabs"
        role="tablist"
        aria-label="Insight categories"
      >
        {[
          ["traffic", "Traffic & occupancy", UsersRound],
          ["queue", "Queue intelligence", Gauge],
          ["flow", "Flow & dwell", Activity],
          ["map", "Activity map", Map],
          ["experimental", "Custom analyses", Wrench],
        ].map(([value, label, Icon]) => (
          <button
            key={value}
            className={tab === value ? "active" : ""}
            onClick={() => setTab(value)}
            role="tab"
            aria-selected={tab === value}
          >
            <Icon size={16} />
            {label}
          </button>
        ))}
      </div>
      {tab === "traffic" && (
        <div className="insight-layout">
          <Panel
            title="Occupancy over time"
            subtitle="Distinct tracks observed in each bucket"
          >
            <LineChart points={d.occupancy.series} unit=" people" />
          </Panel>
          <Panel
            title="Traffic definition"
            subtitle="What this POC currently measures"
          >
            <div className="definition-card">
              <CircleDot />
              <h3>Anonymous track activity</h3>
              <p>
                The current curve counts distinct non-null track IDs with
                assigned zones. It is useful for demonstrating trends, but it is
                not yet a validated entry/exit counter.
              </p>
              <Badge tone="warning">Needs pilot validation</Badge>
            </div>
          </Panel>
        </div>
      )}
      {tab === "queue" && (
        <div className="insight-layout">
          <Panel
            title="Queue-zone presence"
            subtitle="Average dwell in checkout or queue zones"
          >
            <BarChart
              rows={queueRows}
              unit=" sec"
              empty="Create a checkout or queue zone, then post zone_dwell events."
            />
          </Panel>
          <Panel
            title="Operational interpretation"
            subtitle="A signal, not an automatic staffing decision"
          >
            <div className="definition-card">
              <Gauge />
              <h3>Pressure indicator</h3>
              <p>
                People inside a queue polygon may not all be queuing. Calibrate
                the zone and validate against peak and off-peak samples before
                using a threshold.
              </p>
              <a className="text-link" href="#configure">
                Review zone setup <ArrowRight size={14} />
              </a>
            </div>
          </Panel>
        </div>
      )}
      {tab === "flow" && (
        <div className="stack">
          <Panel
            title="Dwell by zone"
            subtitle="Average seconds per observed visit"
          >
            <BarChart
              rows={dwellRows}
              unit=" sec"
              empty="Post zone_dwell events or paired zone_enter and zone_exit events."
            />
          </Panel>
          <Panel
            title="Zone-to-zone flow"
            subtitle="Anonymous transitions for stable track IDs"
          >
            <FlowTable data={d.transitions} />
          </Panel>
        </div>
      )}
      {tab === "map" && (
        <Panel
          title="Activity map"
          subtitle={`${d.heat.points.length.toLocaleString()} populated cells · calibrated detections`}
        >
          <ActivityMap
            store={d.store}
            zones={d.zones}
            sources={d.sources}
            points={d.heat.points}
          />
          {!d.heat.points.length && (
            <EmptyState title="No positioned detections">
              Calibrate a camera and post detection events with pixel or map
              points.
            </EmptyState>
          )}
        </Panel>
      )}
      {tab === "experimental" && (
        <div className="stack">
          <div className="experimental-note">
            <Wrench size={17} />
            <div>
              <strong>Agent-defined classifier views</strong>
              <p>
                A worker can post labelled count events for an approved
                question, such as the number of children in a main hall over
                time. The classifier and metric definition must be validated for
                that space.
              </p>
            </div>
          </div>
          <Panel
            title={
              countSeries
                ? `${countSeries.label} over time`
                : "Classifier counts"
            }
            subtitle="Generic labelled count events"
          >
            <LineChart
              points={countSeries?.points || []}
              unit={countSeries ? ` ${countSeries.label}` : ""}
              empty="Post labelled count events to populate this view."
            />
          </Panel>
          <Panel
            title="State monitoring"
            subtitle="Equipment or scene state durations"
          >
            <StateSummary series={d.states.series} />
          </Panel>
        </div>
      )}
    </>
  );
}

function FlowTable({ data }) {
  if (!data.links.length)
    return (
      <EmptyState title="No transitions yet">
        Stable track IDs need zone-enter sequences or zoned detections.
      </EmptyState>
    );
  const names = [
    ...new Set(
      data.links
        .flatMap((link) => [link.from_name, link.to_name])
        .filter(Boolean),
    ),
  ];
  const max = Math.max(...data.links.map((link) => link.count), 1);
  return (
    <div className="table-scroll">
      <table className="matrix-table">
        <thead>
          <tr>
            <th>From / to</th>
            {names.map((name) => (
              <th key={name}>{name}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {names.map((from) => (
            <tr key={from}>
              <th>{from}</th>
              {names.map((to) => {
                const value =
                  data.links.find(
                    (link) => link.from_name === from && link.to_name === to,
                  )?.count || 0;
                return (
                  <td
                    key={to}
                    style={{
                      backgroundColor: value
                        ? `rgba(112,89,255,${0.08 + (0.5 * value) / max})`
                        : undefined,
                    }}
                  >
                    {value || "—"}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function StateSummary({ series }) {
  if (!series.length)
    return (
      <EmptyState title="No state history">
        State-change events will create equipment or scene timelines.
      </EmptyState>
    );
  return (
    <div className="state-list">
      {series.map((item) => (
        <div key={item.source_id}>
          <strong>{item.source_name}</strong>
          <div>
            {Object.entries(item.totals).map(([label, seconds]) => (
              <Badge
                key={label}
                tone={
                  label === "open" || label === "on" ? "warning" : "neutral"
                }
              >
                {label} · {formatDuration(seconds)}
              </Badge>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}

export function EventsPage({ liveTick = 0, initialSignal, clearInitial }) {
  const [alerts, setAlerts] = useState([]),
    [loading, setLoading] = useState(true),
    [error, setError] = useState(null);
  const [filter, setFilter] = useState("open"),
    [selected, setSelected] = useState(initialSignal || null);
  const load = async () => {
    setLoading(true);
    try {
      setAlerts(await api.get("/alerts?limit=200"));
      setError(null);
    } catch (err) {
      setError(err);
    } finally {
      setLoading(false);
    }
  };
  useEffect(() => {
    const timer = window.setTimeout(load, liveTick ? 500 : 0);
    return () => window.clearTimeout(timer);
  }, [liveTick]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (initialSignal) setSelected(initialSignal);
  }, [initialSignal]);
  const visible = alerts.filter(
    (alert) =>
      filter === "all" ||
      (filter === "open"
        ? ["new", "in_review"].includes(alert.status || "new")
        : alert.status === filter),
  );
  const update = async (status, note) => {
    const saved = await api.put(`/alerts/${selected.id}`, { status, note });
    setAlerts((items) =>
      items.map((item) => (item.id === saved.id ? saved : item)),
    );
    setSelected(saved);
  };
  return (
    <>
      <PageHeader
        eyebrow="Reviewable events"
        title="One queue for what needs attention"
        description="Signals support human review. They are not automatic conclusions or accusations."
        actions={
          <button className="button button-secondary" onClick={load}>
            <RefreshCw size={15} />
            Refresh
          </button>
        }
      />
      <div className="filter-row">
        {[
          ["open", "Open"],
          ["new", "New"],
          ["in_review", "In review"],
          ["resolved", "Resolved"],
          ["dismissed", "Dismissed"],
          ["all", "All"],
        ].map(([value, label]) => (
          <button
            key={value}
            className={filter === value ? "active" : ""}
            onClick={() => setFilter(value)}
          >
            {label}
            <span>
              {
                alerts.filter(
                  (a) =>
                    value === "all" ||
                    (value === "open"
                      ? ["new", "in_review"].includes(a.status || "new")
                      : a.status === value),
                ).length
              }
            </span>
          </button>
        ))}
      </div>
      {loading ? (
        <LoadingState label="Loading review queue…" />
      ) : error ? (
        <ErrorState error={error} retry={load} />
      ) : (
        <Panel
          title="Signals"
          subtitle={`${visible.length} items in this view`}
        >
          <div className="signal-list signal-list-large">
            {visible.map((signal) => (
              <SignalRow
                key={signal.id}
                signal={signal}
                onClick={() => setSelected(signal)}
              />
            ))}
            {!visible.length && (
              <EmptyState title="This queue is clear">
                No signals match the selected review state.
              </EmptyState>
            )}
          </div>
        </Panel>
      )}
      {selected && (
        <SignalDrawer
          signal={selected}
          onClose={() => {
            setSelected(null);
            clearInitial?.();
          }}
          onSave={update}
        />
      )}
    </>
  );
}

function SignalDrawer({ signal, onClose, onSave }) {
  const [status, setStatus] = useState(signal.status || "new"),
    [note, setNote] = useState(signal.note || ""),
    [saving, setSaving] = useState(false),
    [error, setError] = useState("");
  const save = async () => {
    setSaving(true);
    setError("");
    try {
      await onSave(status, note);
    } catch (err) {
      setError(err.message);
    } finally {
      setSaving(false);
    }
  };
  return (
    <div
      className="drawer-backdrop"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <aside
        className="drawer"
        role="dialog"
        aria-modal="true"
        aria-label={`Review signal: ${signal.title}`}
      >
        <header>
          <div>
            <span className="tiny-label">Signal review</span>
            <h2>{signal.title}</h2>
          </div>
          <button
            className="icon-button"
            onClick={onClose}
            aria-label="Close signal review"
          >
            ×
          </button>
        </header>
        <div className="drawer-body">
          <Badge
            tone={
              status === "new"
                ? "warning"
                : status === "resolved"
                  ? "positive"
                  : "violet"
            }
          >
            {status.replace("_", " ")}
          </Badge>
          <p className="drawer-message">{signal.message}</p>
          <div className="why-signal">
            <strong>Why this appeared</strong>
            <p>
              {signal.rule_name
                ? `A worker-submitted event matched the “${signal.rule_name}” rule. Review the payload and source context before deciding what happened.`
                : "This signal was submitted without a linked rule. Review its technical payload and upstream job before acting."}
            </p>
          </div>
          <dl>
            <div>
              <dt>Occurred</dt>
              <dd>{formatDateTime(signal.ts)}</dd>
            </div>
            <div>
              <dt>Rule</dt>
              <dd>{signal.rule_name || "Unlinked signal"}</dd>
            </div>
            <div>
              <dt>Signal ID</dt>
              <dd>#{signal.id}</dd>
            </div>
          </dl>
          <label className="field">
            <span>Review status</span>
            <select
              value={status}
              onChange={(event) => setStatus(event.target.value)}
            >
              <option value="new">New</option>
              <option value="in_review">In review</option>
              <option value="resolved">Resolved</option>
              <option value="dismissed">Dismissed</option>
            </select>
          </label>
          <label className="field">
            <span>Review note</span>
            <textarea
              rows="5"
              value={note}
              onChange={(event) => setNote(event.target.value)}
              placeholder="Record what was checked and any action taken."
            />
          </label>
          <details>
            <summary>Technical payload</summary>
            <pre>{JSON.stringify(signal.payload || {}, null, 2)}</pre>
          </details>
          {error && (
            <div className="form-error" role="alert">
              {error}
            </div>
          )}
        </div>
        <footer>
          <button className="button button-secondary" onClick={onClose}>
            Cancel
          </button>
          <button
            className="button button-dark"
            disabled={saving}
            onClick={save}
          >
            <Save size={15} />
            {saving ? "Saving…" : "Save review"}
          </button>
        </footer>
      </aside>
    </div>
  );
}

export function StreamsPage({ notify }) {
  const [sources, setSources] = useState([]),
    [loading, setLoading] = useState(true),
    [error, setError] = useState(null),
    [filter, setFilter] = useState("all"),
    [editing, setEditing] = useState(null),
    [connection, setConnection] = useState(null);
  const load = async () => {
    setLoading(true);
    try {
      setSources(await api.get("/sources"));
      setError(null);
    } catch (err) {
      setError(err);
    } finally {
      setLoading(false);
    }
  };
  useEffect(() => {
    load();
  }, []);
  const test = async (source) => {
    try {
      const result = await api.post(`/sources/${source.id}/snapshot`);
      notify(
        result.status === "online" ? "Frame captured" : "Camera unavailable",
        result.status === "online"
          ? source.name
          : "Check the stream URL and network.",
        result.status === "online" ? "success" : "error",
      );
      await load();
    } catch (err) {
      notify("Connection test failed", err.message, "error");
    }
  };
  const remove = async (source) => {
    if (!window.confirm(`Delete ${source.name}? Stored events remain.`)) return;
    await api.del(`/sources/${source.id}`);
    notify("Camera removed", source.name);
    load();
  };
  const visible = sources.filter(
    (source) => filter === "all" || source.status === filter,
  );
  return (
    <>
      <PageHeader
        eyebrow="Streams"
        title="Camera coverage and health"
        description="The views required for the selected workflow, with credentials hidden by default."
        actions={
          <button className="button button-dark" onClick={() => setEditing({})}>
            <Plus size={16} />
            Add stream
          </button>
        }
      />
      <div className="health-summary">
        <div>
          <strong>
            {sources.filter((source) => source.status === "online").length}
          </strong>
          <span>online</span>
        </div>
        <div>
          <strong>
            {sources.filter((source) => source.status === "offline").length}
          </strong>
          <span>offline</span>
        </div>
        <div>
          <strong>
            {sources.filter((source) => source.calibrated).length}
          </strong>
          <span>calibrated</span>
        </div>
        <p>
          <AlertTriangle size={15} />
          POC health reflects the last manual snapshot test, not continuous
          monitoring.
        </p>
      </div>
      <div className="filter-row">
        {["all", "online", "offline", "unknown", "unsupported"].map((value) => (
          <button
            key={value}
            className={filter === value ? "active" : ""}
            onClick={() => setFilter(value)}
          >
            {value}
            <span>
              {
                sources.filter(
                  (source) => value === "all" || source.status === value,
                ).length
              }
            </span>
          </button>
        ))}
      </div>
      {loading ? (
        <LoadingState label="Loading streams…" />
      ) : error ? (
        <ErrorState error={error} retry={load} />
      ) : (
        <div className="stream-grid">
          {visible.map((source) => (
            <article className="stream-card" key={source.id}>
              <div className="stream-image">
                <img
                  src={assetUrl(
                    `/sources/${source.id}/snapshot.jpg?t=${Date.now()}`,
                  )}
                  alt={`Latest frame from ${source.name}`}
                />
                <Badge
                  tone={
                    source.status === "online"
                      ? "positive"
                      : source.status === "offline"
                        ? "danger"
                        : "neutral"
                  }
                >
                  <span className="badge-dot" />
                  {source.status}
                </Badge>
              </div>
              <div className="stream-body">
                <div className="stream-title">
                  <div>
                    <strong>{source.name}</strong>
                    <small>
                      {source.kind.toUpperCase()} ·{" "}
                      {source.placement ? "Placed" : "Not placed"}
                    </small>
                  </div>
                  <button
                    className="icon-button"
                    onClick={() => setEditing(source)}
                    aria-label={`Edit ${source.name}`}
                  >
                    <Settings2 size={16} />
                  </button>
                </div>
                <div className="stream-meta">
                  <span>
                    <CheckCircle2 size={14} />
                    {source.calibrated
                      ? `Calibrated ±${source.calibration.error_m.toFixed(2)}m`
                      : "Calibration required"}
                  </span>
                  <span>
                    <RefreshCw size={14} />
                    {source.last_checked
                      ? `Checked ${formatDateTime(source.last_checked)}`
                      : "Not tested"}
                  </span>
                </div>
                <div className="card-actions">
                  <button
                    className="button button-secondary"
                    onClick={() => test(source)}
                  >
                    <RefreshCw size={14} />
                    Test frame
                  </button>
                  <button
                    className="button button-secondary"
                    onClick={() => setConnection(source)}
                  >
                    <Eye size={14} />
                    Connection
                  </button>
                  <button
                    className="button button-ghost danger"
                    onClick={() => remove(source)}
                  >
                    <Trash2 size={14} />
                    Remove
                  </button>
                </div>
              </div>
            </article>
          ))}
          {!visible.length && (
            <EmptyState title="No streams in this view">
              Add a camera or choose a different health filter.
            </EmptyState>
          )}
        </div>
      )}
      {editing && (
        <SourceModal
          source={editing.id ? editing : null}
          onClose={() => setEditing(null)}
          onSaved={() => {
            setEditing(null);
            load();
            notify("Stream saved", "The camera configuration is ready.");
          }}
        />
      )}
      {connection && (
        <ConnectionModal
          source={connection}
          onClose={() => setConnection(null)}
          notify={notify}
        />
      )}
    </>
  );
}

function SourceModal({ source, onClose, onSaved }) {
  const [form, setForm] = useState({
      name: source?.name || "",
      kind: source?.kind || "rtsp",
      url: source?.url || "",
      username: source?.username || "",
      password: "",
      extra: JSON.stringify(source?.extra || {}),
    }),
    [saving, setSaving] = useState(false),
    [error, setError] = useState("");
  const change = (key, value) =>
    setForm((current) => ({ ...current, [key]: value }));
  const save = async () => {
    setSaving(true);
    setError("");
    try {
      const body = {
        name: form.name.trim(),
        kind: form.kind,
        url: form.url.trim(),
        username: form.username.trim(),
        extra: JSON.parse(form.extra || "{}"),
      };
      if (form.password) body.password = form.password;
      if (!body.name) throw new Error("Camera name is required");
      source
        ? await api.put(`/sources/${source.id}`, body)
        : await api.post("/sources", { ...body, password: form.password });
      onSaved();
    } catch (err) {
      setError(err.message);
    } finally {
      setSaving(false);
    }
  };
  return (
    <Modal
      title={source ? `Edit ${source.name}` : "Add camera stream"}
      onClose={onClose}
      footer={
        <>
          <button className="button button-secondary" onClick={onClose}>
            Cancel
          </button>
          <button
            className="button button-dark"
            onClick={save}
            disabled={saving}
          >
            <Save size={15} />
            {saving ? "Saving…" : "Save stream"}
          </button>
        </>
      }
    >
      <div className="form-grid">
        <label className="field field-full">
          <span>Camera name</span>
          <input
            value={form.name}
            onChange={(e) => change("name", e.target.value)}
            placeholder="Main entrance"
          />
        </label>
        <label className="field">
          <span>Source type</span>
          <select
            value={form.kind}
            onChange={(e) => change("kind", e.target.value)}
          >
            {["rtsp", "http", "webcam", "file", "webrtc"].map((kind) => (
              <option key={kind}>{kind}</option>
            ))}
          </select>
        </label>
        <label className="field field-full">
          <span>URL, device, or file path</span>
          <input
            value={form.url}
            onChange={(e) => change("url", e.target.value)}
            placeholder={
              form.kind === "webcam" ? "0" : "rtsp://camera.local/stream"
            }
          />
        </label>
        <label className="field">
          <span>Username</span>
          <input
            value={form.username}
            onChange={(e) => change("username", e.target.value)}
          />
        </label>
        <label className="field">
          <span>Password</span>
          <input
            type="password"
            value={form.password}
            onChange={(e) => change("password", e.target.value)}
            placeholder={
              source?.has_password ? "Leave empty to keep current" : ""
            }
          />
        </label>
        <label className="field field-full">
          <span>Worker configuration (JSON)</span>
          <textarea
            rows="3"
            value={form.extra}
            onChange={(e) => change("extra", e.target.value)}
          />
        </label>
      </div>
      {error && (
        <div className="form-error" role="alert">
          {error}
        </div>
      )}
    </Modal>
  );
}

export function ConfigurePage({ notify, refreshShell, liveTick = 0 }) {
  const [tab, setTab] = useState("workspace"),
    [store, setStore] = useState(null),
    [zones, setZones] = useState([]),
    [sources, setSources] = useState([]),
    [jobs, setJobs] = useState([]),
    [rules, setRules] = useState([]),
    [loading, setLoading] = useState(true),
    [error, setError] = useState(null),
    [showRule, setShowRule] = useState(false);
  const load = async () => {
    setLoading(true);
    try {
      const values = await Promise.all([
        api.get("/store"),
        api.get("/zones"),
        api.get("/sources"),
        api.get("/jobs"),
        api.get("/alert-rules"),
      ]);
      setStore(values[0]);
      setZones(values[1]);
      setSources(values[2]);
      setJobs(values[3]);
      setRules(values[4]);
      setError(null);
    } catch (err) {
      setError(err);
    } finally {
      setLoading(false);
    }
  };
  useEffect(() => {
    load();
  }, []);
  if (loading && !store) return <LoadingState label="Loading configuration…" />;
  if (error && !store) return <ErrorState error={error} retry={load} />;
  const readiness = [
    sources.length > 0,
    zones.length > 0,
    sources.some((source) => source.calibrated),
    jobs.length > 0,
  ];
  return (
    <>
      <PageHeader
        eyebrow="Configure"
        title="Pilot setup"
        description="A guided path from camera access to an accepted operational signal."
      />
      <div className="setup-progress">
        {[
          ["Streams", readiness[0]],
          ["Zones", readiness[1]],
          ["Calibration", readiness[2]],
          ["Analysis", readiness[3]],
        ].map(([label, done], index) => (
          <div key={label} className={done ? "done" : ""}>
            <span>{done ? <CheckCircle2 size={16} /> : index + 1}</span>
            <small>{label}</small>
          </div>
        ))}
      </div>
      <div className="configure-layout">
        <nav className="configure-nav">
          {[
            ["workspace", "Workspace", Settings2],
            ["space", "Space & zones", Map],
            ["analyses", "Analyses", Activity],
            ["rules", "Thresholds", BellRing],
            ["technical", "Technical details", Code2],
          ].map(([value, label, Icon]) => (
            <button
              key={value}
              className={tab === value ? "active" : ""}
              onClick={() => setTab(value)}
            >
              <Icon size={16} />
              {label}
              <ChevronRight size={14} />
            </button>
          ))}
        </nav>
        <div className="configure-main">
          {tab === "workspace" && (
            <WorkspaceForm
              store={store}
              onSaved={(saved) => {
                setStore(saved);
                refreshShell?.();
                notify(
                  "Workspace updated",
                  "ManySight now uses the new workspace details.",
                );
              }}
            />
          )}
          {tab === "space" && (
            <SpaceWorkbench
              store={store}
              zones={zones}
              sources={sources}
              onRefresh={load}
              notify={notify}
            />
          )}
          {tab === "analyses" && <JobsConfig jobs={jobs} onRefresh={load} />}
          {tab === "rules" && (
            <RulesConfig
              rules={rules}
              onRefresh={load}
              onAdd={() => setShowRule(true)}
            />
          )}
          {tab === "technical" && (
            <TechnicalConfig
              notify={notify}
              sources={sources}
              zones={zones}
              liveTick={liveTick}
            />
          )}
        </div>
      </div>
      {showRule && (
        <RuleModal
          zones={zones}
          sources={sources}
          onClose={() => setShowRule(false)}
          onSaved={() => {
            setShowRule(false);
            load();
            notify(
              "Threshold created",
              "New events will be evaluated against this rule.",
            );
          }}
        />
      )}
    </>
  );
}

function WorkspaceForm({ store, onSaved }) {
  const [form, setForm] = useState({ ...store }),
    [saving, setSaving] = useState(false);
  const save = async () => {
    setSaving(true);
    try {
      onSaved(
        await api.put("/store", {
          name: form.name,
          space_type: form.space_type || "store",
          environment: form.environment,
          width_m: Number(form.width_m),
          height_m: Number(form.height_m),
        }),
      );
    } finally {
      setSaving(false);
    }
  };
  return (
    <Panel
      title="Workspace details"
      subtitle="The identity and data state shown across the dashboard"
    >
      <div className="form-grid">
        <label className="field field-full">
          <span>Workspace name</span>
          <input
            value={form.name}
            onChange={(e) => setForm({ ...form, name: e.target.value })}
          />
        </label>
        <label className="field">
          <span>Data state</span>
          <select
            value={form.environment || "setup"}
            onChange={(e) => setForm({ ...form, environment: e.target.value })}
          >
            <option value="setup">Setup incomplete</option>
            <option value="demo">Example data</option>
            <option value="live">Live pilot</option>
          </select>
          <small>
            Choose Live pilot only for real connected streams and workers.
          </small>
        </label>
        <label className="field">
          <span>Space type</span>
          <select
            value={form.space_type || "store"}
            onChange={(e) => setForm({ ...form, space_type: e.target.value })}
          >
            <option value="store">Retail store</option>
            <option value="school">School</option>
            <option value="office">Office / workplace</option>
            <option value="warehouse">Warehouse</option>
            <option value="public_space">Public space</option>
            <option value="custom">Other physical space</option>
          </select>
        </label>
        <label className="field">
          <span>Width (metres)</span>
          <input
            type="number"
            value={form.width_m}
            onChange={(e) => setForm({ ...form, width_m: e.target.value })}
          />
        </label>
        <label className="field">
          <span>Height (metres)</span>
          <input
            type="number"
            value={form.height_m}
            onChange={(e) => setForm({ ...form, height_m: e.target.value })}
          />
        </label>
      </div>
      <div className="panel-footer">
        <button className="button button-dark" disabled={saving} onClick={save}>
          <Save size={15} />
          {saving ? "Saving…" : "Save workspace"}
        </button>
      </div>
    </Panel>
  );
}

function JobsConfig({ jobs, onRefresh }) {
  const toggle = async (job) => {
    await api.put(`/jobs/${job.id}`, {
      status: job.status === "active" ? "paused" : "active",
    });
    onRefresh();
  };
  const remove = async (job) => {
    if (window.confirm(`Delete job ${job.name}? Its events remain.`)) {
      await api.del(`/jobs/${job.id}`);
      onRefresh();
    }
  };
  return (
    <Panel
      title="Analysis registrations"
      subtitle="POC job metadata; this does not yet manage or restart worker processes"
    >
      <div className="experimental-note">
        <AlertTriangle size={17} />
        <div>
          <strong>Runtime state is external</strong>
          <p>
            An active registration does not prove that its worker process is
            running. Continuous heartbeat and restart controls remain backend
            work.
          </p>
        </div>
      </div>
      <div className="data-list">
        {jobs.map((job) => (
          <div key={job.id}>
            <span className={`status-light ${job.status}`} />
            <div>
              <strong>{job.name}</strong>
              <small>
                {job.event_count.toLocaleString()} events · last{" "}
                {formatDateTime(job.last_event_at)}
              </small>
            </div>
            <Badge tone={job.status === "active" ? "positive" : "neutral"}>
              {job.status}
            </Badge>
            <button className="icon-button" onClick={() => toggle(job)}>
              {job.status === "active" ? (
                <Pause size={15} />
              ) : (
                <Play size={15} />
              )}
            </button>
            <button className="icon-button danger" onClick={() => remove(job)}>
              <Trash2 size={15} />
            </button>
          </div>
        ))}
        {!jobs.length && (
          <EmptyState title="No analyses registered">
            Codex or a worker registers a job before posting events.
          </EmptyState>
        )}
      </div>
    </Panel>
  );
}

function RulesConfig({ rules, onRefresh, onAdd }) {
  const toggle = async (rule) => {
    await api.put(`/alert-rules/${rule.id}`, { enabled: !rule.enabled });
    onRefresh();
  };
  const remove = async (rule) => {
    if (window.confirm(`Delete ${rule.name}?`)) {
      await api.del(`/alert-rules/${rule.id}`);
      onRefresh();
    }
  };
  return (
    <Panel
      title="Thresholds & notifications"
      subtitle="Rules turn narrow conditions into reviewable signals"
      action={
        <button className="button button-dark" onClick={onAdd}>
          <Plus size={14} />
          New threshold
        </button>
      }
    >
      <div className="data-list">
        {rules.map((rule) => (
          <div key={rule.id}>
            <span
              className={`status-light ${rule.enabled ? "active" : "paused"}`}
            />
            <div>
              <strong>{rule.name}</strong>
              <small>
                {rule.kind.replaceAll("_", " ")} · {JSON.stringify(rule.params)}
                {rule.webhook_url ? " · webhook" : ""}
              </small>
            </div>
            <Badge tone={rule.enabled ? "positive" : "neutral"}>
              {rule.enabled ? "enabled" : "paused"}
            </Badge>
            <button className="icon-button" onClick={() => toggle(rule)}>
              {rule.enabled ? <Pause size={15} /> : <Play size={15} />}
            </button>
            <button className="icon-button danger" onClick={() => remove(rule)}>
              <Trash2 size={15} />
            </button>
          </div>
        ))}
        {!rules.length && (
          <EmptyState title="No thresholds configured">
            Create a narrow condition for a queue, dwell, occupancy, or event
            signal.
          </EmptyState>
        )}
      </div>
    </Panel>
  );
}

function RuleModal({ zones, sources, onClose, onSaved }) {
  const [form, setForm] = useState({
      name: "",
      kind: "occupancy_exceeds",
      zone_id: "",
      threshold: 5,
      window: 60,
      source_id: "",
      state_label: "open",
      event_type: "detection",
      attr_key: "",
      attr_value: "",
      webhook_url: "",
      cooldown: 60,
    }),
    [error, setError] = useState("");
  const save = async () => {
    try {
      const params = {};
      if (form.zone_id) params.zone_id = Number(form.zone_id);
      if (form.kind === "occupancy_exceeds") {
        params.count = Number(form.threshold);
        params.window_s = Number(form.window);
      } else if (form.kind === "dwell_exceeds") {
        params.seconds = Number(form.threshold);
      } else if (form.kind === "state_alert") {
        params.label = form.state_label;
        if (form.source_id) params.source_id = Number(form.source_id);
        if (form.threshold) params.min_seconds = Number(form.threshold);
      } else {
        params.event_type = form.event_type;
        if (form.attr_key) params.attr_key = form.attr_key;
        if (form.attr_value) params.attr_value = form.attr_value;
      }
      await api.post("/alert-rules", {
        name: form.name || "Unnamed threshold",
        kind: form.kind,
        params,
        cooldown_s: Number(form.cooldown) || 60,
        webhook_url: form.webhook_url.trim(),
      });
      onSaved();
    } catch (err) {
      setError(err.message);
    }
  };
  return (
    <Modal
      title="New review threshold"
      onClose={onClose}
      footer={
        <>
          <button className="button button-secondary" onClick={onClose}>
            Cancel
          </button>
          <button className="button button-dark" onClick={save}>
            Create threshold
          </button>
        </>
      }
    >
      <div className="form-grid">
        <label className="field field-full">
          <span>Name</span>
          <input
            value={form.name}
            onChange={(e) => setForm({ ...form, name: e.target.value })}
            placeholder="Queue pressure at main checkout"
          />
        </label>
        <label className="field field-full">
          <span>Condition</span>
          <select
            value={form.kind}
            onChange={(e) =>
              setForm({
                ...form,
                kind: e.target.value,
                threshold:
                  e.target.value === "occupancy_exceeds"
                    ? 5
                    : e.target.value === "dwell_exceeds"
                      ? 60
                      : "",
              })
            }
          >
            <option value="occupancy_exceeds">Occupancy exceeds</option>
            <option value="dwell_exceeds">Dwell exceeds</option>
            <option value="state_alert">State duration / change</option>
            <option value="event_match">Event match</option>
          </select>
        </label>
        {form.kind !== "state_alert" && (
          <label className="field">
            <span>Zone</span>
            <select
              value={form.zone_id}
              onChange={(e) => setForm({ ...form, zone_id: e.target.value })}
            >
              <option value="">Any zone</option>
              {zones.map((zone) => (
                <option key={zone.id} value={zone.id}>
                  {zone.name}
                </option>
              ))}
            </select>
          </label>
        )}
        {form.kind === "state_alert" && (
          <label className="field">
            <span>Camera</span>
            <select
              value={form.source_id}
              onChange={(e) => setForm({ ...form, source_id: e.target.value })}
            >
              <option value="">Any camera</option>
              {sources.map((source) => (
                <option key={source.id} value={source.id}>
                  {source.name}
                </option>
              ))}
            </select>
          </label>
        )}
        {form.kind === "state_alert" && (
          <label className="field">
            <span>State label</span>
            <input
              value={form.state_label}
              onChange={(e) =>
                setForm({ ...form, state_label: e.target.value })
              }
              placeholder="open"
            />
          </label>
        )}
        {form.kind === "event_match" && (
          <label className="field">
            <span>Event type</span>
            <select
              value={form.event_type}
              onChange={(e) => setForm({ ...form, event_type: e.target.value })}
            >
              {[
                "detection",
                "zone_enter",
                "zone_exit",
                "zone_dwell",
                "transition",
                "state_change",
                "count",
                "custom",
              ].map((type) => (
                <option key={type} value={type}>
                  {type.replaceAll("_", " ")}
                </option>
              ))}
            </select>
          </label>
        )}
        {form.kind === "event_match" && (
          <label className="field">
            <span>Attribute key (optional)</span>
            <input
              value={form.attr_key}
              onChange={(e) => setForm({ ...form, attr_key: e.target.value })}
              placeholder="severity"
            />
          </label>
        )}
        {form.kind === "event_match" && form.attr_key && (
          <label className="field">
            <span>Attribute value</span>
            <input
              value={form.attr_value}
              onChange={(e) => setForm({ ...form, attr_value: e.target.value })}
              placeholder="high"
            />
          </label>
        )}
        {form.kind !== "event_match" && (
          <label className="field">
            <span>
              {form.kind === "occupancy_exceeds"
                ? "People"
                : form.kind === "state_alert"
                  ? "Minimum duration (seconds, optional)"
                  : "Seconds"}
            </span>
            <input
              type="number"
              value={form.threshold}
              onChange={(e) => setForm({ ...form, threshold: e.target.value })}
            />
          </label>
        )}
        {form.kind === "occupancy_exceeds" && (
          <label className="field">
            <span>Window (seconds)</span>
            <input
              type="number"
              value={form.window}
              onChange={(e) => setForm({ ...form, window: e.target.value })}
            />
          </label>
        )}
        <label className="field">
          <span>Cooldown (seconds)</span>
          <input
            type="number"
            min="0"
            value={form.cooldown}
            onChange={(e) => setForm({ ...form, cooldown: e.target.value })}
          />
        </label>
        <label className="field field-full">
          <span>Webhook URL (optional)</span>
          <input
            type="url"
            value={form.webhook_url}
            onChange={(e) => setForm({ ...form, webhook_url: e.target.value })}
            placeholder="https://automation.example/webhook"
          />
          <small>
            Each fired signal is POSTed as JSON. Use a trusted HTTPS endpoint.
          </small>
        </label>
      </div>
      {error && (
        <div className="form-error" role="alert">
          {error}
        </div>
      )}
    </Modal>
  );
}
