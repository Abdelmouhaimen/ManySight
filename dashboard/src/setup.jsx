/* Setup — Space, Cameras, Advanced.
 *
 * The old six tabs mixed physical configuration with worker internals, alert
 * rules and developer links. Alert rules now live in Review; everything a
 * person only touches occasionally is behind Advanced; and the two things they
 * really do here — describe the space, and get the cameras ready — each own a
 * tab.
 */
import { useEffect, useState } from "react";
import { ExternalLink, Plus, RefreshCw, Save, Trash2 } from "lucide-react";
import { api, apiKey, demoSessionId, formatDateTime } from "./api.js";
import { routeHref } from "./routes.js";
import {
  calibrationStatus, combinedTrackingStatus, countLabel, dataHealth, placementStatus,
  runtimeStatus, trustworthyCount,
} from "./status.js";
import { onTourEvent } from "./demo-tour.jsx";
import { CameraDetail, SpaceEditor } from "./space-workbench.jsx";
import { SourceEditorModal } from "./sources.jsx";
import {
  Collapsible, DefinitionList, EmptyState, ErrorState, LoadingState, Modal, OverflowMenu,
  PageHeader, Panel, StatusPill, SubNav, TechnicalDetails,
} from "./ui.jsx";

const TABS = [["space", "Space"], ["cameras", "Cameras"], ["advanced", "Advanced"]];

export function SetupPage({ subview = "space", notify, refreshShell }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const load = async () => {
    try {
      const [store, zones, sources, zoneViews, groups] = await Promise.all([
        api.get("/store"), api.get("/zones"), api.get("/sources"),
        api.get("/zone-views"), api.get("/multiview/groups"),
      ]);
      setData({ store, zones, sources, zoneViews, groups });
      setError(null);
    } catch (err) { setError(err); } finally { setLoading(false); }
  };
  useEffect(() => { load(); }, []); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => onTourEvent((detail) => {
    // The guided demo creates and restores real geometry behind this view.
    if (detail.kind === "workspace-changed") { load(); refreshShell?.(); }
  }), []); // eslint-disable-line react-hooks/exhaustive-deps
  // The guided demo asks for the area it is teaching in.
  useEffect(() => {
    const onRequestedTab = (event) => {
      const requested = event.detail?.tab;
      if (requested) window.location.hash = `setup/${requested}`;
    };
    window.addEventListener("storelens-setup-tab", onRequestedTab);
    return () => window.removeEventListener("storelens-setup-tab", onRequestedTab);
  }, []);

  const refresh = async () => { await load(); await refreshShell?.(); };

  if (loading && !data) return <LoadingState label="Loading setup…" />;
  if (error && !data) return <ErrorState error={error} retry={load} />;
  return (
    <>
      <PageHeader title="Setup" />
      <SubNav
        ariaLabel="Setup sections"
        items={TABS}
        active={subview}
        onSelect={(value) => { window.location.hash = `setup/${value}`; }}
      />
      {subview === "cameras" && (
        <CamerasTab data={data} onRefresh={refresh} notify={notify} />
      )}
      {subview === "advanced" && (
        <AdvancedTab store={data.store} onRefresh={refresh} notify={notify} />
      )}
      {subview === "space" && (
        <SpaceEditor
          store={data.store}
          zones={data.zones}
          sources={data.sources}
          zoneViews={data.zoneViews}
          onRefresh={refresh}
          notify={notify}
        />
      )}
    </>
  );
}

/* --------------------------------------------------------------- cameras */

function CamerasTab({ data, onRefresh, notify }) {
  const { store, zones, sources, groups } = data;
  // The first camera is selected so its controls — including the calibration
  // button the guided walkthrough points at — are on screen straight away.
  const [selectedId, setSelectedId] = useState(sources[0]?.id ?? null);
  const [adding, setAdding] = useState(false);
  const [configuring, setConfiguring] = useState(false);
  const selected = sources.find((source) => source.id === selectedId) || null;
  const group = groups.find((item) => item.enabled) || groups[0] || null;
  const combined = combinedTrackingStatus(group, sources);

  if (!sources.length) {
    return (
      <>
        <EmptyState
          title="No cameras yet"
          action={
            <button className="button button-primary" onClick={() => setAdding(true)}>
              Add a camera
            </button>
          }
        >
          Add a camera, place it on the floor map, then calibrate it.
        </EmptyState>
        {adding && (
          <SourceEditorModal
            onClose={() => setAdding(false)}
            onSaved={async (source) => {
              setAdding(false);
              await onRefresh();
              notify("Camera added", source.name);
            }}
          />
        )}
      </>
    );
  }

  return (
    <div className="stack">
      <Panel className="table-panel">
        <div className="table-scroll">
          <table className="record-table">
            <thead>
              <tr>
                <th>Camera</th><th>Data</th><th>Placement</th><th>Calibration</th>
                <th>Combined tracking</th><th><span className="sr-only">Actions</span></th>
              </tr>
            </thead>
            <tbody>
              {sources.map((source) => {
                const included = (group?.source_ids || []).includes(source.id);
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
                    <td><StatusPill status={dataHealth(source)} compact /></td>
                    <td><StatusPill status={placementStatus(source)} compact /></td>
                    <td><StatusPill status={calibrationStatus(source)} compact /></td>
                    <td>{included ? "Included" : <span className="muted">Not included</span>}</td>
                    <td onClick={(event) => event.stopPropagation()}>
                      <OverflowMenu
                        label={`Actions for ${source.name}`}
                        items={[
                          { label: "Open", onSelect: () => setSelectedId(source.id) },
                          { label: "Edit connection", onSelect: () => setAdding(source) },
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

      <Panel
        title="Combined tracking"
        action={
          <button className="button button-secondary" onClick={() => setConfiguring(true)}>
            Configure
          </button>
        }
      >
        <div className="combined-summary">
          <StatusPill status={combined} />
          <p>
            {group
              ? `${countLabel((group.source_ids || []).length, "camera")} combined, so a person `
                + "seen by two cameras is counted once."
              : "Cameras are counted separately. Combine the ones whose views overlap so a "
                + "person is not counted twice."}
          </p>
        </div>
        {group && (
          <ul className="combined-members">
            {(group.source_ids || []).map((id) => (
              <li key={id}>{sources.find((source) => source.id === id)?.name || `Camera ${id}`}</li>
            ))}
          </ul>
        )}
        {group && (
          <TechnicalDetails>
            <DefinitionList
              rows={[
                ["Group", group.name],
                ["Internal name", "multiview group"],
                ["Time tolerance", `${group.time_tolerance_s} s`],
                ["Spatial gate", `${group.spatial_gate_m} m`],
                ["Track age", `${group.track_age_s} s`],
                ["Algorithm", `${group.algorithm} v${group.algorithm_version}`],
              ]}
            />
          </TechnicalDetails>
        )}
      </Panel>

      {selected && (
        <Panel title={selected.name} className="camera-detail-panel">
          <CameraDetail
            source={selected}
            store={store}
            zones={zones}
            sources={sources}
            onRefresh={onRefresh}
            notify={notify}
          />
        </Panel>
      )}
      {configuring && (
        <CombinedTrackingModal
          group={group}
          sources={sources}
          onClose={() => setConfiguring(false)}
          onSaved={async () => {
            setConfiguring(false);
            await onRefresh();
            notify("Combined tracking updated");
          }}
        />
      )}
      {adding && (
        <SourceEditorModal
          source={adding === true ? null : adding}
          onClose={() => setAdding(false)}
          onSaved={async (source) => {
            setAdding(false);
            await onRefresh();
            notify("Camera saved", source.name);
          }}
        />
      )}
    </div>
  );
}

/**
 * The one API-only capability the audit found: Live defaults to combined data,
 * but nothing in the UI could create or inspect the group behind it. This is
 * deliberately the smallest editor that answers "which cameras work together,
 * and is that ready?" — the fusion gates stay in Technical details.
 */
function CombinedTrackingModal({ group, sources, onClose, onSaved }) {
  const [name, setName] = useState(group?.name || "Combined cameras");
  const [selected, setSelected] = useState(() => new Set(group?.source_ids || []));
  const [enabled, setEnabled] = useState(group ? Boolean(group.enabled) : true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  const toggle = (id) => setSelected((current) => {
    const next = new Set(current);
    if (next.has(id)) next.delete(id); else next.add(id);
    return next;
  });

  const save = async () => {
    setSaving(true);
    setError("");
    try {
      const source_ids = [...selected];
      if (source_ids.length < 2) throw new Error("Choose at least two cameras.");
      const uncalibrated = sources.filter(
        (source) => source_ids.includes(source.id) && !source.calibrated);
      if (uncalibrated.length) {
        throw new Error(
          `Calibrate ${uncalibrated.map((source) => source.name).join(", ")} first — `
          + "cameras can only be combined once they share the floor map.",
        );
      }
      if (group) await api.patch(`/multiview/groups/${group.id}`, { name, source_ids, enabled });
      else await api.post("/multiview/groups", { name, source_ids, enabled });
      await onSaved();
    } catch (err) { setError(err.message); } finally { setSaving(false); }
  };

  return (
    <Modal
      title="Combined tracking"
      description="Choose the cameras whose views overlap. ManySight matches the same person across them."
      onClose={onClose}
      footer={
        <>
          <button className="button button-secondary" onClick={onClose}>Cancel</button>
          <button className="button button-primary" onClick={save} disabled={saving}>
            <Save size={14} aria-hidden="true" /> {saving ? "Saving…" : "Save"}
          </button>
        </>
      }
    >
      <label className="field field-full">
        <span>Name</span>
        <input value={name} onChange={(event) => setName(event.target.value)} />
      </label>
      <fieldset className="check-list">
        <legend>Cameras</legend>
        {sources.map((source) => (
          <label key={source.id} className="check-field">
            <input
              type="checkbox"
              checked={selected.has(source.id)}
              onChange={() => toggle(source.id)}
              disabled={!source.calibrated}
            />
            {source.name}
            {!source.calibrated && <span className="muted"> · needs calibration first</span>}
          </label>
        ))}
      </fieldset>
      <label className="check-field">
        <input type="checkbox" checked={enabled} onChange={(event) => setEnabled(event.target.checked)} />
        Combine these cameras now
      </label>
      {error && <div className="form-error" role="alert">{error}</div>}
    </Modal>
  );
}

/* -------------------------------------------------------------- advanced */

function AdvancedTab({ store, onRefresh, notify }) {
  return (
    <div className="stack">
      <WorkspaceSection store={store} onRefresh={onRefresh} notify={notify} />
      <WorkersSection notify={notify} />
      <DeveloperSection notify={notify} />
      <DangerZone onReset={onRefresh} notify={notify} />
    </div>
  );
}

function WorkspaceSection({ store, onRefresh, notify }) {
  const [name, setName] = useState(store?.name || "");
  const [saving, setSaving] = useState(false);
  const traced = Boolean(store?.map?.floor_polygons?.length);
  const save = async () => {
    setSaving(true);
    try {
      await api.put("/store", { name });
      await onRefresh();
      notify("Workspace renamed", name);
    } catch (err) { notify("Couldn't save", err.message, "error"); } finally { setSaving(false); }
  };
  return (
    <Collapsible title="Workspace">
      <label className="field field-full">
        <span>Name</span>
        <input value={name} onChange={(event) => setName(event.target.value)} />
      </label>
      <div className="status-row">
        <span>Floor size</span>
        <strong>
          {Number(store?.width_m || 0).toFixed(1)} × {Number(store?.height_m || 0).toFixed(1)} m
        </strong>
      </div>
      {/* One source of truth: the traced plan sets the metric size, so there is
          no second form here that could quietly disagree with it. */}
      <p className="form-note">
        {traced
          ? "Set by the floor plan you traced. Digitize the plan again in Space to change it."
          : "Set when you digitize a floor plan in Space."}
      </p>
      <div className="panel-footer">
        <button className="button button-primary" onClick={save} disabled={saving || !name.trim()}>
          <Save size={14} aria-hidden="true" /> {saving ? "Saving…" : "Save"}
        </button>
      </div>
    </Collapsible>
  );
}

function WorkersSection({ notify }) {
  const [jobs, setJobs] = useState([]);
  const [loading, setLoading] = useState(true);
  const load = async () => {
    try { setJobs(await api.get("/jobs")); } catch { /* surfaced below */ } finally { setLoading(false); }
  };
  useEffect(() => { load(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const command = async (worker, desiredState) => {
    try {
      await api.put(`/workers/${worker.id}/desired-state`, { desired_state: desiredState });
      await load();
      notify?.(desiredState === "stopped" ? "Stop requested" : "Restart requested",
               "The worker applies this on its next heartbeat.");
    } catch (err) { notify?.("Couldn't send the request", err.message, "error"); }
  };
  const remove = async (job) => {
    if (!window.confirm(`Delete "${job.name}"? Its observations are kept.`)) return;
    try { await api.del(`/jobs/${job.id}`); await load(); }
    catch (err) { notify?.("Couldn't delete it", err.message, "error"); }
  };

  return (
    <Collapsible title="Workers" summaryExtra={
      jobs.length ? <span className="collapsible-count">{jobs.length}</span> : null
    }>
      {loading ? <LoadingState label="Loading workers…" /> : !jobs.length ? (
        <EmptyState title="No workers yet">
          A worker runs on your own machine, reads a camera and sends what it sees.
        </EmptyState>
      ) : (
        <div className="record-list">
          {jobs.map((job) => {
            const worker = job.latest_worker;
            const status = runtimeStatus(worker);
            return (
              <div className="record-list-row" key={job.id}>
                <div className="record-copy">
                  <strong>{job.name}</strong>
                  <small>
                    {worker
                      ? `Last heartbeat ${formatDateTime(worker.last_heartbeat_at)}`
                      : "No worker has connected"}
                  </small>
                </div>
                <StatusPill status={status} compact />
                <OverflowMenu
                  label={`Actions for ${job.name}`}
                  items={[
                    worker && status.label === "Running"
                      ? { label: "Ask it to stop", onSelect: () => command(worker, "stopped") }
                      : worker
                        ? { label: "Ask it to restart", onSelect: () => command(worker, "restart") }
                        : null,
                    { label: "Delete", destructive: true, onSelect: () => remove(job) },
                  ]}
                />
              </div>
            );
          })}
        </div>
      )}
      {jobs.length > 0 && (
        <TechnicalDetails>
          <DefinitionList
            rows={jobs.map((job) => [
              job.name,
              /* jobs.event_count only rises for submissions that carry a job_id,
                 so a running worker can legitimately read zero. Show a dash
                 rather than a number we cannot stand behind. */
              `${trustworthyCount(job.event_count, { hasRuntime: Boolean(job.latest_worker) })} `
              + `observations attributed · worker ${job.latest_worker?.worker_id || "—"}`,
            ])}
          />
          <p className="technical-note">
            Counts only include observations submitted with a job reference, so a worker that
            omits one shows a dash.
          </p>
        </TechnicalDetails>
      )}
    </Collapsible>
  );
}

function DeveloperSection({ notify }) {
  const [key, setKey] = useState(apiKey());
  const [endpoints, setEndpoints] = useState(null);
  useEffect(() => {
    api.get("/platform-config").then(setEndpoints).catch(() => setEndpoints(null));
  }, []);
  const saveKey = () => {
    if (key) window.localStorage.setItem("storelens_api_key", key);
    else window.localStorage.removeItem("storelens_api_key");
    notify("API key saved", "Reloading to apply it.");
    window.location.reload();
  };
  return (
    <Collapsible title="Developer">
      <label className="field field-full">
        <span>API key</span>
        <input
          type="password"
          value={key}
          onChange={(event) => setKey(event.target.value)}
          placeholder="Only needed if this deployment requires one"
        />
      </label>
      <div className="panel-footer">
        <button className="button button-secondary" onClick={saveKey}>
          <Save size={14} aria-hidden="true" /> Save key
        </button>
      </div>
      <DefinitionList
        rows={[
          ["API documentation", (
            <a href={endpoints?.docs_url || "/docs"} target="_blank" rel="noreferrer">
              Open <ExternalLink size={12} aria-hidden="true" />
            </a>
          )],
          ["REST", <code key="rest">{endpoints?.rest_url || "…"}</code>],
          ["MCP", <code key="mcp">{endpoints?.mcp_url || "…"}</code>],
          ["Agent guide", <code key="guide">{endpoints?.agent_guide_url || "…"}</code>],
        ]}
      />
    </Collapsible>
  );
}

/* Reset cameras — the only reset that removes the cameras themselves, so it is
 * the only one that shows the user what will go before asking them to confirm.
 * The preview is a real server dry run, not a guess assembled in the browser. */
function ResetCamerasBlock({ onReset, notify }) {
  const [impact, setImpact] = useState(null);
  const [confirmation, setConfirmation] = useState("");
  const [busy, setBusy] = useState(false);
  // Inside the guided demo every request is routed into the demo's own
  // workspace, so this would wipe the prepared demo instead of the user's
  // cameras. The server refuses it too; this just says so before they click.
  const [inDemo, setInDemo] = useState(Boolean(demoSessionId()));
  useEffect(() => {
    const sync = () => setInDemo(Boolean(demoSessionId()));
    window.addEventListener("storelens-demo-session", sync);
    return () => window.removeEventListener("storelens-demo-session", sync);
  }, []);

  const openPreview = async () => {
    setBusy(true);
    try {
      const preview = await api.post("/workspace/reset-cameras", { dry_run: true });
      setImpact(preview.impact);
      setConfirmation("");
    } catch (error) { notify?.("Couldn't check what would be removed", error.message, "error"); }
    finally { setBusy(false); }
  };

  const run = async () => {
    setBusy(true);
    try {
      const result = await api.post("/workspace/reset-cameras", {
        dry_run: false, confirmation, reset_token: impact.reset_token,
      });
      setImpact(null);
      setConfirmation("");
      const disabled = result.alert_rules_disabled?.length || 0;
      notify?.(
        result.removed.cameras === 1 ? "1 camera removed"
          : `${result.removed.cameras} cameras removed`,
        disabled
          ? `The floor plan and zones were kept. ${countLabel(disabled, "alert rule")} turned off.`
          : "The floor plan and your zones were kept.",
      );
      await onReset?.();
    } catch (error) { notify?.("That didn't work", error.message, "error"); }
    finally { setBusy(false); }
  };

  const rows = impact ? [
    ["Cameras", impact.cameras],
    ["Saved connections", impact.stored_credentials],
    ["Calibrations", impact.calibrations + impact.imported_calibrations],
    ["Camera views of zones", impact.zone_views],
    ["Camera observations", impact.observations],
    ["Combined tracking groups", impact.multiview_groups],
  ] : [];

  return (
    <div className="danger-block">
      <h3>Reset cameras</h3>
      <p>
        Remove all cameras and camera-specific setup so you can configure cameras again from
        scratch. Your floor plan and zones stay as they are.
      </p>
      <button className="button danger" disabled={busy || inDemo} onClick={openPreview}>
        <Trash2 size={14} aria-hidden="true" /> Reset cameras…
      </button>
      {inDemo && <p className="muted">Exit the demo before resetting your cameras.</p>}
      {impact && (
        <Modal
          title="Reset cameras"
          description="This cannot be undone."
          onClose={() => setImpact(null)}
          footer={(
            <>
              <button className="button button-secondary" onClick={() => setImpact(null)}>
                Cancel
              </button>
              <button className="button danger"
                      disabled={busy || confirmation !== "RESET CAMERAS" || !impact.cameras}
                      onClick={run}>
                <Trash2 size={14} aria-hidden="true" />
                {" "}
                {impact.cameras === 1 ? "Remove 1 camera" : `Remove ${impact.cameras} cameras`}
              </button>
            </>
          )}
        >
          {impact.cameras === 0 ? (
            <p>There are no cameras to remove.</p>
          ) : (
            <>
              <p><strong>This removes</strong></p>
              <DefinitionList rows={rows} />
              <p>
                Also removed: camera placements, projection surfaces, and the combined tracking
                history those cameras produced.
              </p>
              <p><strong>This keeps</strong> your floor plan, the size of the space, and your
                {" "}
                {countLabel(impact.preserved.canonical_zones, "zone")} — the zones lose their
                camera views until you set cameras up again.
              </p>
              {impact.alert_rules_to_disable?.length > 0 && (
                <p>
                  {countLabel(impact.alert_rules_to_disable.length, "alert rule")} will be turned
                  off because it relies on these cameras:{" "}
                  {impact.alert_rules_to_disable.map((rule) => rule.name).join(", ")}. The rule is
                  kept so you can point it at new cameras later.
                </p>
              )}
              {impact.workers_to_stop?.length > 0 && (
                <p>
                  {countLabel(impact.workers_to_stop.length, "detector")} will be asked to stop.
                  ManySight can ask, but cannot close a program it did not start.
                </p>
              )}
              <label className="field field-full">
                <span>Type RESET CAMERAS to confirm</span>
                <input value={confirmation}
                       onChange={(event) => setConfirmation(event.target.value)} />
              </label>
            </>
          )}
        </Modal>
      )}
    </div>
  );
}

function DangerZone({ onReset, notify }) {
  const [spaceConfirmation, setSpaceConfirmation] = useState("");
  const [observationConfirmation, setObservationConfirmation] = useState("");
  const [history, setHistory] = useState("keep");
  const [busy, setBusy] = useState(false);
  const run = async (kind) => {
    setBusy(true);
    try {
      if (kind === "space") {
        await api.post("/workspace/reinitialize-space",
                       { confirmation: spaceConfirmation, history });
        setSpaceConfirmation("");
        notify?.("Space reinitialized", history === "keep"
          ? "Earlier observations stay attached to the previous version of the space."
          : "Geometry and observation history were removed.");
      } else {
        await api.post("/workspace/reinitialize-observations",
                       { confirmation: observationConfirmation });
        setObservationConfirmation("");
        notify?.("Observations cleared", "Cameras, geometry and rules were kept.");
      }
      await onReset?.();
    } catch (error) { notify?.("That didn't work", error.message, "error"); }
    finally { setBusy(false); }
  };
  return (
    <Collapsible title="Danger zone">
      <div className="danger-block">
        <h3>Start the space again</h3>
        <p>
          Clears camera placements, calibrations, zones and combined tracking. Cameras and their
          sign-in details are kept.
        </p>
        <label className="field">
          <span>Existing observations</span>
          <select value={history} onChange={(event) => setHistory(event.target.value)}>
            <option value="keep">Keep them against the old space</option>
            <option value="delete">Delete them</option>
          </select>
        </label>
        <label className="field field-full">
          <span>Type REINITIALIZE SPACE to confirm</span>
          <input value={spaceConfirmation}
                 onChange={(event) => setSpaceConfirmation(event.target.value)} />
        </label>
        <button className="button danger" disabled={busy || spaceConfirmation !== "REINITIALIZE SPACE"}
                onClick={() => run("space")}>
          <Trash2 size={14} aria-hidden="true" /> Start the space again
        </button>
      </div>
      <div className="danger-block">
        <h3>Clear all observations</h3>
        <p>
          Deletes observations, current and combined state, occupancy history and alerts already
          raised. Cameras, geometry, saved results, dashboards and rules are kept.
        </p>
        <label className="field field-full">
          <span>Type REINITIALIZE OBSERVATIONS to confirm</span>
          <input value={observationConfirmation}
                 onChange={(event) => setObservationConfirmation(event.target.value)} />
        </label>
        <button className="button danger"
                disabled={busy || observationConfirmation !== "REINITIALIZE OBSERVATIONS"}
                onClick={() => run("observations")}>
          <Trash2 size={14} aria-hidden="true" /> Clear all observations
        </button>
      </div>
      <ResetCamerasBlock onReset={onReset} notify={notify} />
    </Collapsible>
  );
}
