import { useEffect, useRef, useState } from "react";
import {
  AlertTriangle, CheckCircle2, ExternalLink, Pause, Play, Save, Trash2,
} from "lucide-react";
import { api, assetUrl, demoSessionId, setDemoSessionId } from "./api.js";
import { ErrorState, LoadingState, PageHeader } from "./components.jsx";
import { fusedRuntimeIdForSourceTrack } from "./demo-replay-state.js";
import { trackColor } from "./live-colors.js";

const CAMERA_KEYS = [1, 2, 3, 4].map((value) =>
  `Warehouse_Synthetic_Cam${String(value).padStart(3, "0")}`);
function pointInPolygon(point, polygon) {
  if (!point || !polygon?.length) return false;
  const [x, y] = point;
  let inside = false;
  for (let i = 0, j = polygon.length - 1; i < polygon.length; j = i, i += 1) {
    const [xi, yi] = polygon[i]; const [xj, yj] = polygon[j];
    if (((yi > y) !== (yj > y)) && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) inside = !inside;
  }
  return inside;
}

function VideoEvidenceOverlay({ definition, frame, fusedEntities }) {
  if (!definition) return null;
  const width = definition.frame_width || 1920; const height = definition.frame_height || 1080;
  const detections = frame?.detections || [];
  const zoneActive = definition.zones?.some((zone) => detections.some((detection) =>
    (zone.polygons_px || []).some((polygon) => pointInPolygon(detection.point_px, polygon))));
  return <svg className="demo-video-overlay" viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none" aria-hidden="true">
    {definition.zones?.flatMap((zone, zoneIndex) => (zone.polygons_px || []).map((polygon, polygonIndex) => <polygon
      key={`${zoneIndex}-${polygonIndex}`} points={polygon.map(([x, y]) => `${x},${y}`).join(" ")}
      className={`demo-zone-trace ${zoneActive ? "active" : ""}`} vectorEffect="non-scaling-stroke" />))}
    {detections.map((detection, index) => {
      const [x0, y0, x1, y1] = detection.bbox_px || [];
      const widthPx = x1 - x0; const heightPx = y1 - y0;
      if (![x0, y0, widthPx, heightPx].every(Number.isFinite) || widthPx <= 0 || heightPx <= 0) return null;
      const localTrack = detection.local_track_id ?? index + 1;
      const fusedRuntimeId = fusedRuntimeIdForSourceTrack(
        fusedEntities, definition.camera_key, localTrack,
      );
      const color = fusedRuntimeId ? trackColor(fusedRuntimeId) : "#a8a29e";
      const labelY = Math.max(32, y0); const labelX = Math.min(Math.max(0, x0), width - 245);
      return <g key={`${localTrack}-${index}`} className="demo-detection-trace">
        <rect x={x0} y={y0} width={widthPx} height={heightPx} rx="5" fill="none" stroke={color} vectorEffect="non-scaling-stroke" />
        <rect x={labelX} y={labelY - 32} width="235" height="32" rx="5" fill={color} />
        <text x={labelX + 10} y={labelY - 9}>person {localTrack} · {Math.round((detection.confidence || 0) * 100)}%</text>
        {detection.point_px && <circle cx={detection.point_px[0]} cy={detection.point_px[1]} r="8" fill={color} stroke="#111" vectorEffect="non-scaling-stroke" />}
      </g>;
    })}
    <g className={`demo-overlay-badge ${zoneActive ? "active" : ""}`}>
      <rect x="20" y="20" width="550" height="40" rx="8" />
      <text x="38" y="47">LOCAL MODEL · FRAME {frame?.frame_index ?? "—"} · {detections.length} DETECTION{detections.length === 1 ? "" : "S"}</text>
    </g>
  </svg>;
}

export function DemoPage({ refreshShell, demoReplay }) {
  const [assets, setAssets] = useState(null); const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false); const [evidence, setEvidence] = useState({});
  const [exitOpen, setExitOpen] = useState(false); const [includeObservations, setIncludeObservations] = useState(false);
  const [debug, setDebug] = useState(false); const [presented, setPresented] = useState({});
  const videos = useRef({});
  const session = demoReplay?.session; const replay = demoReplay?.replay;

  useEffect(() => { api.get("/demo/assets").then(setAssets).catch(setError); }, []);
  useEffect(() => {
    if (!session?.id) { setEvidence({}); return undefined; }
    let cancelled = false;
    Promise.all(CAMERA_KEYS.map((key) => api.get(`/demo/sessions/${session.id}/camera-evidence/${key}`)))
      .then((items) => { if (!cancelled) setEvidence(Object.fromEntries(items.map((item) => [item.camera_key, item]))); })
      .catch((nextError) => { if (!cancelled) setError(nextError); });
    return () => { cancelled = true; };
  }, [session?.id]);

  useEffect(() => {
    if (!session || !replay) return;
    CAMERA_KEYS.forEach((key) => {
      const video = videos.current[key]; if (!video?.duration || video.readyState < 1) return;
      const target = Math.min(replay.videoTime, Math.max(0, video.duration - 1 / 30));
      if (Math.abs(video.currentTime - target) > .06) video.currentTime = target;
      if (session.status === "running" && video.paused) video.play().catch(() => {});
      if (session.status !== "running" && !video.paused) video.pause();
    });
  }, [session?.status, replay?.videoTime, replay?.epoch]);

  const begin = async (mode) => {
    setBusy(true); setError(null);
    try {
      const created = await api.post("/demo/sessions", { mode });
      setDemoSessionId(created.id);
      const running = await api.post(`/demo/sessions/${created.id}/start`);
      demoReplay?.synchronize(running); await refreshShell?.();
    } catch (nextError) { setError(nextError); } finally { setBusy(false); }
  };
  const control = async (action) => {
    setBusy(true);
    try { demoReplay.synchronize(await api.post(`/demo/sessions/${session.id}/${action}`)); }
    catch (nextError) { setError(nextError); } finally { setBusy(false); }
  };
  const exit = async (promote) => {
    setBusy(true);
    try {
      const result = promote ? await api.post(`/demo/sessions/${session.id}/promote`, { include_recorded_observations: includeObservations })
        : await api.post(`/demo/sessions/${session.id}/discard`);
      setDemoSessionId(""); setExitOpen(false); await refreshShell?.(); window.location.hash = promote ? "setup" : "overview"; return result;
    } catch (nextError) { setError(nextError); } finally { setBusy(false); }
  };

  if (!assets) return error ? <ErrorState error={error} retry={() => window.location.reload()} /> : <LoadingState label="Checking demo assets…" />;
  if (!session) return <>
    <PageHeader eyebrow="Playable walkthrough" title="Try StoreLens with four synchronized cameras"
      description="One master replay timeline presents native NVIDIA video and exact frame evidence alongside a cache derived offline through the real StoreLens geometry, multiview, saved-query, and alert pipeline." />
    <section className="demo-hero panel"><div><span className="tiny-label">Observe locally, derive centrally</span><h2>Alert when at least two fused people are in Aisle 04</h2><p>The temporary workspace never changes your normal StoreLens data unless you explicitly keep its setup.</p><div className="demo-actions"><button className="button button-dark" disabled={busy || !assets.available} onClick={() => begin("guided")}><Play size={15} /> Start guided demo</button><button className="button button-secondary" disabled={busy || !assets.available} onClick={() => begin("learn")}><ExternalLink size={15} /> Learn by exploring</button></div></div><div className={`demo-asset-card ${assets.available ? "ready" : "missing"}`}>{assets.available ? <CheckCircle2 /> : <AlertTriangle />}<strong>{assets.available ? "Media and derived cache ready" : "Demo assets are incomplete"}</strong><p>StoreLens does not redistribute NVIDIA footage or model weights.</p>{!assets.available && <code>{assets.derived_cache_error || assets.install_command}</code>}</div></section>
    {error && <div className="inline-warning">{error.message}</div>}
  </>;

  const frameIndex = replay?.frameIndex ?? 0; const sample = replay?.derivedSample;
  const count = replay?.kpi?.value; const known = replay?.kpi?.quality === "known"; const alerts = replay?.alerts || [];
  return <>
    <PageHeader eyebrow="Temporary demo workspace" title="Aisle 04 synchronized replay"
      description="Video, source-local boxes, zone polygons, interpolated Live positions, stepwise KPI, and alerts all follow one authoritative media-relative clock. Playback performs no live central derivation."
      actions={<button className="button button-secondary" onClick={() => setExitOpen(true)}>Exit demo</button>} />
    <div className="demo-status-strip"><span><i className={session.status === "running" ? "active" : ""} /> {session.status}</span><span>Loop {replay.epoch + 1}</span><span>{replay.videoTime.toFixed(3)} / {session.duration_s.toFixed(3)} sec</span><button onClick={() => control(session.status === "running" ? "pause" : "start")} disabled={busy}>{session.status === "running" ? <Pause size={13} /> : <Play size={13} />} {session.status === "running" ? "Pause" : "Play"}</button><button onClick={() => setDebug((value) => !value)}>{debug ? "Hide sync" : "Debug sync"}</button></div>
    {debug && <div className="demo-sync-debug" data-testid="demo-sync-debug"><code>Master: {replay.videoTime.toFixed(3)}s · frame {frameIndex}</code><code>Boxes: frame {frameIndex}</code><code>Derived: {sample?.video_time_s?.toFixed(3) ?? "—"}s · index {replay.derivedIndex}</code><code>Epoch: {replay.epoch}</code>{CAMERA_KEYS.map((key) => <code key={key}>{key.slice(-3)} presented: {presented[key]?.toFixed?.(3) ?? "—"}s</code>)}</div>}
    <div className="demo-video-grid">{CAMERA_KEYS.map((key, index) => <figure key={key}><div className="demo-video-stage"><video ref={(node) => { videos.current[key] = node; }} data-camera-key={key} src={assetUrl(`/demo/media/${key}.mp4`)} muted playsInline preload="auto" onLoadedMetadata={(event) => { event.currentTarget.currentTime = replay.videoTime; }} onTimeUpdate={(event) => { const mediaTime = event.currentTarget.currentTime; setPresented((current) => ({ ...current, [key]: mediaTime })); }} /><VideoEvidenceOverlay definition={session.result?.camera_overlays?.[key]} frame={evidence[key]?.frames?.[frameIndex]} fusedEntities={replay.entities} /></div><figcaption><strong>Camera {index + 1}</strong><span>Native 30 FPS · source-local frame {frameIndex}</span></figcaption></figure>)}</div>
    <div className="demo-outcome-grid"><section className={`panel demo-occupancy ${known && count >= 2 ? "threshold" : ""}`}><span className="tiny-label">Cached real saved-query result</span><strong>{count ?? "—"}</strong><h2>Fused people in Aisle 04</h2><p>Quality: {replay?.kpi?.quality || "unknown"} · evidence from {replay?.kpi?.evidence?.source_count || 0} sources · derived at {sample?.video_time_s?.toFixed(3) ?? "—"}s.</p></section><section className="panel"><span className="tiny-label">Cached real alert evaluation</span><h2>{alerts.length ? "Threshold event recorded" : "Waiting for ≥ 2 people"}</h2><p>{alerts.at(-1)?.message || "Only alert events whose derived time is at or before the master clock are visible."}</p>{alerts.length > 0 && <small>Current count: {count} · threshold event at {alerts.at(-1).video_time_s.toFixed(3)}s</small>}</section></div>
    <section className="panel demo-timeline"><div className="panel-heading"><div><h2>What StoreLens actually configured</h2><p>Each action corresponds to a stored demo-workspace result. The derived timeline was generated separately through the real platform pipeline.</p></div></div><ol>{session.action_log.map((item) => <li key={item.name}><CheckCircle2 size={16} /><div><strong>{item.name}</strong><p>{item.explanation}</p><code>{JSON.stringify(item.result)}</code></div></li>)}</ol></section>
    <section className="panel demo-explore"><div><span className="tiny-label">Inspect the same timeline</span><h2>Move between synchronized views</h2><p>Live 3D and the generated dashboard consume this replay state from the app-level master clock.</p></div><div>{[["live","Live 3D"],["overview","Dashboard"],["setup","Plan & calibration"],["observations","Evidence"],["sources","Sources"]].map(([route, label]) => <a key={route} className="button button-secondary" href={`#${route}`}>{label}<ExternalLink size={12} /></a>)}</div></section>
    {(error || demoReplay?.error) && <div className="inline-warning">{(error || demoReplay.error).message}</div>}
    {exitOpen && <div className="modal-backdrop"><section className="modal demo-exit"><h2>Exit temporary demo workspace</h2><p>Keep setup copies only the mapped space, four sources, placements, calibrations, and multiview group.</p><ul><li>Aisle 04, views, query, dashboard, alert, and review events remain demo-only.</li></ul><label className="check-row"><input type="checkbox" checked={includeObservations} onChange={(event) => setIncludeObservations(event.target.checked)} /> Also copy raw replay observations (off by default)</label><div className="panel-footer"><button className="button button-ghost" onClick={() => setExitOpen(false)}>Cancel</button><button className="button danger" disabled={busy} onClick={() => exit(false)}><Trash2 size={14} /> Discard demo</button><button className="button button-dark" disabled={busy} onClick={() => exit(true)}><Save size={14} /> Keep camera & space setup</button></div></section></div>}
  </>;
}
