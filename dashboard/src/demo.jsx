import { useEffect, useRef, useState } from "react";
import {
  AlertTriangle, CheckCircle2, ExternalLink, Pause, Play,
  RotateCcw, Save, Trash2,
} from "lucide-react";
import { api, assetUrl, demoSessionId, setDemoSessionId } from "./api.js";
import { ErrorState, LoadingState, PageHeader } from "./components.jsx";

const CAMERA_KEYS = [1, 2, 3, 4].map((value) => `Warehouse_Synthetic_Cam${String(value).padStart(3, "0")}`);

export function DemoPage({ refreshShell }) {
  const [assets, setAssets] = useState(null);
  const [session, setSession] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const [occupancy, setOccupancy] = useState(null);
  const [fused, setFused] = useState(null);
  const [alerts, setAlerts] = useState([]);
  const [exitOpen, setExitOpen] = useState(false);
  const [includeObservations, setIncludeObservations] = useState(false);
  const videos = useRef({});

  const load = async () => {
    try {
      const assetState = await api.get("/demo/assets");
      setAssets(assetState);
      const id = demoSessionId();
      if (id) setSession(await api.get(`/demo/sessions/${id}`));
      setError(null);
    } catch (nextError) {
      if (demoSessionId()) setDemoSessionId("");
      setError(nextError);
    }
  };

  useEffect(() => { load(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!session?.id) return undefined;
    let cancelled = false;
    const refresh = async () => {
      try {
        const current = await api.get(`/demo/sessions/${session.id}`);
        if (cancelled) return;
        setSession(current);
        const queryId = current.result?.query_id;
        const [query, currentFused, signals] = await Promise.all([
          queryId ? api.post(`/queries/${queryId}/execute`) : Promise.resolve(null),
          api.get("/multiview/current?entity_type=person"),
          api.get("/alerts?limit=20"),
        ]);
        if (cancelled) return;
        setOccupancy(query?.rows?.[0] || null);
        setFused(currentFused);
        setAlerts(signals);
        Object.values(videos.current).forEach((video) => {
          if (!video || !Number.isFinite(video.duration)) return;
          const target = Math.min(current.playback_position_s, Math.max(0, video.duration - 0.05));
          if (Math.abs(video.currentTime - target) > 0.45) video.currentTime = target;
          if (current.status === "running" && video.paused) video.play().catch(() => {});
          if (current.status !== "running" && !video.paused) video.pause();
        });
      } catch (nextError) {
        if (!cancelled) setError(nextError);
      }
    };
    refresh();
    const timer = window.setInterval(refresh, 500);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, [session?.id]);

  const begin = async (mode = "guided") => {
    setBusy(true); setError(null);
    try {
      const created = await api.post("/demo/sessions", { mode });
      setDemoSessionId(created.id);
      const running = await api.post(`/demo/sessions/${created.id}/start`);
      setSession(running);
      await refreshShell?.();
    } catch (nextError) {
      setError(nextError);
    } finally { setBusy(false); }
  };

  const control = async (action) => {
    setBusy(true);
    try { setSession(await api.post(`/demo/sessions/${session.id}/${action}`)); }
    catch (nextError) { setError(nextError); }
    finally { setBusy(false); }
  };

  const openSpaceSetup = () => {
    localStorage.setItem("storelens.setup.tab", "space");
    window.location.hash = "setup";
  };

  const exit = async (promote) => {
    setBusy(true);
    try {
      const result = promote
        ? await api.post(`/demo/sessions/${session.id}/promote`, {
            include_recorded_observations: includeObservations,
          })
        : await api.post(`/demo/sessions/${session.id}/discard`);
      setDemoSessionId(""); setSession(null); setExitOpen(false);
      await refreshShell?.();
      window.location.hash = promote ? "setup" : "overview";
      return result;
    } catch (nextError) { setError(nextError); }
    finally { setBusy(false); }
  };

  if (!assets) return error ? <ErrorState error={error} retry={load} /> : <LoadingState label="Checking demo assets…" />;
  if (!session) return <>
    <PageHeader eyebrow="Playable walkthrough" title="Try StoreLens with four synchronized cameras"
      description="Replay precomputed YOLO11n + ByteTrack observations from NVIDIA’s synthetic warehouse sample through StoreLens’s real ingestion, geometry, multiview, query, dashboard, and alert paths. No GPU is used during replay." />
    <section className="demo-hero panel">
      <div>
        <span className="tiny-label">Observe locally, derive centrally</span>
        <h2>Alert when at least two fused people are in Aisle 04</h2>
        <p>StoreLens creates an isolated temporary workspace. Your normal sources, observations, geometry, dashboards, and alerts remain untouched.</p>
        <div className="demo-actions">
          <button className="button button-dark" disabled={busy || !assets.available} onClick={() => begin("guided")}><Play size={15} /> Start guided demo</button>
          <button className="button button-secondary" disabled={busy || !assets.available} onClick={() => begin("learn")}><ExternalLink size={15} /> Learn by exploring</button>
        </div>
      </div>
      <div className={`demo-asset-card ${assets.available ? "ready" : "missing"}`}>
        {assets.available ? <CheckCircle2 /> : <AlertTriangle />}
        <strong>{assets.available ? "Local sample media ready" : "Optional sample media required"}</strong>
        <p>StoreLens does not redistribute NVIDIA footage or model weights.</p>
        {!assets.available && <code>{assets.install_command}</code>}
      </div>
    </section>
    {error && <div className="inline-warning">{error.message}</div>}
  </>;

  const count = occupancy?.current_occupancy;
  const known = occupancy?.quality === "known";
  return <>
    <PageHeader eyebrow="Temporary demo workspace" title="Aisle 04 guided replay"
      description="The videos share one server-owned clock. Numerical detections are replayed progressively; future observations are never preloaded."
      actions={<button className="button button-secondary" onClick={() => setExitOpen(true)}>Exit demo</button>} />
    <div className="demo-status-strip">
      <span><i className={session.status === "running" ? "active" : ""} /> {session.status}</span>
      <span>Loop {session.playback_epoch + 1}</span>
      <span>{session.playback_position_s.toFixed(1)} / {session.duration_s.toFixed(1)} sec</span>
      <button onClick={() => control(session.status === "running" ? "pause" : "start")} disabled={busy}>
        {session.status === "running" ? <Pause size={13} /> : <Play size={13} />} {session.status === "running" ? "Pause" : "Play"}
      </button>
      <button onClick={() => control("restart")} disabled={busy}><RotateCcw size={13} /> Restart evidence</button>
    </div>
    <div className="demo-video-grid">
      {CAMERA_KEYS.map((key, index) => <figure key={key}>
        <video ref={(node) => { videos.current[key] = node; }} src={assetUrl(`/demo/media/${key}.mp4`)} muted playsInline preload="auto" />
        <figcaption><strong>Camera {index + 1}</strong><span>Direct synchronized MP4 · replay evidence</span></figcaption>
      </figure>)}
    </div>
    {session.mode === "learn" && <section className="panel demo-learning-path">
      <div className="panel-heading"><div><span className="tiny-label">Learn how it works</span><h2>Pixels become one physical map</h2><p>The validated demo map and all four calibrations are already loaded so replay remains reliable. Inspect the real tools and review one camera mapping before returning.</p></div></div>
      <ol>
        <li><strong>Digitize a floor plan</strong><span>Open Space &amp; zones and expand Digitize a floor plan. Upload and trace a plan, or keep the supplied demo map.</span></li>
        <li><strong>Review Camera 1 calibration</strong><span>Select Warehouse camera 1 and open Review calibration to inspect pixel-to-map control points and test projection.</span></li>
        <li><strong>Use validated remaining cameras</strong><span>The known NVIDIA matrices stay applied to cameras 2–4; replay never substitutes fabricated geometry.</span></li>
      </ol>
      <button className="button button-dark" onClick={openSpaceSetup}>Open real plan &amp; calibration tools <ExternalLink size={12} /></button>
    </section>}
    <div className="demo-outcome-grid">
      <section className={`panel demo-occupancy ${known && count >= 2 ? "threshold" : ""}`}>
        <span className="tiny-label">Real saved-query result</span>
        <strong>{count ?? "—"}</strong>
        <h2>Fused people in Aisle 04</h2>
        <p>Quality: {occupancy?.quality || "unknown"} · {fused?.entities?.length || 0} active fused tracks across the map.</p>
      </section>
      <section className="panel">
        <span className="tiny-label">Alert evaluation</span>
        <h2>{alerts.length ? "Threshold event recorded" : "Waiting for ≥ 2 people"}</h2>
        <p>{alerts[0]?.message || "The query-backed rule evaluates only known fused occupancy evidence."}</p>
        {alerts.length > 0 && <a className="text-link" href="#review">Open real review queue <ExternalLink size={12} /></a>}
      </section>
    </div>
    <section className="panel demo-timeline">
      <div className="panel-heading"><div><h2>What StoreLens actually did</h2><p>Results and identifiers below came from real domain operations—not a scripted success animation.</p></div></div>
      <ol>{session.action_log.map((item) => <li key={item.name}><CheckCircle2 size={16} /><div><strong>{item.name}</strong><p>{item.explanation}</p><code>{JSON.stringify(item.result)}</code></div></li>)}</ol>
    </section>
    <section className="panel demo-explore">
      <div><span className="tiny-label">Learn path</span><h2>Inspect and change the real workspace</h2><p>Use Setup for the integrated plan digitizer and calibration tools, then return here. Changes remain isolated until you explicitly promote setup.</p></div>
      <div>{[["setup","Plan & calibration"],["live","Live 3D"],["overview","Dashboard"],["observations","Evidence"],["sources","Sources"],["review","Alerts"]].map(([route,label]) => <a key={route} className="button button-secondary" href={`#${route}`} onClick={route === "setup" ? () => localStorage.setItem("storelens.setup.tab", "space") : undefined}>{label}<ExternalLink size={12} /></a>)}</div>
    </section>
    {error && <div className="inline-warning">{error.message}</div>}
    {exitOpen && <div className="modal-backdrop"><section className="modal demo-exit">
      <h2>Exit temporary demo workspace</h2>
      <p>Discard removes the isolated session. Keep setup transactionally copies only the mapped space, four sources, placements, calibrations, and multiview group.</p>
      <ul><li>Aisle 04 and camera zone views are not promoted.</li><li>The saved query, dashboard, alert rule, and fired alerts are not promoted.</li></ul>
      <label className="check-row"><input type="checkbox" checked={includeObservations} onChange={(event) => setIncludeObservations(event.target.checked)} /> Also copy replayed raw observations (off by default)</label>
      <div className="panel-footer"><button className="button button-ghost" onClick={() => setExitOpen(false)}>Cancel</button><button className="button danger" disabled={busy} onClick={() => exit(false)}><Trash2 size={14} /> Discard demo</button><button className="button button-dark" disabled={busy} onClick={() => exit(true)}><Save size={14} /> Keep camera & space setup</button></div>
    </section></div>}
  </>;
}
