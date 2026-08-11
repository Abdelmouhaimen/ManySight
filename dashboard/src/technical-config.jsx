import { useEffect, useState } from "react";
import {
  ArrowRight,
  Check,
  Code2,
  ExternalLink,
  Save,
  ScanSearch,
  ShieldAlert,
} from "lucide-react";
import { api, apiKey } from "./api.js";
import { Badge, Panel } from "./components.jsx";

export function TechnicalConfig({ notify }) {
  const [key, setKey] = useState(apiKey());
  const [endpoints, setEndpoints] = useState(null);

  useEffect(() => {
    api.get("/platform-config").then(setEndpoints).catch(() => setEndpoints(null));
  }, []);

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
        subtitle="How an agent-local worker produces hosted analyses"
      >
        <div className="agent-contract">
          <div className="agent-flow" aria-label="Analysis workflow">
            {[
              ["1", "Register", "Create a managed or external-secret logical source"],
              [
                "2",
                "Inspect",
                "Codex opens the camera and inspects frames on the worker device",
              ],
              ["3", "Run", "An external worker subscribes and runs a model"],
              [
                "4",
                "Explain",
                "The worker submits raw observations that the platform derives analyses from",
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
                  Register and inspect safe logical source metadata through the
                  API or MCP; authorized workers can resolve managed connections.
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
                  StoreLens never subscribes to or proxies a webcam or RTSP feed;
                  managed credentials stay encrypted and require privileged resolution.
                </li>
                <li>
                  A job marked active is registration metadata, not proof that
                  its worker is alive.
                </li>
                <li>
                  Source reachability and model runtime must exist where the worker
                  runs; resolved credentials must remain in worker memory.
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
        title="Raw observation explorer"
        subtitle="Moved to its own tab"
      >
        <div className="technical-links">
          <a href="#observations">
            <ScanSearch />
            Open the Observations tab
            <span>Filterable, paginated, documented</span>
            <ArrowRight />
          </a>
        </div>
        <p className="definition-note">
          Every observation workers submitted — with column and kind
          documentation — now lives in the Observations tab.
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
            <a href={endpoints?.docs_url || "/docs"} target="_blank" rel="noreferrer">
              <Code2 />
              Interactive API documentation<span>OpenAPI</span>
              <ExternalLink />
            </a>
          </div>
          <div className="definition-card compact-definition">
            <Code2 />
            <h3>Codex / MCP</h3>
            <p>
              Connect StoreLens MCP so Codex can register logical sources,
              follow a skill recipe, register a job, and verify submitted
              observations. Frames remain local to the worker.
            </p>
            <pre>
              {`Remote: ${endpoints?.mcp_url || "Loading…"}\n`}
              {`REST: ${endpoints?.rest_url || "Loading…"}\n`}
              {`Agent guide: ${endpoints?.agent_guide_url || "Loading…"}`}
            </pre>
          </div>
        </Panel>
      </div>
    </div>
  );
}

