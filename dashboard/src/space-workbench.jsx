/* The floor-map editor and camera calibration.
 *
 * Split by job rather than by object: Setup › Space owns the plan, the map and
 * the zones; Setup › Cameras owns placement and calibration. Both share the one
 * map surface below, so the picture a user learns on the Space tab is the same
 * picture they calibrate against.
 */
import { useEffect, useRef, useState } from "react";
import {
  Camera, CheckCircle2, Crosshair, Eraser, FileUp, MousePointer2, Pencil, Plus,
  RotateCcw, Ruler, ScanLine, Save, Trash2, Type, X,
} from "lucide-react";
import { api, assetUrl, demoSessionId } from "./api.js";
import { reportTourEvent } from "./demo-tour.jsx";
import { PlanDigitizer } from "./plan-digitizer.jsx";
import { EmptyState, Modal, OverflowMenu, Panel, StatusPill, TechnicalDetails } from "./ui.jsx";
import { calibrationStatus } from "./status.js";

const ZONE_TYPES = [
  "area", "entrance", "checkout", "queue", "aisle", "stockroom", "restricted",
  "equipment", "hall", "classroom", "playground", "meeting_room", "custom",
];

const TOOLS = [
  ["select", "Select", MousePointer2],
  ["wall", "Wall", Pencil],
  ["zone", "Zone", ScanLine],
  ["label", "Label", Type],
  ["camera", "Place camera", Camera],
  ["erase", "Erase", Eraser],
];

/* Short and imperative. The old strings explained the concept as well as the
 * gesture; the gesture is the only part that changes per tool. */
const TOOL_HINTS = {
  select: "Click a camera to adjust it.",
  wall: "Click each corner, then press Enter to finish.",
  zone: "Click the corners, then finish the shape.",
  label: "Click where the label should go.",
  camera: "Choose a camera, then click the map.",
  erase: "Click a wall or label to remove it.",
};

export function polygonPoints(points = []) {
  return points.map((point) => `${point.x},${point.y}`).join(" ");
}

export function zoneRings(zone) {
  const geometry = zone?.geometry;
  if (geometry?.type === "Polygon") {
    return [(geometry.coordinates?.[0] || []).map(([x, y]) => ({ x, y }))];
  }
  if (geometry?.type === "MultiPolygon") {
    return (geometry.coordinates || []).map((part) => (part?.[0] || []).map(([x, y]) => ({ x, y })));
  }
  return zone?.polygon?.length ? [zone.polygon] : [];
}

function eventPoint(event, svg) {
  if (!svg) return null;
  const point = svg.createSVGPoint();
  point.x = event.clientX;
  point.y = event.clientY;
  const transform = svg.getScreenCTM();
  if (!transform) return null;
  const result = point.matrixTransform(transform.inverse());
  return { x: Math.round(result.x * 100) / 100, y: Math.round(result.y * 100) / 100 };
}

function centroid(points = []) {
  if (!points.length) return { x: 0, y: 0 };
  return points.reduce((sum, point) => ({
    x: sum.x + point.x / points.length,
    y: sum.y + point.y / points.length,
  }), { x: 0, y: 0 });
}

function CameraGlyph({ source, selected, onClick }) {
  if (!source.placement) return null;
  const { x, y, rotation_deg: rotation = 0, fov_deg: fov = 70 } = source.placement;
  const length = 2.1;
  const a = ((rotation - fov / 2) * Math.PI) / 180;
  const b = ((rotation + fov / 2) * Math.PI) / 180;
  const wedge = `${x},${y} ${x + Math.cos(a) * length},${y + Math.sin(a) * length} `
    + `${x + Math.cos(b) * length},${y + Math.sin(b) * length}`;
  return (
    <g
      className={`workbench-camera ${selected ? "selected" : ""}`}
      onClick={(event) => { event.stopPropagation(); onClick?.(); }}
      role="button"
      tabIndex="0"
      aria-label={`Select ${source.name}`}
      onKeyDown={(event) => { if (event.key === "Enter" || event.key === " ") onClick?.(); }}
    >
      <polygon points={wedge} className="camera-fov" />
      <circle className="camera-focus-ring" cx={x} cy={y} r=".31" />
      <circle className="camera-position" cx={x} cy={y} r=".19" />
      <path d={`M ${x} ${y} L ${x + Math.cos((rotation * Math.PI) / 180) * 0.52} `
        + `${y + Math.sin((rotation * Math.PI) / 180) * 0.52}`} />
      <text x={x} y={y - 0.32}>{source.name}</text>
    </g>
  );
}

export function MapSurface({
  store, zones = [], sources = [], backgroundUrl, draft = [], selectedSourceId, tool = "select",
  onMapClick, onSourceSelect, onEraseWall, onEraseLabel, svgRef, testPoint, children,
}) {
  const width = Math.max(Number(store?.width_m) || 20, 1);
  const height = Math.max(Number(store?.height_m) || 12, 1);
  return (
    <div className="map-workbench-canvas" data-demo-tour="floor-map">
      <svg
        ref={svgRef}
        viewBox={`-.6 -.6 ${width + 1.2} ${height + 1.2}`}
        onClick={onMapClick}
        aria-label="Floor map"
        role="img"
      >
        <defs>
          <pattern id="workbench-grid" width="1" height="1" patternUnits="userSpaceOnUse">
            <path d="M 1 0 L 0 0 0 1" />
          </pattern>
        </defs>
        <rect x="0" y="0" width={width} height={height} className="workbench-floor" />
        {backgroundUrl && (
          <image href={backgroundUrl} x="0" y="0" width={width} height={height}
                 preserveAspectRatio="none" className="workbench-plan-background" />
        )}
        {(store?.map?.floor_polygons || []).map((floor, index) => (
          <polygon key={`imported-floor-${index}`} points={polygonPoints(floor)}
                   className="workbench-imported-floor" />
        ))}
        <rect x="0" y="0" width={width} height={height} fill="url(#workbench-grid)"
              className="workbench-grid" />
        {zones.map((zone) => {
          const rings = zoneRings(zone);
          const labelRing = rings.reduce((largest, ring) => (
            ring.length > largest.length ? ring : largest), []);
          const center = centroid(labelRing);
          return (
            <g key={zone.id} className="workbench-zone">
              {rings.map((ring, index) => (
                <polygon key={`${zone.id}-${index}`} points={polygonPoints(ring)}
                         fill={`${zone.color}24`} stroke={zone.color} />
              ))}
              <text x={center.x} y={center.y}>{zone.name}</text>
            </g>
          );
        })}
        {(store?.map?.walls || []).map((wall, index) => (
          <polyline
            key={`wall-${index}`}
            points={polygonPoints(wall)}
            className={`workbench-wall ${tool === "erase" ? "erasable" : ""}`}
            onClick={(event) => {
              if (tool === "erase") { event.stopPropagation(); onEraseWall?.(index); }
            }}
          />
        ))}
        {(store?.map?.labels || []).map((label, index) => (
          <text
            key={`label-${index}`}
            x={label.x}
            y={label.y}
            className={`workbench-label ${tool === "erase" ? "erasable" : ""}`}
            onClick={(event) => {
              if (tool === "erase") { event.stopPropagation(); onEraseLabel?.(index); }
            }}
          >
            {label.text}
          </text>
        ))}
        {sources.map((source) => (
          <CameraGlyph
            key={source.id}
            source={source}
            selected={source.id === selectedSourceId}
            onClick={() => onSourceSelect?.(source.id)}
          />
        ))}
        {!!draft.length && (
          <g className="workbench-draft">
            <polyline points={polygonPoints(draft)} />
            {tool === "zone" && draft.length > 2 && (
              <line x1={draft.at(-1).x} y1={draft.at(-1).y} x2={draft[0].x} y2={draft[0].y} />
            )}
            {draft.map((point, index) => (
              <circle key={index} cx={point.x} cy={point.y} r=".11" />
            ))}
          </g>
        )}
        {testPoint && (
          <g className="test-map-point">
            <circle cx={testPoint.x} cy={testPoint.y} r=".25" />
            <path d={`M ${testPoint.x - 0.45} ${testPoint.y} H ${testPoint.x + 0.45} `
              + `M ${testPoint.x} ${testPoint.y - 0.45} V ${testPoint.y + 0.45}`} />
          </g>
        )}
        {children}
      </svg>
      <div className="map-scale"><Ruler size={13} aria-hidden="true" /> 1 m grid</div>
    </div>
  );
}

/* ------------------------------------------------------------- Space tab */

export function SpaceEditor({ store, zones, sources, zoneViews = [], onRefresh, notify }) {
  const svgRef = useRef(null);
  const demoPlanBackground = demoSessionId() ? assetUrl("/demo/plan.png") : null;
  const [tool, setTool] = useState("select");
  const [draft, setDraft] = useState([]);
  const [zoneDraft, setZoneDraft] = useState(null);
  const [labelDraft, setLabelDraft] = useState(null);
  const [editingZone, setEditingZone] = useState(null);
  const [placingSourceId, setPlacingSourceId] = useState(sources[0]?.id || null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const placingSource = sources.find((source) => source.id === placingSourceId) || null;

  useEffect(() => {
    const onKeyDown = (event) => {
      if (event.key === "Escape") setDraft([]);
      if (event.target.closest?.("input, select, textarea, [role='dialog']")) return;
      if (event.key === "Enter" && (tool === "wall" || tool === "zone")) finishDraft();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  });

  const saveMap = async (nextMap, message) => {
    setBusy(true);
    setError("");
    try {
      await api.put("/store", { map: nextMap });
      await onRefresh();
      notify(message);
    } catch (err) { setError(err.message); } finally { setBusy(false); }
  };

  const finishDraft = () => {
    if (tool === "wall") {
      if (draft.length < 2) return setError("A wall needs at least two points.");
      const map = store.map || {};
      saveMap({ ...map, walls: [...(map.walls || []), draft] }, "Wall added");
      setDraft([]);
    } else if (tool === "zone") {
      if (draft.length < 3) return setError("A zone needs at least three points.");
      setZoneDraft(draft);
      setDraft([]);
    }
    return undefined;
  };

  const mapClick = async (event) => {
    const point = eventPoint(event, svgRef.current);
    if (!point) return;
    if (tool === "wall" || tool === "zone") setDraft((current) => [...current, point]);
    if (tool === "label") setLabelDraft(point);
    if (tool === "camera" && placingSource) {
      setBusy(true);
      setError("");
      try {
        await api.put(`/sources/${placingSource.id}/placement`, {
          x: point.x, y: point.y,
          rotation_deg: placingSource.placement?.rotation_deg || 0,
          fov_deg: placingSource.placement?.fov_deg || 70,
        });
        await onRefresh();
        notify("Camera placed", placingSource.name);
        setTool("select");
      } catch (err) { setError(err.message); } finally { setBusy(false); }
    }
  };

  const eraseWall = async (index) => {
    if (!window.confirm("Remove this wall?")) return;
    const map = store.map || {};
    const walls = [...(map.walls || [])];
    walls.splice(index, 1);
    await saveMap({ ...map, walls }, "Wall removed");
  };
  const eraseLabel = async (index) => {
    if (!window.confirm("Remove this label?")) return;
    const map = store.map || {};
    const labels = [...(map.labels || [])];
    labels.splice(index, 1);
    await saveMap({ ...map, labels }, "Label removed");
  };
  const deleteZone = async (zone) => {
    if (!window.confirm(`Delete ${zone.name}? Observations keep the zone they were assigned.`)) return;
    try {
      await api.del(`/zones/${zone.id}`);
      await onRefresh();
      notify("Zone deleted", zone.name);
    } catch (err) { notify("Couldn't delete the zone", err.message, "error"); }
  };

  return (
    <div className="stack">
      <PlanDigitizer onRefresh={onRefresh} notify={notify} />
      <Panel
        title="Floor map"
        action={
          <div className="workbench-toolbar" role="toolbar" aria-label="Map tools">
            {TOOLS.map(([value, label, Icon]) => (
              <button
                key={value}
                className={tool === value ? "active" : ""}
                onClick={() => { setTool(value); setDraft([]); setError(""); }}
                aria-pressed={tool === value}
                disabled={busy || (value === "camera" && !sources.length)}
                title={value === "camera" && !sources.length ? "Add a source first" : undefined}
              >
                <Icon size={15} aria-hidden="true" />{label}
              </button>
            ))}
          </div>
        }
      >
        <div className="workbench-status" role="status">
          <Crosshair size={15} aria-hidden="true" />
          <span>{TOOL_HINTS[tool]}</span>
          {tool === "camera" && sources.length > 0 && (
            <select
              aria-label="Camera to place"
              value={placingSourceId || ""}
              onChange={(event) => setPlacingSourceId(Number(event.target.value))}
            >
              {sources.map((source) => (
                <option key={source.id} value={source.id}>{source.name}</option>
              ))}
            </select>
          )}
          {draft.length > 0 && <span className="draft-count">{draft.length} points</span>}
        </div>
        <MapSurface
          store={store}
          zones={zones}
          sources={sources}
          backgroundUrl={demoPlanBackground}
          draft={draft}
          selectedSourceId={placingSourceId}
          tool={tool}
          onMapClick={mapClick}
          onSourceSelect={setPlacingSourceId}
          onEraseWall={eraseWall}
          onEraseLabel={eraseLabel}
          svgRef={svgRef}
        />
        {(tool === "wall" || tool === "zone") && (
          <div className="draft-actions">
            <button className="button button-secondary" onClick={() => setDraft([])}
                    disabled={!draft.length}>
              <RotateCcw size={14} aria-hidden="true" /> Clear
            </button>
            <button className="button button-primary" onClick={finishDraft}
                    disabled={busy || draft.length < (tool === "zone" ? 3 : 2)}>
              <CheckCircle2 size={14} aria-hidden="true" /> Finish {tool}
            </button>
          </div>
        )}
        {error && <div className="form-error" role="alert">{error}</div>}
      </Panel>

      <Panel
        title="Zones"
        action={
          <button className="button button-primary"
                  onClick={() => { setTool("zone"); setDraft([]); }}>
            <Plus size={14} aria-hidden="true" /> Draw zone
          </button>
        }
      >
        <div className="record-list">
          {zones.map((zone) => (
            <div className="record-list-row" key={zone.id}>
              <span className="zone-swatch" style={{ background: zone.color }} aria-hidden="true" />
              <div className="record-copy">
                <strong>{zone.name}</strong>
                <small>{zoneCoverage(zone, sources, zoneViews)}</small>
              </div>
              <OverflowMenu
                label={`Actions for ${zone.name}`}
                items={[
                  { label: "Rename", onSelect: () => setEditingZone(zone) },
                  { label: "Delete", destructive: true, onSelect: () => deleteZone(zone) },
                ]}
              />
            </div>
          ))}
          {!zones.length && (
            <EmptyState title="No zones yet">
              Draw the areas you want to monitor, like an aisle or a queue.
            </EmptyState>
          )}
        </div>
      </Panel>

      {zoneDraft && (
        <ZoneEditorModal
          polygonPoints={zoneDraft}
          onClose={() => setZoneDraft(null)}
          onSaved={async () => {
            setZoneDraft(null);
            setTool("select");
            await onRefresh();
            notify("Zone created");
          }}
        />
      )}
      {editingZone && (
        <ZoneEditorModal
          zone={editingZone}
          polygonPoints={editingZone.polygon}
          onClose={() => setEditingZone(null)}
          onSaved={async () => { setEditingZone(null); await onRefresh(); notify("Zone updated"); }}
        />
      )}
      {labelDraft && (
        <LabelModal
          onClose={() => setLabelDraft(null)}
          onSave={async (text) => {
            const map = store.map || {};
            await saveMap({ ...map, labels: [...(map.labels || []), { ...labelDraft, text }] },
                          "Label added");
            setLabelDraft(null);
            setTool("select");
          }}
        />
      )}
    </div>
  );
}

/** "Seen by Camera 3, Camera 4" — zone views surfaced as coverage, not as CRUD.
 *  A ZoneView is a real ManySight object, but it is not a thing a person needs
 *  to manage: what they want to know is which cameras can see the zone. */
function zoneCoverage(zone, sources, zoneViews) {
  const names = zoneViews
    .filter((view) => Number(view.zone_id) === Number(zone.id))
    .map((view) => sources.find((source) => source.id === view.source_id)?.name)
    .filter(Boolean);
  if (names.length) return `Seen by ${names.join(", ")}`;
  return String(zone.ztype || "area").replaceAll("_", " ");
}

/* ------------------------------------------------------------- dialogs */

function ZoneEditorModal({ zone, polygonPoints: points, onClose, onSaved }) {
  const [form, setForm] = useState({
    name: zone?.name || "", ztype: zone?.ztype || "area", color: zone?.color || "#7059ff",
  });
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const save = async () => {
    setSaving(true);
    setError("");
    try {
      if (!form.name.trim()) throw new Error("Give the zone a name.");
      const body = { name: form.name.trim(), ztype: form.ztype, color: form.color, polygon: points };
      if (zone) await api.put(`/zones/${zone.id}`, body);
      else await api.post("/zones", body);
      onSaved();
    } catch (err) { setError(err.message); } finally { setSaving(false); }
  };
  return (
    <Modal
      title={zone ? `Edit ${zone.name}` : "Name this zone"}
      onClose={onClose}
      footer={
        <>
          <button className="button button-secondary" onClick={onClose}>Cancel</button>
          <button className="button button-primary" onClick={save} disabled={saving}>
            <Save size={14} aria-hidden="true" />{saving ? "Saving…" : "Save zone"}
          </button>
        </>
      }
    >
      <div className="form-grid">
        <label className="field field-full">
          <span>Name</span>
          <input autoFocus value={form.name} placeholder="Aisle 04"
                 onChange={(event) => setForm({ ...form, name: event.target.value })} />
        </label>
        <label className="field">
          <span>Kind</span>
          <select value={form.ztype}
                  onChange={(event) => setForm({ ...form, ztype: event.target.value })}>
            {ZONE_TYPES.map((type) => (
              <option key={type} value={type}>{type.replaceAll("_", " ")}</option>
            ))}
          </select>
          {/* Honest about what the field does: it labels the zone, it does not
              change how anything is counted. */}
          <small>A label for your own reference.</small>
        </label>
        <label className="field">
          <span>Colour</span>
          <input type="color" value={form.color}
                 onChange={(event) => setForm({ ...form, color: event.target.value })} />
        </label>
      </div>
      {error && <div className="form-error" role="alert">{error}</div>}
    </Modal>
  );
}

function LabelModal({ onClose, onSave }) {
  const [text, setText] = useState("");
  const [error, setError] = useState("");
  return (
    <Modal
      title="Add a label"
      onClose={onClose}
      footer={
        <>
          <button className="button button-secondary" onClick={onClose}>Cancel</button>
          <button className="button button-primary"
                  onClick={() => (text.trim() ? onSave(text.trim()) : setError("Enter some text."))}>
            Add label
          </button>
        </>
      }
    >
      <label className="field">
        <span>Text</span>
        <input autoFocus value={text} placeholder="Loading bay"
               onChange={(event) => setText(event.target.value)} />
      </label>
      {error && <div className="form-error" role="alert">{error}</div>}
    </Modal>
  );
}

function CalibrationMap({ store, zones, sources, svgRef, onClick, points, pending, testPoint }) {
  return (
    <MapSurface
      store={store}
      zones={zones}
      sources={sources}
      // The same plan image the map shows, so matching points has a recognisable
      // floor to aim at instead of an empty grid.
      backgroundUrl={demoSessionId() ? assetUrl("/demo/plan.png") : null}
      draft={[]}
      selectedSourceId={null}
      tool="select"
      onMapClick={onClick}
      svgRef={svgRef}
      testPoint={testPoint}
    >
      {points.map((pair, index) => (
        <g key={index} className="calibration-map-point">
          <circle cx={pair.map.x} cy={pair.map.y} r=".18" />
          <text x={pair.map.x} y={pair.map.y + 0.07}>{index + 1}</text>
        </g>
      ))}
      {pending && (
        <text x=".25" y={Math.max(Number(store?.height_m) || 12, 1) - 0.25} className="calibration-hint">
          Now click the same spot here
        </text>
      )}
    </MapSurface>
  );
}

export function CalibrationModal({ source, store, zones, sources, onClose, onSaved }) {
  const imageRef = useRef(null);
  const mapRef = useRef(null);
  const replayKey = source.metadata?.demo_fixture_source_key || "";
  const [pairs, setPairs] = useState(source.calibration?.points || []);
  const [pending, setPending] = useState(null);
  const [frameUrl, setFrameUrl] = useState(replayKey ? assetUrl(`/demo/media/${replayKey}.mp4`) : "");
  const [frameKind] = useState(replayKey ? "video" : "image");
  const [frame, setFrame] = useState({
    width: source.calibration?.frame_w || 0, height: source.calibration?.frame_h || 0,
  });
  const [mode, setMode] = useState("pair");
  const [testPoint, setTestPoint] = useState(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [practiceResult, setPracticeResult] = useState(null);

  useEffect(() => () => {
    if (frameUrl?.startsWith("blob:")) URL.revokeObjectURL(frameUrl);
  }, [frameUrl]);

  // Real pair count, so a guided walkthrough follows the actual exercise.
  useEffect(() => {
    reportTourEvent({ kind: "calibration-progress", pairs: pairs.length });
  }, [pairs.length]);

  const frameClick = async (event) => {
    const image = imageRef.current;
    const naturalWidth = image?.naturalWidth || image?.videoWidth;
    const naturalHeight = image?.naturalHeight || image?.videoHeight;
    if (!image || !naturalWidth || !naturalHeight) return;
    const rect = image.getBoundingClientRect();
    const point = {
      x: ((event.clientX - rect.left) / rect.width) * naturalWidth,
      y: ((event.clientY - rect.top) / rect.height) * naturalHeight,
    };
    if (mode === "test") {
      setError("");
      try {
        const result = await api.post(`/sources/${source.id}/project`, { points: [point] });
        setTestPoint(result.points[0]);
      } catch (err) { setError(err.message); }
    } else {
      setPending(point);
    }
  };

  const mapClick = (event) => {
    if (!pending) return;
    const point = eventPoint(event, mapRef.current);
    if (!point) return;
    setPairs((current) => [...current, { px: pending, map: point }]);
    setPending(null);
  };

  const save = async () => {
    setSaving(true);
    setError("");
    try {
      if (pairs.length < 4) throw new Error("Match at least four points.");
      if (!frame.width || !frame.height) throw new Error("Choose the picture to calibrate from.");
      await api.put(`/sources/${source.id}/calibration`, {
        points: pairs, frame_w: frame.width, frame_h: frame.height,
      });
      if (replayKey && demoSessionId()) {
        const result = await api.post(
          `/demo/sessions/${demoSessionId()}/restore-practice-calibration`, { source_id: source.id },
        );
        setPracticeResult(result);
        reportTourEvent({
          kind: "practice-calibration-restored", at: Date.now(), sourceId: source.id,
          cameraIndex: sources.findIndex((item) => item.id === source.id) + 1,
        });
      }
      await onSaved();
      setMode("test");
    } catch (err) { setError(err.message); } finally { setSaving(false); }
  };

  const clear = async () => {
    if (!window.confirm(`Clear the calibration for ${source.name}?`)) return;
    await api.del(`/sources/${source.id}/calibration`);
    setPairs([]);
    setPending(null);
    setTestPoint(null);
    await onSaved();
    setMode("pair");
  };

  return (
    <Modal
      wide
      title={`Calibrate ${source.name}`}
      description="Match fixed points in the camera image with the same points on the floor map."
      onClose={onClose}
      footer={
        <>
          <button className="button button-secondary" onClick={onClose}>Close</button>
          {source.calibrated && (
            <button className="button button-ghost destructive" onClick={clear}>
              <Trash2 size={14} aria-hidden="true" /> Clear calibration
            </button>
          )}
          <button className="button button-primary" onClick={save}
                  disabled={saving || pairs.length < 4 || !frameUrl}>
            <Save size={14} aria-hidden="true" /> {saving ? "Saving…" : "Save calibration"}
          </button>
        </>
      }
    >
      <div className="calibration-intro">
        <span>{pairs.length} of 4 points matched</span>
        <label className="button button-secondary">
          <FileUp size={14} aria-hidden="true" />
          {frameUrl ? "Use another picture" : "Choose a picture"}
          <input
            type="file" accept="image/*" hidden
            onChange={(event) => {
              const file = event.target.files?.[0];
              if (!file) return;
              setFrameUrl(URL.createObjectURL(file));
              setPending(null);
              setTestPoint(null);
            }}
          />
        </label>
      </div>
      <div className="calibration-mode" role="tablist" aria-label="Calibration step">
        <button role="tab" aria-selected={mode === "pair"} className={mode === "pair" ? "active" : ""}
                onClick={() => { setMode("pair"); setTestPoint(null); }}>
          1. Match points
        </button>
        <button role="tab" aria-selected={mode === "test"} className={mode === "test" ? "active" : ""}
                disabled={!source.calibrated} onClick={() => { setMode("test"); setPending(null); }}>
          2. Test it
        </button>
      </div>
      <div className="calibration-status" role="status">
        {!frameUrl ? "Choose a picture from this camera."
          : mode === "test" ? "Click a spot on the floor in the camera image."
            : pending ? "Now click the same spot on the floor map."
              : "Click a fixed point on the floor in the camera image."}
      </div>
      <div className="calibration-grid">
        <section>
          <h4>Camera</h4>
          {frameUrl ? (
            <div
              className={`calibration-frame ${pending ? "has-pending" : ""}`}
              onClick={frameClick}
              style={frame.width && frame.height
                ? { aspectRatio: `${frame.width} / ${frame.height}` } : undefined}
            >
              {frameKind === "video" ? (
                <video
                  ref={imageRef} src={frameUrl} muted playsInline controls preload="metadata"
                  aria-label={`Recorded footage from ${source.name}`}
                  onLoadedMetadata={(event) => setFrame({
                    width: event.currentTarget.videoWidth, height: event.currentTarget.videoHeight,
                  })}
                />
              ) : (
                <img
                  ref={imageRef} src={frameUrl} alt={`Picture from ${source.name}`}
                  onLoad={(event) => setFrame({
                    width: event.currentTarget.naturalWidth, height: event.currentTarget.naturalHeight,
                  })}
                />
              )}
              {pairs.map((pair, index) => (
                <span key={index} className="calibration-frame-point" style={{
                  left: `${(pair.px.x / (frame.width || 1)) * 100}%`,
                  top: `${(pair.px.y / (frame.height || 1)) * 100}%`,
                }}>{index + 1}</span>
              ))}
              {pending && (
                <span className="calibration-frame-point pending" style={{
                  left: `${(pending.x / (frame.width || 1)) * 100}%`,
                  top: `${(pending.y / (frame.height || 1)) * 100}%`,
                }}>+</span>
              )}
            </div>
          ) : (
            <div className="calibration-frame calibration-frame-empty">
              Choose a picture to begin.
            </div>
          )}
        </section>
        <section>
          <h4>Floor map</h4>
          <CalibrationMap store={store} zones={zones} sources={sources} svgRef={mapRef}
                          onClick={mapClick} points={pairs} pending={pending} testPoint={testPoint} />
        </section>
      </div>
      {source.calibrated && (
        <div className="quality-note">
          <CheckCircle2 size={15} aria-hidden="true" />
          <div>
            <strong>Accurate to about ±{Number(source.calibration?.error_m || 0).toFixed(2)} m</strong>
            <p>Test a few points before relying on it.</p>
          </div>
        </div>
      )}
      {practiceResult && (
        <div className="quality-note">
          <CheckCircle2 size={15} aria-hidden="true" />
          <div>
            <strong>Your calibration was compared with the prepared one</strong>
            <p>
              Average difference {practiceResult.comparison.mean_difference_m.toFixed(2)} m.
              The demo restored its own calibration so the recorded replay stays accurate.
            </p>
          </div>
        </div>
      )}
      {!!pairs.length && (
        <TechnicalDetails summary="Matched points">
          <p className="technical-note">Picture {frame.width} × {frame.height} px</p>
          <ol className="calibration-pair-list">
            {pairs.map((pair, index) => (
              <li key={index}>
                <span>{index + 1}</span>
                <code>px {Math.round(pair.px.x)}, {Math.round(pair.px.y)}</code>
                <code>map {pair.map.x.toFixed(2)}, {pair.map.y.toFixed(2)} m</code>
                <button className="icon-button" aria-label={`Remove point ${index + 1}`}
                        onClick={() => setPairs((current) =>
                          current.filter((_, itemIndex) => itemIndex !== index))}>
                  <X size={14} />
                </button>
              </li>
            ))}
          </ol>
        </TechnicalDetails>
      )}
      {error && <div className="form-error" role="alert">{error}</div>}
    </Modal>
  );
}

/** Placement + calibration for one camera, used by Setup › Cameras. */
export function CameraDetail({ source, store, zones, sources, onRefresh, notify }) {
  const [draft, setDraft] = useState(source.placement || null);
  const [busy, setBusy] = useState(false);
  const [calibrating, setCalibrating] = useState(false);
  useEffect(() => { setDraft(source.placement || null); }, [source.id, source.placement]);

  const dirty = Boolean(source.placement && draft
    && (draft.rotation_deg !== source.placement.rotation_deg
      || draft.fov_deg !== source.placement.fov_deg));

  const savePlacement = async () => {
    setBusy(true);
    try {
      await api.put(`/sources/${source.id}/placement`, draft);
      await onRefresh();
      notify("Camera updated", source.name);
    } catch (err) { notify("Couldn't save", err.message, "error"); } finally { setBusy(false); }
  };
  const clearPlacement = async () => {
    if (!window.confirm(`Remove ${source.name} from the map?`)) return;
    try {
      await api.del(`/sources/${source.id}/placement`);
      await onRefresh();
      notify("Camera removed from the map", source.name);
    } catch (err) { notify("Couldn't remove it", err.message, "error"); }
  };

  const index = sources.findIndex((item) => item.id === source.id) + 1;
  return (
    <div className="camera-detail stack">
      {!source.placement ? (
        <EmptyState title="Not on the map yet">
          Place this camera on the floor map in the Space tab, then calibrate it here.
        </EmptyState>
      ) : (
        <>
          <label className="range-field">
            <span>Direction <strong>{Math.round(draft?.rotation_deg ?? 0)}°</strong></span>
            <input type="range" min="-180" max="180" value={draft?.rotation_deg ?? 0}
                   onChange={(event) => setDraft((current) => ({
                     ...current, rotation_deg: Number(event.target.value),
                   }))} />
          </label>
          <label className="range-field">
            <span>Field of view <strong>{Math.round(draft?.fov_deg ?? 70)}°</strong></span>
            <input type="range" min="20" max="160" value={draft?.fov_deg ?? 70}
                   onChange={(event) => setDraft((current) => ({
                     ...current, fov_deg: Number(event.target.value),
                   }))} />
          </label>
          <div className="card-actions">
            <button className="button button-secondary" onClick={savePlacement}
                    disabled={!dirty || busy}>
              <Save size={14} aria-hidden="true" /> Save
            </button>
            <button
              className="button button-primary"
              data-demo-tour={`camera-calibrate-${index}`}
              onClick={() => {
                setCalibrating(true);
                reportTourEvent({ kind: "calibration-open", cameraIndex: index });
              }}
            >
              <Crosshair size={14} aria-hidden="true" />
              {source.calibrated ? "Review calibration" : "Calibrate camera"}
            </button>
            <OverflowMenu
              label={`More actions for ${source.name}`}
              items={[{ label: "Remove from map", destructive: true, onSelect: clearPlacement }]}
            />
          </div>
          <div className="status-row">
            <span>Calibration</span><StatusPill status={calibrationStatus(source)} compact />
          </div>
        </>
      )}
      {calibrating && (
        <CalibrationModal
          source={source}
          store={store}
          zones={zones}
          sources={sources}
          onClose={() => { setCalibrating(false); reportTourEvent({ kind: "calibration-closed" }); }}
          onSaved={async () => {
            await onRefresh();
            notify("Calibration saved", source.name);
          }}
        />
      )}
    </div>
  );
}
