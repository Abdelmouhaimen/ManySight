import { useState } from "react";
import {
  ArrowRight,
  Check,
  Code2,
  Copy,
  ExternalLink,
  Eye,
  EyeOff,
  RefreshCw,
  Save,
  ScanSearch,
  ShieldAlert,
} from "lucide-react";
import { api, apiKey } from "./api.js";
import { Badge, Modal, Panel } from "./components.jsx";

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

export function TechnicalConfig({ notify }) {
  const [key, setKey] = useState(apiKey());

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
              [
                "4",
                "Explain",
                "The worker posts raw observations that the platform turns into insights",
              ],
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
        subtitle="Moved to its own tab"
      >
        <div className="technical-links">
          <a href="#detections">
            <ScanSearch />
            Open the Detections tab
            <span>Filterable, paginated, documented</span>
            <ArrowRight />
          </a>
        </div>
        <p className="definition-note">
          Every event workers posted — with column and event-type documentation
          — now lives in the Detections tab.
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

