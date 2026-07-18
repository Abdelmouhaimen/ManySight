import { useEffect, useState } from "react";
import {
  Activity,
  AlertTriangle,
  ArrowRight,
  BellRing,
  CheckCircle2,
  ChevronRight,
  Code2,
  Map,
  Pause,
  Play,
  Plus,
  RefreshCw,
  Save,
  Settings2,
  Trash2,
} from "lucide-react";
import { api, formatDateTime, formatDuration } from "./api.js";
import {
  ActivityMap,
  Badge,
  EmptyState,
  ErrorState,
  LineChart,
  LoadingState,
  MetricCard,
  Modal,
  PageHeader,
  Panel,
  RangeSelect,
  SignalRow,
} from "./components.jsx";
import { InsightCard } from "./insights.jsx";
import { SpaceWorkbench } from "./space-workbench.jsx";
import { TechnicalConfig } from "./technical-config.jsx";

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
      const [summary, store, zones, sources, heat, dwell, occupancy, alerts, pinned] =
        await Promise.all([
          api.get(`/analytics/summary?${query}`),
          api.get("/store"),
          api.get("/zones"),
          api.get("/sources"),
          api.get(`/analytics/heatmap?${query}`),
          api.get(`/analytics/dwell?${query}`),
          api.get(`/analytics/occupancy?${query}`),
          api.get("/alerts?limit=60"),
          api.get("/insights?pinned=true"),
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
          alerts,
          pinned,
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
      {d.pinned.length > 0 && (
        <div className="insight-grid">
          {d.pinned.map((definition) => (
            <InsightCard
              key={definition.id}
              definition={definition}
              range={range}
              context={{ store: d.store, zones: d.zones, sources: d.sources }}
              liveTick={liveTick}
              readOnly
            />
          ))}
        </div>
      )}
      <Panel
        title="Recent signals"
        subtitle="Model outputs remain reviewable and traceable"
        action={
          <a className="text-link" href="#review">
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

export function ReviewPage({ liveTick = 0, initialSignal, clearInitial }) {
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
        eyebrow="Review queue"
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

export function SourcesPage({ liveTick = 0 }) {
  const [sources, setSources] = useState([]),
    [loading, setLoading] = useState(true),
    [error, setError] = useState(null),
    [filter, setFilter] = useState("all");
  const load = async () => {
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
  }, [liveTick]);

  const statuses = ["all", "active", "recent", "stale", "never"];
  const visible = sources.filter(
    (source) =>
      filter === "all" || source.observation_status === filter,
  );
  const statusTone = (status) =>
    status === "active"
      ? "positive"
      : status === "recent"
        ? "warning"
        : status === "stale"
          ? "danger"
          : "neutral";

  return (
    <>
      <PageHeader
        eyebrow="Sources"
        title="Observation sources"
        description="Logical provenance registered by agents. Camera access and models run on the device that can reach the source."
      />
      <div className="health-summary source-health-summary">
        <div>
          <strong>
            {sources.filter((source) => source.observation_status === "active").length}
          </strong>
          <span>active now</span>
        </div>
        <div>
          <strong>
            {sources.reduce((sum, source) => sum + source.event_count, 0)}
          </strong>
          <span>observations</span>
        </div>
        <div>
          <strong>
            {sources.filter(
              (source) => source.latest_runtime?.worker?.effective_status === "running",
            ).length}
          </strong>
          <span>workers live</span>
        </div>
        <p>
          <AlertTriangle size={15} />
          Health reflects event ingestion and worker heartbeats, never a server-side camera probe.
        </p>
      </div>
      <div className="filter-row">
        {statuses.map((value) => (
          <button
            key={value}
            className={filter === value ? "active" : ""}
            onClick={() => setFilter(value)}
          >
            {value}
            <span>
              {sources.filter(
                (source) => value === "all" || source.observation_status === value,
              ).length}
            </span>
          </button>
        ))}
      </div>
      {loading ? (
        <LoadingState label="Loading sources…" />
      ) : error ? (
        <ErrorState error={error} retry={load} />
      ) : (
        <div className="source-status-grid">
          {visible.map((source) => {
            const worker = source.latest_runtime?.worker;
            return (
              <article className="source-status-card" key={source.id}>
                <header>
                  <div className="source-kind-mark">
                    <Activity size={18} />
                  </div>
                  <div>
                    <span className="tiny-label">Source #{source.id}</span>
                    <h2>{source.name}</h2>
                  </div>
                  <Badge tone={statusTone(source.observation_status)}>
                    <span className="badge-dot" />
                    {source.observation_status}
                  </Badge>
                </header>
                <dl className="source-status-facts">
                  <div>
                    <dt>Type</dt>
                    <dd>{source.kind.toUpperCase()}</dd>
                  </div>
                  <div>
                    <dt>Connection</dt>
                    <dd>{source.connection_mode.replaceAll("_", " ")}</dd>
                  </div>
                  <div>
                    <dt>Last observation</dt>
                    <dd>{formatDateTime(source.last_observation_at)}</dd>
                  </div>
                  <div>
                    <dt>Received</dt>
                    <dd>
                      {source.observation_age_s == null
                        ? "No data yet"
                        : `${formatDuration(source.observation_age_s)} ago`}
                    </dd>
                  </div>
                </dl>
                <div className="source-runtime-row">
                  <div>
                    <span className="tiny-label">Latest runtime</span>
                    <strong>{source.latest_runtime?.job_name || "No job registered"}</strong>
                  </div>
                  <Badge
                    tone={worker?.effective_status === "running" ? "positive" : "neutral"}
                  >
                    {worker?.effective_status || "not running"}
                  </Badge>
                </div>
                <div className="source-capabilities">
                  {(source.capabilities.length ? source.capabilities : ["unspecified"]).map(
                    (capability) => <span key={capability}>{capability}</span>,
                  )}
                </div>
                <footer>
                  <span>{source.event_count.toLocaleString()} stored observations</span>
                  <a href={`#detections`} className="button button-secondary">
                    Inspect data <ArrowRight size={14} />
                  </a>
                </footer>
              </article>
            );
          })}
          {!visible.length && (
            <EmptyState title="No sources in this view">
              Agents register sources through StoreLens MCP before starting a worker.
            </EmptyState>
          )}
        </div>
      )}
    </>
  );
}

export function ConfigurePage({ notify, refreshShell }) {
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
        description="A guided path from logical source definition to an accepted operational signal."
      />
      <div className="setup-progress">
        {[
          ["Sources", readiness[0]],
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
          {tab === "technical" && <TechnicalConfig notify={notify} />}
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
            Choose Live pilot only when real workers are sending observations.
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
  const toggleRegistration = async (job) => {
    await api.put(`/jobs/${job.id}`, {
      status: job.status === "active" ? "paused" : "active",
    });
    onRefresh();
  };
  const commandWorker = async (worker, desiredState) => {
    await api.put(`/workers/${worker.id}/desired-state`, {
      desired_state: desiredState,
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
      title="Analyses & workers"
      subtitle="Registered analyses plus heartbeat-backed runtime state"
    >
      <div className="experimental-note">
        <AlertTriangle size={17} />
        <div>
          <strong>Worker control is cooperative</strong>
          <p>
            A worker registers once and heartbeats every 5–15 seconds. Stop and
            restart requests are returned on its next heartbeat; a hosted
            supervisor must relaunch it after a restart request. A stale worker
            has missed heartbeats for more than 30 seconds.
          </p>
        </div>
      </div>
      <div className="data-list">
        {jobs.map((job) => {
          const worker = job.latest_worker;
          const workerStatus = worker?.effective_status || "unreported";
          return (
            <div key={job.id}>
              <span
                className={`status-light ${workerStatus === "running" ? "active" : "paused"}`}
              />
              <div>
                <strong>{job.name}</strong>
                <small>
                  {job.event_count.toLocaleString()} events · last{" "}
                  {formatDateTime(job.last_event_at)}
                </small>
                <small>
                  {worker
                    ? `${worker.name || worker.worker_id} · heartbeat ${formatDateTime(worker.last_heartbeat_at)}`
                    : "No worker has registered a heartbeat"}
                </small>
              </div>
              <Badge
                tone={
                  workerStatus === "running"
                    ? "positive"
                    : workerStatus === "error" || workerStatus === "stale"
                      ? "warning"
                      : "neutral"
                }
              >
                {workerStatus}
              </Badge>
              {worker && (
                <button
                  className="icon-button"
                  onClick={() =>
                    commandWorker(
                      worker,
                      workerStatus === "running" ? "stopped" : "restart",
                    )
                  }
                  aria-label={
                    workerStatus === "running"
                      ? `Request ${job.name} worker stop`
                      : `Request ${job.name} worker restart`
                  }
                >
                  {workerStatus === "running" ? (
                    <Pause size={15} />
                  ) : (
                    <RefreshCw size={15} />
                  )}
                </button>
              )}
              <button
                className="icon-button"
                onClick={() => toggleRegistration(job)}
                aria-label={`${job.status === "active" ? "Pause" : "Activate"} ${job.name} registration`}
              >
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
          );
        })}
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
