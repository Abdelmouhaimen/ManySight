/* Sources — where data comes from, and whether it is working.
 *
 * A compact list answers that question at a glance; everything else (preview,
 * connection internals, worker identifiers, capabilities) moved into the detail
 * drawer, so four cameras no longer mean four embedded preview forms.
 */
import { useEffect, useState } from "react";
import { ExternalLink, Plus, RefreshCw, Save } from "lucide-react";
import { api, formatDateTime, formatDuration } from "./api.js";
import { routeHref } from "./routes.js";
import {
  calibrationStatus, dataHealth, placementStatus, runtimeStatus, setupStatus, trustworthyCount,
} from "./status.js";
import {
  Collapsible, DefinitionList, Drawer, EmptyState, ErrorState, LoadingState, Modal, OverflowMenu,
  PageHeader, Panel, StatusPill,
} from "./ui.jsx";

const FILTERS = [
  ["all", "All", () => true],
  ["live", "Live", (source) => dataHealth(source).label === "Live"],
  ["stale", "Stale", (source) => dataHealth(source).label === "Stale"],
  ["no-data", "No data", (source) => dataHealth(source).label === "No data"],
  ["needs-setup", "Needs setup", (source) => setupStatus(source).label === "Needs setup"],
];

const KIND_LABELS = {
  rtsp: "RTSP camera", http: "HTTP / MJPEG camera", webrtc: "WebRTC camera",
  webcam: "Webcam", file: "Video file", sensor: "Sensor", custom: "Custom",
};
const kindLabel = (kind) => KIND_LABELS[kind] || "Source";

export function SourcesPage({ liveTick = 0, notify }) {
  const [sources, setSources] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [filter, setFilter] = useState("all");
  const [selectedId, setSelectedId] = useState(null);
  const [editing, setEditing] = useState(null);      // source object, or "new"

  const load = async () => {
    try {
      setSources(await api.get("/sources"));
      setError(null);
    } catch (err) { setError(err); } finally { setLoading(false); }
  };
  useEffect(() => { load(); }, [liveTick]); // eslint-disable-line react-hooks/exhaustive-deps

  const visible = sources.filter(FILTERS.find(([value]) => value === filter)[2]);
  const selected = sources.find((source) => source.id === selectedId) || null;

  const remove = async (source) => {
    if (!window.confirm(
      `Delete ${source.name}? Its placement, calibration and zone views are removed. `
      + "Observations it already produced are kept.",
    )) return;
    try {
      await api.del(`/sources/${source.id}`);
      window.localStorage.removeItem(`storelens.local-preview.${source.id}`);
      setSelectedId(null);
      await load();
      notify?.("Source deleted", source.name);
    } catch (err) { notify?.("Couldn't delete the source", err.message, "error"); }
  };

  return (
    <>
      <PageHeader
        title="Sources"
        actions={
          <>
            <button className="icon-button" onClick={load} aria-label="Refresh sources">
              <RefreshCw size={16} />
            </button>
            <button className="button button-primary" onClick={() => setEditing("new")}>
              <Plus size={15} aria-hidden="true" /> Add source
            </button>
          </>
        }
      />
      {sources.length > 0 && (
        <div className="filter-row">
          {FILTERS.map(([value, label, match]) => (
            <button
              key={value}
              className={filter === value ? "active" : ""}
              aria-pressed={filter === value}
              onClick={() => setFilter(value)}
            >
              {label}
              <span>{sources.filter(match).length}</span>
            </button>
          ))}
        </div>
      )}
      {loading ? <LoadingState label="Loading sources…" />
        : error ? <ErrorState error={error} retry={load} />
          : !sources.length ? (
            <EmptyState
              title="No sources yet"
              action={
                <button className="button button-primary" onClick={() => setEditing("new")}>
                  Add source
                </button>
              }
            >
              A source is a camera, stream or file that a local worker reads from.
            </EmptyState>
          ) : !visible.length ? (
            <EmptyState tone="no-data" title="No sources match this filter"
              action={
                <button className="button button-secondary" onClick={() => setFilter("all")}>
                  Clear filters
                </button>
              } />
          ) : (
            <Panel className="table-panel">
              <div className="table-scroll">
                <table className="record-table">
                  <thead>
                    <tr>
                      <th>Name</th><th>Type</th><th>Data</th><th>Perception</th><th>Setup</th>
                      <th><span className="sr-only">Actions</span></th>
                    </tr>
                  </thead>
                  <tbody>
                    {visible.map((source) => {
                      const worker = source.latest_runtime?.worker;
                      const replay = source.metadata?.producer_kind === "replay";
                      return (
                        <tr
                          key={source.id}
                          className="record-row"
                          tabIndex={0}
                          role="button"
                          aria-label={`Open ${source.name}`}
                          onClick={() => setSelectedId(source.id)}
                          onKeyDown={(event) => {
                            if (event.key === "Enter" || event.key === " ") {
                              event.preventDefault();
                              setSelectedId(source.id);
                            }
                          }}
                        >
                          <td className="record-name">{source.name}</td>
                          <td>{kindLabel(source.kind)}</td>
                          <td><StatusPill status={dataHealth(source)} compact /></td>
                          <td>
                            {replay
                              ? <span className="muted">Recorded replay</span>
                              : <StatusPill status={runtimeStatus(worker)} compact />}
                          </td>
                          <td><StatusPill status={setupStatus(source)} compact /></td>
                          <td onClick={(event) => event.stopPropagation()}>
                            <OverflowMenu
                              label={`Actions for ${source.name}`}
                              items={[
                                { label: "View details", onSelect: () => setSelectedId(source.id) },
                                { label: "Edit source", onSelect: () => setEditing(source) },
                                { label: "Delete", destructive: true, onSelect: () => remove(source) },
                              ]}
                            />
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </Panel>
          )}

      {selected && (
        <SourceDrawer
          source={selected}
          onClose={() => setSelectedId(null)}
          onEdit={() => setEditing(selected)}
          onDelete={() => remove(selected)}
        />
      )}
      {editing && (
        <SourceEditorModal
          source={editing === "new" ? null : editing}
          onClose={() => setEditing(null)}
          onSaved={async (source) => {
            setEditing(null);
            await load();
            notify?.(editing === "new" ? "Source added" : "Source updated", source.name);
          }}
        />
      )}
    </>
  );
}

/* ---------------------------------------------------------------- detail */

function SourceDrawer({ source, onClose, onEdit, onDelete }) {
  const [previewOpen, setPreviewOpen] = useState(false);
  const worker = source.latest_runtime?.worker;
  const health = dataHealth(source);
  return (
    <Drawer
      title={source.name}
      onClose={onClose}
      footer={
        <>
          <button className="button button-secondary" onClick={onEdit}>Edit source</button>
          <a className="button button-secondary" href={routeHref("observations")}>View observations</a>
          <button className="button button-ghost destructive" onClick={onDelete}>Delete</button>
        </>
      }
    >
      <section className="drawer-section">
        <h3>Data</h3>
        <StatusPill status={health} />
        <p className="drawer-fact">
          {source.observation_age_s == null
            ? "Nothing received yet."
            : `Last received ${formatDuration(source.observation_age_s)} ago · `
              + `${trustworthyCount(source.event_count)} observations stored`}
        </p>
      </section>
      <section className="drawer-section">
        <h3>Perception</h3>
        {source.metadata?.producer_kind === "replay" ? (
          <p className="drawer-fact">Recorded demo replay — no worker is running.</p>
        ) : (
          <>
            <StatusPill status={runtimeStatus(worker)} />
            <p className="drawer-fact">
              {worker
                ? `${source.latest_runtime?.job_name || "Perception"} · last heartbeat `
                  + formatDateTime(worker.last_heartbeat_at)
                : "No worker has registered for this source."}
            </p>
          </>
        )}
      </section>
      <section className="drawer-section">
        <h3>Setup</h3>
        <div className="status-row">
          <span>Placement</span><StatusPill status={placementStatus(source)} compact />
        </div>
        <div className="status-row">
          <span>Calibration</span><StatusPill status={calibrationStatus(source)} compact />
        </div>
        <a className="text-link" href={routeHref("setup", "cameras")}>Open camera setup</a>
      </section>
      <section className="drawer-section">
        <h3>Connection</h3>
        <p className="drawer-fact">{kindLabel(source.kind)}</p>
        <button className="button button-secondary" onClick={() => setPreviewOpen(true)}>
          Preview
        </button>
      </section>
      <Collapsible title="Advanced">
        <DefinitionList
          rows={[
            ["Source ID", `#${source.id}`],
            ["Connection mode", (source.connection_mode || "").replaceAll("_", " ")],
            ["Credentials", source.credential_status?.configured ? "Stored" : "None stored"],
            ["Capabilities", (source.capabilities || []).join(", ") || "—"],
            ["Calibration revision", source.calibration_revision ?? "—"],
            worker ? ["Worker ID", worker.worker_id] : null,
            worker ? ["Reported state", worker.effective_status] : null,
          ]}
        />
      </Collapsible>
      {previewOpen && <PreviewModal source={source} onClose={() => setPreviewOpen(false)} />}
    </Drawer>
  );
}

/** The worker-local preview. One dialog, opened on demand, not a form per row. */
function PreviewModal({ source, onClose }) {
  const storageKey = `storelens.local-preview.${source.id}`;
  const [address, setAddress] = useState(() => window.localStorage.getItem(storageKey) || "");
  const [connected, setConnected] = useState(() => window.localStorage.getItem(storageKey) || "");
  const connect = () => {
    const value = address.trim();
    window.localStorage.setItem(storageKey, value);
    setConnected(value);
  };
  return (
    <Modal
      title={`Preview ${source.name}`}
      description="Point this at a player running on your own machine. ManySight never receives the address or the video."
      onClose={onClose}
      footer={<button className="button button-secondary" onClick={onClose}>Close</button>}
    >
      <label className="field">
        <span>Local player address</span>
        <input
          value={address}
          onChange={(event) => setAddress(event.target.value)}
          placeholder="http://127.0.0.1:8765/stream.mjpg"
        />
      </label>
      <div className="card-actions">
        <button className="button button-secondary" onClick={connect} disabled={!address.trim()}>
          Connect
        </button>
        {address.trim() && (
          <a className="button button-ghost" href={address.trim()} target="_blank" rel="noreferrer">
            <ExternalLink size={14} aria-hidden="true" /> Open in a new tab
          </a>
        )}
      </div>
      {connected && (
        <iframe
          className="local-preview-frame"
          src={connected}
          title={`Preview of ${source.name}`}
          allow="autoplay; fullscreen"
        />
      )}
    </Modal>
  );
}

/* ----------------------------------------------------------------- editor */

export function SourceEditorModal({ source = null, onClose, onSaved }) {
  const existing = source?.connection || {};
  const [form, setForm] = useState({
    name: source?.name || "",
    kind: source?.kind || "http",
    connection_management: source?.connection_management || "storelens_managed",
    local_secret_ref: source?.locator?.local_secret_ref || "",
    device_index: existing.device_index ?? 0,
    host: existing.host || "", port: existing.port ?? 554,
    path: existing.path || "/live", transport: existing.transport || "tcp",
    url: existing.url || "", auth_type: existing.auth_type || "none",
    file_path: source?.kind === "file" ? existing.path || "" : "",
    username: "", password: "", clear_credentials: false,
  });
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const set = (patch) => setForm((current) => ({ ...current, ...patch }));
  const managed = form.connection_management === "storelens_managed";

  const save = async () => {
    setSaving(true);
    setError("");
    try {
      if (!form.name.trim()) throw new Error("Give the source a name.");
      if (!managed && !form.local_secret_ref.trim()) {
        throw new Error("Name the secret your worker will read.");
      }
      let connection = {};
      if (managed && form.kind === "webcam") connection = { device_index: Number(form.device_index) };
      if (managed && form.kind === "rtsp") {
        connection = { host: form.host.trim(), port: Number(form.port),
                       path: form.path.trim(), transport: form.transport };
      }
      if (managed && form.kind === "http") connection = { url: form.url.trim(), auth_type: form.auth_type };
      if (managed && form.kind === "file") connection = { path: form.file_path.trim() };
      const replacing = form.username.length > 0 || form.password.length > 0;
      if (replacing && (!form.username || !form.password)) {
        throw new Error("Enter both a username and a password to replace the saved ones.");
      }
      const payload = {
        name: form.name.trim(), kind: form.kind, connection_mode: "agent_local",
        connection_management: form.connection_management, connection,
        locator: managed ? {} : { local_secret_ref: form.local_secret_ref.trim() },
        capabilities: ["video"],
      };
      if (replacing) payload.credentials = { username: form.username, password: form.password };
      if (source && form.clear_credentials) payload.clear_credentials = true;
      const saved = source
        ? await api.put(`/sources/${source.id}`, payload)
        : await api.post("/sources", payload);
      await onSaved(saved);
    } catch (err) { setError(err.message); } finally { setSaving(false); }
  };

  return (
    <Modal
      title={source ? "Edit source" : "Add source"}
      onClose={onClose}
      footer={
        <>
          <button className="button button-secondary" onClick={onClose}>Cancel</button>
          <button className="button button-primary" onClick={save} disabled={saving}>
            <Save size={14} aria-hidden="true" />
            {saving ? "Saving…" : source ? "Save" : "Add source"}
          </button>
        </>
      }
    >
      <div className="form-grid">
        <label className="field field-full">
          <span>Name</span>
          <input autoFocus value={form.name} placeholder="Camera 1"
                 onChange={(event) => set({ name: event.target.value })} />
        </label>
        <label className="field">
          <span>Type</span>
          <select
            value={form.kind}
            onChange={(event) => set({
              kind: event.target.value,
              connection_management: event.target.value === "webrtc"
                ? "external_secret" : form.connection_management,
            })}
          >
            <option value="http">HTTP / MJPEG</option>
            <option value="rtsp">RTSP</option>
            <option value="webrtc">WebRTC</option>
            <option value="webcam">Webcam</option>
            <option value="file">Video file</option>
          </select>
        </label>
        <label className="field">
          <span>Sign-in details</span>
          <select value={form.connection_management}
                  onChange={(event) => set({ connection_management: event.target.value })}>
            <option value="storelens_managed" disabled={form.kind === "webrtc"}>
              Store them here, encrypted
            </option>
            <option value="external_secret">Keep them on the worker machine</option>
          </select>
        </label>

        {!managed && (
          <label className="field field-full">
            <span>Secret name on the worker</span>
            <input value={form.local_secret_ref} placeholder="CAMERA_STREAM_URL"
                   onChange={(event) => set({ local_secret_ref: event.target.value })} />
          </label>
        )}
        {managed && form.kind === "webcam" && (
          <label className="field field-full">
            <span>Device number</span>
            <input type="number" min="0" value={form.device_index}
                   onChange={(event) => set({ device_index: event.target.value })} />
          </label>
        )}
        {managed && form.kind === "rtsp" && (
          <>
            <label className="field"><span>Address</span>
              <input value={form.host} placeholder="192.168.1.20"
                     onChange={(event) => set({ host: event.target.value })} /></label>
            <label className="field"><span>Port</span>
              <input type="number" min="1" max="65535" value={form.port}
                     onChange={(event) => set({ port: event.target.value })} /></label>
            <label className="field"><span>Path</span>
              <input value={form.path} placeholder="/live"
                     onChange={(event) => set({ path: event.target.value })} /></label>
            <label className="field"><span>Transport</span>
              <select value={form.transport} onChange={(event) => set({ transport: event.target.value })}>
                <option value="tcp">TCP</option><option value="udp">UDP</option>
              </select></label>
          </>
        )}
        {managed && form.kind === "http" && (
          <>
            <label className="field field-full"><span>Stream URL</span>
              <input value={form.url} placeholder="http://camera.local/stream.mjpg"
                     onChange={(event) => set({ url: event.target.value })} /></label>
            <label className="field"><span>Sign-in</span>
              <select value={form.auth_type} onChange={(event) => set({ auth_type: event.target.value })}>
                <option value="none">None</option><option value="basic">Username and password</option>
              </select></label>
          </>
        )}
        {managed && form.kind === "file" && (
          <label className="field field-full"><span>File path on the worker machine</span>
            <input value={form.file_path}
                   onChange={(event) => set({ file_path: event.target.value })} /></label>
        )}
        {managed && ["rtsp", "http"].includes(form.kind)
          && (form.kind !== "http" || form.auth_type === "basic") && (
          <>
            <label className="field"><span>Username{source ? " (new)" : ""}</span>
              <input autoComplete="off" value={form.username}
                     onChange={(event) => set({ username: event.target.value })} /></label>
            <label className="field"><span>Password{source ? " (new)" : ""}</span>
              <input type="password" autoComplete="new-password" value={form.password}
                     onChange={(event) => set({ password: event.target.value })} /></label>
          </>
        )}
        {managed && source?.credential_status?.configured && (
          <>
            <p className="field field-full form-note">
              Sign-in details are saved. Leave the fields above blank to keep them.
            </p>
            <label className="check-field field-full">
              <input type="checkbox" checked={form.clear_credentials}
                     onChange={(event) => set({ clear_credentials: event.target.checked })} />
              Remove the saved sign-in details
            </label>
          </>
        )}
      </div>
      <p className="form-note">
        ManySight never opens the camera itself — a worker on your network does, using these
        details.
      </p>
      {error && <div className="form-error" role="alert">{error}</div>}
    </Modal>
  );
}
