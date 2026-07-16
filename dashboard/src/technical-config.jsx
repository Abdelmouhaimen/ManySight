import { useEffect, useMemo, useState } from "react";
import {
  Activity,
  ArrowRight,
  Check,
  Code2,
  Copy,
  ExternalLink,
  Eye,
  EyeOff,
  RefreshCw,
  Save,
  ShieldAlert,
} from "lucide-react";
import { api, apiKey, formatDateTime } from "./api.js";
import {
  Badge,
  EmptyState,
  LoadingState,
  Modal,
  Panel,
} from "./components.jsx";

const EVENT_TYPES = [
  "all",
  "detection",
  "zone_enter",
  "zone_exit",
  "zone_dwell",
  "transition",
  "state_change",
  "count",
  "custom",
];

export function ConnectionModal({ source, onClose, notify }) {
  const [details, setDetails] = useState(null),
    [revealed, setRevealed] = useState(false),
    [loading, setLoading] = useState(false),
    [error, setError] = useState("");
  const reveal = async () => {
    setLoading(true);
    setError("");
    try {
      setDetails(await api.get(`/sources/${source.id}?secrets=true`));
      setRevealed(true);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };
  const copy = async (value, label) => {
    try {
      await navigator.clipboard.writeText(value || "");
      notify(`${label} copied`, "Treat camera credentials as sensitive.");
    } catch {
      notify("Copy failed", "Select and copy the value manually.", "error");
    }
  };
  return (
    <Modal title={`${source.name} connection`} onClose={onClose}>
      <div className="sensitive-note">
        <ShieldAlert size={18} />
        <div>
          <strong>Sensitive technical access</strong>
          <p>
            Only reveal this when configuring a trusted worker. Browser
            dashboards do not continuously consume the feed, and adding a source
            alone does not start analytics.
          </p>
        </div>
      </div>
      <dl className="connection-summary">
        <div>
          <dt>Protocol</dt>
          <dd>{source.kind.toUpperCase()}</dd>
        </div>
        <div>
          <dt>Source status</dt>
          <dd>
            <Badge tone={source.status === "online" ? "positive" : "neutral"}>
              {source.status}
            </Badge>
          </dd>
        </div>
        <div>
          <dt>Worker configuration</dt>
          <dd>
            <code>{JSON.stringify(source.extra || {})}</code>
          </dd>
        </div>
      </dl>
      {!revealed ? (
        <button
          className="button button-dark reveal-button"
          onClick={reveal}
          disabled={loading}
        >
          {loading ? (
            <RefreshCw className="spin" size={14} />
          ) : (
            <Eye size={14} />
          )}
          {loading ? "Loading…" : "Reveal connection details"}
        </button>
      ) : (
        <div className="secret-fields">
          {[
            ["Connect URL", details?.connect_url],
            ["Configured URL", details?.url],
            ["Username", details?.username],
            ["Password", details?.password],
          ].map(([label, value]) => (
            <label className="field" key={label}>
              <span>{label}</span>
              <div className="copy-field">
                <input
                  readOnly
                  type={label === "Password" ? "password" : "text"}
                  value={value || ""}
                  aria-label={label}
                />
                <button
                  className="icon-button"
                  onClick={() => copy(value, label)}
                  aria-label={`Copy ${label}`}
                >
                  <Copy size={14} />
                </button>
              </div>
            </label>
          ))}
          <button
            className="button button-secondary"
            onClick={() => setRevealed(false)}
          >
            <EyeOff size={14} />
            Hide details
          </button>
        </div>
      )}
      {error && (
        <div className="form-error" role="alert">
          {error}
        </div>
      )}
    </Modal>
  );
}

export function TechnicalConfig({ notify, sources, zones, liveTick = 0 }) {
  const [key, setKey] = useState(apiKey());
  const [events, setEvents] = useState([]),
    [loading, setLoading] = useState(true),
    [error, setError] = useState("");
  const [filters, setFilters] = useState({
    event_type: "all",
    source_id: "all",
    zone_id: "all",
  });
  const [expanded, setExpanded] = useState(null),
    [autoRefresh, setAutoRefresh] = useState(true);

  const query = useMemo(() => {
    const params = new URLSearchParams({ limit: "100" });
    Object.entries(filters).forEach(([name, value]) => {
      if (value !== "all") params.set(name, value);
    });
    return params.toString();
  }, [filters]);

  const load = async (quiet = false) => {
    if (!quiet) setLoading(true);
    setError("");
    try {
      const result = await api.get(`/events?${query}`);
      setEvents(result.events);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };
  useEffect(() => {
    load();
  }, [query]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (!autoRefresh || !liveTick) return;
    const timer = window.setTimeout(() => load(true), 500);
    return () => window.clearTimeout(timer);
  }, [liveTick]); // eslint-disable-line react-hooks/exhaustive-deps

  const saveKey = () => {
    key
      ? localStorage.setItem("storelens_api_key", key)
      : localStorage.removeItem("storelens_api_key");
    notify("API key saved", "Reloading applies it to dashboard requests.");
    window.location.reload();
  };

  return (
    <div className="stack">
      <Panel
        title="Agent analysis contract"
        subtitle="What happens after a camera source is added"
      >
        <div className="agent-contract">
          <div className="agent-flow" aria-label="Analysis workflow">
            {[
              ["1", "Connect", "Store a stream URL and capture a frame"],
              [
                "2",
                "Inspect",
                "Codex checks snapshots, zones, and calibration",
              ],
              ["3", "Run", "An external worker subscribes and runs a model"],
              ["4", "Explain", "The worker posts events that become insights"],
            ].map(([number, title, detail], index) => (
              <div key={number}>
                <span>{number}</span>
                <div>
                  <strong>{title}</strong>
                  <small>{detail}</small>
                </div>
                {index < 3 && <ArrowRight size={15} />}
              </div>
            ))}
          </div>
          <div className="contract-columns">
            <section>
              <Badge tone="positive">
                <Check size={12} />
                Codex can
              </Badge>
              <ul>
                <li>
                  Inspect stored source metadata and snapshots through the API
                  or MCP.
                </li>
                <li>
                  Choose or write a classifier/tracker worker for an approved
                  question.
                </li>
                <li>
                  Register the analysis job and submit structured event batches.
                </li>
                <li>
                  Verify which job, source, zone, and rule produced a dashboard
                  signal.
                </li>
              </ul>
            </section>
            <section>
              <Badge tone="warning">
                <ShieldAlert size={12} />
                Important limits
              </Badge>
              <ul>
                <li>
                  The dashboard itself does not subscribe to RTSP or run models
                  continuously.
                </li>
                <li>
                  A job marked active is registration metadata, not proof that
                  its worker is alive.
                </li>
                <li>
                  Source access, model runtime, and credentials must exist where
                  the worker runs.
                </li>
                <li>
                  Outputs require sample-based validation before operational
                  use.
                </li>
              </ul>
            </section>
          </div>
        </div>
      </Panel>

      <Panel
        title="Raw event explorer"
        subtitle="Inspect the evidence stream behind charts and reviewable signals"
        action={
          <div className="event-refresh">
            <label>
              <input
                type="checkbox"
                checked={autoRefresh}
                onChange={(event) => setAutoRefresh(event.target.checked)}
              />{" "}
              Follow live events
            </label>
            <button
              className="icon-button"
              onClick={() => load()}
              aria-label="Refresh raw events"
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
              onChange={(event) =>
                setFilters({ ...filters, event_type: event.target.value })
              }
            >
              {EVENT_TYPES.map((type) => (
                <option key={type} value={type}>
                  {type.replaceAll("_", " ")}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            <span>Camera</span>
            <select
              value={filters.source_id}
              onChange={(event) =>
                setFilters({ ...filters, source_id: event.target.value })
              }
            >
              <option value="all">All cameras</option>
              {sources.map((source) => (
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
              onChange={(event) =>
                setFilters({ ...filters, zone_id: event.target.value })
              }
            >
              <option value="all">All zones</option>
              {zones.map((zone) => (
                <option key={zone.id} value={zone.id}>
                  {zone.name}
                </option>
              ))}
            </select>
          </label>
        </div>
        {error && (
          <div className="form-error" role="alert">
            {error}
          </div>
        )}
        {loading ? (
          <LoadingState label="Loading raw events…" />
        ) : !events.length ? (
          <EmptyState title="No matching events">
            Change the filters or run an analysis worker that submits event
            batches.
          </EmptyState>
        ) : (
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
                    source={sources.find(
                      (source) => source.id === event.source_id,
                    )}
                    expanded={expanded === event.id}
                    toggle={() =>
                      setExpanded(expanded === event.id ? null : event.id)
                    }
                  />
                ))}
              </tbody>
            </table>
          </div>
        )}
        <p className="definition-note">
          Rows are raw worker outputs after server-side projection and zone
          assignment. They are evidence for an insight, not automatically a
          real-world conclusion.
        </p>
      </Panel>

      <div className="technical-grid">
        <Panel
          title="API access"
          subtitle="Local browser credential for a protected POC server"
        >
          <label className="field">
            <span>API key</span>
            <input
              type="password"
              value={key}
              onChange={(event) => setKey(event.target.value)}
              placeholder="Leave empty when authentication is disabled"
            />
          </label>
          <div className="panel-footer">
            <button className="button button-dark" onClick={saveKey}>
              <Save size={14} />
              Save API key
            </button>
          </div>
        </Panel>
        <Panel
          title="Developer access"
          subtitle="Build and verify external analysis workers"
        >
          <div className="technical-links">
            <a href="/docs" target="_blank" rel="noreferrer">
              <Code2 />
              Interactive API documentation<span>OpenAPI</span>
              <ExternalLink />
            </a>
          </div>
          <div className="definition-card compact-definition">
            <Code2 />
            <h3>Codex / MCP</h3>
            <p>
              Connect this repository's MCP server so Codex can discover
              sources, inspect frames, follow a skill recipe, register a job,
              and submit events.
            </p>
            <pre>
              [mcp_servers.storelens]{"\n"}command = "python"{"\n"}args =
              [".../mcp_server/server.py"]
            </pre>
          </div>
        </Panel>
      </div>
    </div>
  );
}

function EventRows({ event, source, expanded, toggle }) {
  return (
    <>
      <tr>
        <td>{formatDateTime(event.ts)}</td>
        <td>
          <Badge tone="violet">{event.event_type.replaceAll("_", " ")}</Badge>
        </td>
        <td>
          {source?.name || (event.source_id ? `#${event.source_id}` : "—")}
        </td>
        <td>{event.zone_name || "—"}</td>
        <td>{event.track_id || event.label || "—"}</td>
        <td>{event.value ?? "—"}</td>
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
          <td colSpan="7">
            <div>
              <span>
                Event #{event.id} · job {event.job_id || "unregistered"}
              </span>
              <pre>
                {JSON.stringify(
                  {
                    point_px:
                      event.x_px == null
                        ? null
                        : { x: event.x_px, y: event.y_px },
                    point_map:
                      event.x_map == null
                        ? null
                        : { x: event.x_map, y: event.y_map },
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
