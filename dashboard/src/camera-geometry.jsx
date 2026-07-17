import { useEffect, useMemo, useRef, useState } from "react";
import {
  CheckCircle2,
  Layers3,
  MousePointer2,
  RotateCcw,
  Save,
  Trash2,
  Undo2,
} from "lucide-react";
import { api, assetUrl } from "./api.js";
import { Badge, EmptyState, Modal } from "./components.jsx";

const SURFACE_KINDS = [
  "mattress",
  "table",
  "shelf",
  "conveyor",
  "platform",
  "custom",
];

function asFramePoint(event, image) {
  if (!image?.naturalWidth || !image?.naturalHeight) return null;
  const rect = image.getBoundingClientRect();
  return {
    x: Math.round(
      (((event.clientX - rect.left) / rect.width) * image.naturalWidth) * 10,
    ) / 10,
    y: Math.round(
      (((event.clientY - rect.top) / rect.height) * image.naturalHeight) * 10,
    ) / 10,
  };
}

function insetPolygon(points, factor = 0.9) {
  if (!points.length) return [];
  const center = points.reduce(
    (sum, point) => ({
      x: sum.x + point.x / points.length,
      y: sum.y + point.y / points.length,
    }),
    { x: 0, y: 0 },
  );
  return points.map((point) => ({
    x: center.x + (point.x - center.x) * factor,
    y: center.y + (point.y - center.y) * factor,
  }));
}

function polygonPoints(points) {
  return points.map((point) => `${point.x},${point.y}`).join(" ");
}

function CameraPolygonEditor({
  source,
  outer,
  detection,
  surfacePoints,
  drawTarget,
  onPoint,
  onFrame,
}) {
  const imageRef = useRef(null);
  const snapshot = useMemo(
    () => assetUrl(`/sources/${source.id}/snapshot.jpg?t=${Date.now()}`),
    [source.id],
  );
  const [frame, setFrame] = useState({ width: 1, height: 1 });
  const click = (event) => {
    const point = asFramePoint(event, imageRef.current);
    if (point) onPoint(point);
  };
  return (
    <div className="camera-geometry-frame" onClick={click}>
      <img
        ref={imageRef}
        src={snapshot}
        alt={`Geometry frame from ${source.name}`}
        onLoad={(event) => {
          const next = {
            width: event.currentTarget.naturalWidth,
            height: event.currentTarget.naturalHeight,
          };
          setFrame(next);
          onFrame(next);
        }}
      />
      <svg viewBox={`0 0 ${frame.width} ${frame.height}`} aria-hidden="true">
        {outer.length >= 3 && (
          <polygon className="geometry-outer" points={polygonPoints(outer)} />
        )}
        {detection.length >= 3 && (
          <polygon
            className="geometry-detection"
            points={polygonPoints(detection)}
          />
        )}
        {(drawTarget === "outer" ? outer : drawTarget === "detection" ? detection : surfacePoints).map(
          (point, index) => (
            <g key={`${drawTarget}-${index}`} className="geometry-point">
              <circle cx={point.x} cy={point.y} r="7" />
              <text x={point.x} y={point.y + 3}>
                {index + 1}
              </text>
            </g>
          ),
        )}
      </svg>
      <span className="geometry-frame-size">
        {frame.width} × {frame.height}px
      </span>
    </div>
  );
}

export function CameraGeometryModal({ source, zones, onClose, notify }) {
  const [tab, setTab] = useState("zone");
  const [surfaces, setSurfaces] = useState([]);
  const [views, setViews] = useState([]);
  const [zoneId, setZoneId] = useState(zones[0]?.id || "");
  const [surfaceId, setSurfaceId] = useState("");
  const [drawTarget, setDrawTarget] = useState("outer");
  const [outer, setOuter] = useState([]);
  const [detection, setDetection] = useState([]);
  const [surfacePoints, setSurfacePoints] = useState([]);
  const [rule, setRule] = useState("bbox_overlap");
  const [threshold, setThreshold] = useState(0.5);
  const [minKeypoints, setMinKeypoints] = useState(2);
  const [surfaceForm, setSurfaceForm] = useState({
    name: "Mattress plane",
    kind: "mattress",
    height_m: "",
  });
  const [frame, setFrame] = useState({ width: 0, height: 0 });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const selectedZone = zones.find((zone) => zone.id === Number(zoneId));
  const existingView = views.find((view) => view.zone_id === Number(zoneId));
  const activePoints =
    drawTarget === "outer"
      ? outer
      : drawTarget === "detection"
        ? detection
        : surfacePoints;

  const load = async () => {
    const [nextSurfaces, nextViews] = await Promise.all([
      api.get(`/projection-surfaces?source_id=${source.id}`),
      api.get(`/zone-views?source_id=${source.id}`),
    ]);
    setSurfaces(nextSurfaces);
    setViews(nextViews);
    return { nextSurfaces, nextViews };
  };

  useEffect(() => {
    load().catch((err) => setError(err.message));
  }, [source.id]);

  useEffect(() => {
    if (existingView) {
      setOuter(existingView.outer_polygon_px || []);
      setDetection(existingView.detection_polygon_px || []);
      setSurfaceId(existingView.projection_surface_id || "");
      setRule(existingView.membership_rule);
      setThreshold(existingView.threshold);
      setMinKeypoints(existingView.min_keypoints);
    } else {
      setOuter([]);
      setDetection([]);
      setSurfaceId("");
      setRule("bbox_overlap");
      setThreshold(0.5);
      setMinKeypoints(2);
    }
  }, [zoneId, existingView?.id, existingView?.revision]);

  const addPoint = (point) => {
    if (tab === "surface") {
      const limit = selectedZone?.polygon?.length || 0;
      if (!limit || surfacePoints.length >= limit) return;
      setSurfacePoints((current) => [...current, point]);
      return;
    }
    if (drawTarget === "outer") setOuter((current) => [...current, point]);
    else setDetection((current) => [...current, point]);
  };

  const setActivePoints = (next) => {
    if (tab === "surface") setSurfacePoints(next);
    else if (drawTarget === "outer") setOuter(next);
    else setDetection(next);
  };

  const footprintToFrame = async () => {
    if (!selectedZone) return;
    setBusy(true);
    setError("");
    try {
      const result = await api.post(`/sources/${source.id}/unproject`, {
        points: selectedZone.polygon,
        surface_id: surfaceId ? Number(surfaceId) : null,
      });
      setOuter(result.points);
      setDetection(insetPolygon(result.points));
      setDrawTarget("detection");
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };

  const saveView = async () => {
    setBusy(true);
    setError("");
    try {
      if (!selectedZone) throw new Error("Choose a global map zone first.");
      if (outer.length < 3 || detection.length < 3)
        throw new Error("Draw both the visible boundary and inset decision ROI.");
      const body = {
        zone_id: selectedZone.id,
        source_id: source.id,
        outer_polygon_px: outer,
        detection_polygon_px: detection,
        projection_surface_id: surfaceId ? Number(surfaceId) : null,
        membership_rule: rule,
        threshold: Number(threshold),
        min_keypoints: Number(minKeypoints),
      };
      existingView
        ? await api.put(`/zone-views/${existingView.id}`, body)
        : await api.post("/zone-views", body);
      await load();
      notify?.(
        "Camera zone view saved",
        `${selectedZone.name} now has a versioned decision ROI for ${source.name}.`,
      );
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };

  const saveSurface = async () => {
    setBusy(true);
    setError("");
    try {
      const mapPoints = selectedZone?.polygon || [];
      if (mapPoints.length < 4)
        throw new Error("The selected footprint needs at least four corners.");
      if (surfacePoints.length !== mapPoints.length)
        throw new Error(`Click all ${mapPoints.length} footprint corners in order.`);
      const saved = await api.post("/projection-surfaces", {
        source_id: source.id,
        name: surfaceForm.name.trim(),
        kind: surfaceForm.kind,
        height_m:
          surfaceForm.height_m === "" ? null : Number(surfaceForm.height_m),
        points: surfacePoints.map((px, index) => ({
          px,
          map: mapPoints[index],
        })),
        frame_w: frame.width || null,
        frame_h: frame.height || null,
      });
      await load();
      setSurfaceId(saved.id);
      setSurfacePoints([]);
      setTab("zone");
      setDrawTarget("outer");
      notify?.(
        "Projection plane saved",
        `${saved.name} accounts for the elevated surface without changing map Y by hand.`,
      );
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };

  const deleteView = async (view) => {
    if (!window.confirm(`Delete the ${view.zone_name} camera view?`)) return;
    await api.del(`/zone-views/${view.id}`);
    await load();
  };

  const deleteSurface = async (surface) => {
    if (!window.confirm(`Delete projection plane ${surface.name}?`)) return;
    try {
      await api.del(`/projection-surfaces/${surface.id}`);
      if (Number(surfaceId) === surface.id) setSurfaceId("");
      await load();
    } catch (err) {
      setError(err.message);
    }
  };

  return (
    <Modal
      wide
      title={`Camera geometry · ${source.name}`}
      onClose={onClose}
      footer={
        <>
          <button className="button button-secondary" onClick={onClose}>
            Close
          </button>
          <button
            className="button button-dark"
            onClick={tab === "zone" ? saveView : saveSurface}
            disabled={busy}
          >
            <Save size={14} />
            {busy ? "Saving…" : tab === "zone" ? "Save zone view" : "Save plane"}
          </button>
        </>
      }
    >
      <div className="geometry-intro">
        <div>
          <span className="tiny-label">Two layers, one physical zone</span>
          <h3>Keep the map footprint global; configure camera evidence here</h3>
          <p>
            The visible boundary explains where the zone appears. The inset ROI
            decides presence. A named plane maps points on an elevated surface
            such as a mattress—height is metadata, never a manual Y offset.
          </p>
        </div>
        <Badge tone={existingView ? "positive" : "warning"}>
          {existingView ? `view revision ${existingView.revision}` : "not configured"}
        </Badge>
      </div>

      <div className="calibration-mode" role="tablist">
        <button
          className={tab === "zone" ? "active" : ""}
          onClick={() => {
            setTab("zone");
            setDrawTarget("outer");
          }}
        >
          Zone view & decision ROI
        </button>
        <button
          className={tab === "surface" ? "active" : ""}
          onClick={() => {
            setTab("surface");
            setDrawTarget("surface");
          }}
        >
          Elevated projection plane
        </button>
      </div>

      <div className="geometry-layout">
        <section className="geometry-editor">
          <CameraPolygonEditor
            source={source}
            outer={tab === "zone" ? outer : []}
            detection={tab === "zone" ? detection : []}
            surfacePoints={surfacePoints}
            drawTarget={tab === "surface" ? "surface" : drawTarget}
            onPoint={addPoint}
            onFrame={setFrame}
          />
          <div className="geometry-draw-toolbar">
            {tab === "zone" && (
              <>
                <button
                  className={drawTarget === "outer" ? "active" : ""}
                  onClick={() => setDrawTarget("outer")}
                >
                  <MousePointer2 size={14} /> Visible boundary
                </button>
                <button
                  className={drawTarget === "detection" ? "active" : ""}
                  onClick={() => setDrawTarget("detection")}
                >
                  <CheckCircle2 size={14} /> Decision ROI
                </button>
              </>
            )}
            <button
              onClick={() => setActivePoints(activePoints.slice(0, -1))}
              disabled={!activePoints.length}
            >
              <Undo2 size={14} /> Undo
            </button>
            <button onClick={() => setActivePoints([])} disabled={!activePoints.length}>
              <RotateCcw size={14} /> Clear
            </button>
          </div>
        </section>

        <aside className="geometry-settings">
          <label className="field">
            <span>Global map zone</span>
            <select value={zoneId} onChange={(event) => setZoneId(event.target.value)}>
              {zones.map((zone) => (
                <option key={zone.id} value={zone.id}>
                  {zone.name} · revision {zone.revision || 1}
                </option>
              ))}
            </select>
          </label>

          {tab === "zone" ? (
            <>
              <label className="field">
                <span>Projection plane</span>
                <select
                  value={surfaceId}
                  onChange={(event) => setSurfaceId(event.target.value)}
                >
                  <option value="">Floor calibration</option>
                  {surfaces.map((surface) => (
                    <option key={surface.id} value={surface.id}>
                      {surface.name} · rev {surface.revision}
                    </option>
                  ))}
                </select>
              </label>
              <button
                className="button button-secondary geometry-footprint-button"
                onClick={footprintToFrame}
                disabled={!selectedZone || busy}
              >
                <Layers3 size={14} /> Project map footprint into frame
              </button>
              <p className="form-note">
                This computes the visible boundary from the selected plane and
                creates a 10% inset ROI. You can then adjust either polygon.
              </p>
              <label className="field">
                <span>Presence rule</span>
                <select value={rule} onChange={(event) => setRule(event.target.value)}>
                  <option value="bbox_overlap">Bounding-box overlap</option>
                  <option value="keypoints_inside">Pose keypoints inside</option>
                  <option value="point">Representative point inside</option>
                </select>
              </label>
              <label className="field">
                <span>
                  {rule === "keypoints_inside" ? "Inside fraction" : "Overlap threshold"} ·{" "}
                  {Math.round(Number(threshold) * 100)}%
                </span>
                <input
                  type="range"
                  min="0.1"
                  max="1"
                  step="0.05"
                  value={threshold}
                  onChange={(event) => setThreshold(event.target.value)}
                />
              </label>
              {rule === "keypoints_inside" && (
                <label className="field">
                  <span>Minimum keypoints inside</span>
                  <input
                    type="number"
                    min="1"
                    value={minKeypoints}
                    onChange={(event) => setMinKeypoints(event.target.value)}
                  />
                </label>
              )}
            </>
          ) : (
            <>
              <label className="field">
                <span>Plane name</span>
                <input
                  value={surfaceForm.name}
                  onChange={(event) =>
                    setSurfaceForm({ ...surfaceForm, name: event.target.value })
                  }
                />
              </label>
              <label className="field">
                <span>Surface kind</span>
                <select
                  value={surfaceForm.kind}
                  onChange={(event) =>
                    setSurfaceForm({ ...surfaceForm, kind: event.target.value })
                  }
                >
                  {SURFACE_KINDS.map((kind) => (
                    <option key={kind}>{kind}</option>
                  ))}
                </select>
              </label>
              <label className="field">
                <span>Height above floor (metres, metadata)</span>
                <input
                  type="number"
                  min="0"
                  step="0.01"
                  value={surfaceForm.height_m}
                  onChange={(event) =>
                    setSurfaceForm({ ...surfaceForm, height_m: event.target.value })
                  }
                  placeholder="0.55"
                />
              </label>
              <div className="geometry-order">
                <strong>Click corners in map order</strong>
                <p>
                  Match each visible surface corner to the selected zone footprint.
                  The footprint has {selectedZone?.polygon?.length || 0} corners.
                </p>
                <ol>
                  {(selectedZone?.polygon || []).map((point, index) => (
                    <li key={index} className={surfacePoints[index] ? "done" : ""}>
                      <span>{index + 1}</span>
                      map {Number(point.x).toFixed(2)}, {Number(point.y).toFixed(2)}m
                    </li>
                  ))}
                </ol>
              </div>
            </>
          )}
          {error && <div className="form-error">{error}</div>}
        </aside>
      </div>

      <div className="saved-geometry">
        <section>
          <h4>Saved zone views</h4>
          {views.map((view) => (
            <div key={view.id}>
              <span>
                <strong>{view.zone_name}</strong>
                <small>
                  {view.membership_rule.replaceAll("_", " ")} · revision {view.revision}
                </small>
              </span>
              <button className="icon-button danger" onClick={() => deleteView(view)}>
                <Trash2 size={14} />
              </button>
            </div>
          ))}
          {!views.length && <EmptyState title="No camera zone views">Create one above.</EmptyState>}
        </section>
        <section>
          <h4>Saved projection planes</h4>
          {surfaces.map((surface) => (
            <div key={surface.id}>
              <span>
                <strong>{surface.name}</strong>
                <small>
                  {surface.kind} · ±{Number(surface.error_m || 0).toFixed(3)}m · revision{" "}
                  {surface.revision}
                </small>
              </span>
              <button className="icon-button danger" onClick={() => deleteSurface(surface)}>
                <Trash2 size={14} />
              </button>
            </div>
          ))}
          {!surfaces.length && <EmptyState title="Floor only">Add a plane for elevated surfaces.</EmptyState>}
        </section>
      </div>
    </Modal>
  );
}
