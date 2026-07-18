import { useEffect, useMemo, useRef, useState } from "react";
import {
  Camera,
  CheckCircle2,
  Eraser,
  MapPin,
  MousePointer2,
  Pencil,
  Plus,
  RotateCcw,
  Ruler,
  Save,
  ScanLine,
  Trash2,
  Type,
  X,
} from "lucide-react";
import { api } from "./api.js";
import { Badge, EmptyState, Modal, Panel } from "./components.jsx";

const ZONE_TYPES = [
  "area",
  "entrance",
  "checkout",
  "queue",
  "aisle",
  "stockroom",
  "restricted",
  "equipment",
  "hall",
  "classroom",
  "playground",
  "meeting_room",
  "custom",
];

const TOOLS = [
  ["select", "Select", MousePointer2],
  ["wall", "Wall", Pencil],
  ["zone", "Zone", ScanLine],
  ["label", "Label", Type],
  ["camera", "Place camera", Camera],
  ["erase", "Erase", Eraser],
];

function polygon(points = []) {
  return points.map((point) => `${point.x},${point.y}`).join(" ");
}

function eventPoint(event, svg) {
  if (!svg) return null;
  const point = svg.createSVGPoint();
  point.x = event.clientX;
  point.y = event.clientY;
  const transform = svg.getScreenCTM();
  if (!transform) return null;
  const result = point.matrixTransform(transform.inverse());
  return {
    x: Math.round(result.x * 100) / 100,
    y: Math.round(result.y * 100) / 100,
  };
}

function centroid(points = []) {
  if (!points.length) return { x: 0, y: 0 };
  return points.reduce(
    (sum, point) => ({
      x: sum.x + point.x / points.length,
      y: sum.y + point.y / points.length,
    }),
    { x: 0, y: 0 },
  );
}

function CameraGlyph({ source, selected, onClick }) {
  if (!source.placement) return null;
  const {
    x,
    y,
    rotation_deg: rotation = 0,
    fov_deg: fov = 70,
  } = source.placement;
  const length = 2.1;
  const a = ((rotation - fov / 2) * Math.PI) / 180;
  const b = ((rotation + fov / 2) * Math.PI) / 180;
  const wedge = `${x},${y} ${x + Math.cos(a) * length},${y + Math.sin(a) * length} ${x + Math.cos(b) * length},${y + Math.sin(b) * length}`;
  return (
    <g
      className={`workbench-camera ${selected ? "selected" : ""}`}
      onClick={(event) => {
        event.stopPropagation();
        onClick?.();
      }}
      role="button"
      tabIndex="0"
      aria-label={`Select ${source.name}`}
      onKeyDown={(event) => {
        if (event.key === "Enter" || event.key === " ") onClick?.();
      }}
    >
      <polygon points={wedge} className="camera-fov" />
      <circle cx={x} cy={y} r=".19" />
      <path
        d={`M ${x} ${y} L ${x + Math.cos((rotation * Math.PI) / 180) * 0.52} ${y + Math.sin((rotation * Math.PI) / 180) * 0.52}`}
      />
      <text x={x} y={y - 0.32}>
        {source.name}
      </text>
    </g>
  );
}

function MapSurface({
  store,
  zones,
  sources,
  draft,
  selectedSourceId,
  tool,
  onMapClick,
  onSourceSelect,
  onEraseWall,
  onEraseLabel,
  svgRef,
  testPoint,
  children,
}) {
  const width = Math.max(Number(store?.width_m) || 20, 1);
  const height = Math.max(Number(store?.height_m) || 12, 1);
  return (
    <div className="map-workbench-canvas">
      <svg
        ref={svgRef}
        viewBox={`-.6 -.6 ${width + 1.2} ${height + 1.2}`}
        onClick={onMapClick}
        aria-label="Interactive floor map editor"
        role="img"
      >
        <defs>
          <pattern
            id="workbench-grid"
            width="1"
            height="1"
            patternUnits="userSpaceOnUse"
          >
            <path d="M 1 0 L 0 0 0 1" />
          </pattern>
        </defs>
        <rect
          x="0"
          y="0"
          width={width}
          height={height}
          className="workbench-floor"
        />
        <rect
          x="0"
          y="0"
          width={width}
          height={height}
          fill="url(#workbench-grid)"
          className="workbench-grid"
        />
        {zones.map((zone) => {
          const center = centroid(zone.polygon);
          return (
            <g key={zone.id} className="workbench-zone">
              <polygon
                points={polygon(zone.polygon)}
                fill={`${zone.color}24`}
                stroke={zone.color}
              />
              <text x={center.x} y={center.y}>
                {zone.name}
              </text>
            </g>
          );
        })}
        {(store?.map?.walls || []).map((wall, index) => (
          <polyline
            key={`wall-${index}`}
            points={polygon(wall)}
            className={`workbench-wall ${tool === "erase" ? "erasable" : ""}`}
            onClick={(event) => {
              if (tool === "erase") {
                event.stopPropagation();
                onEraseWall(index);
              }
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
              if (tool === "erase") {
                event.stopPropagation();
                onEraseLabel(index);
              }
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
            onClick={() => onSourceSelect(source.id)}
          />
        ))}
        {!!draft.length && (
          <g className="workbench-draft">
            <polyline points={polygon(draft)} />
            {tool === "zone" && draft.length > 2 && (
              <line
                x1={draft.at(-1).x}
                y1={draft.at(-1).y}
                x2={draft[0].x}
                y2={draft[0].y}
              />
            )}
            {draft.map((point, index) => (
              <circle key={index} cx={point.x} cy={point.y} r=".11" />
            ))}
          </g>
        )}
        {testPoint && (
          <g className="test-map-point">
            <circle cx={testPoint.x} cy={testPoint.y} r=".25" />
            <path
              d={`M ${testPoint.x - 0.45} ${testPoint.y} H ${testPoint.x + 0.45} M ${testPoint.x} ${testPoint.y - 0.45} V ${testPoint.y + 0.45}`}
            />
          </g>
        )}
        {children}
      </svg>
      <div className="map-scale">
        <Ruler size={13} /> Grid spacing: 1 metre
      </div>
    </div>
  );
}

export function SpaceWorkbench({ store, zones, sources, onRefresh, notify }) {
  const svgRef = useRef(null);
  const [tool, setTool] = useState("select");
  const [draft, setDraft] = useState([]);
  const [selectedSourceId, setSelectedSourceId] = useState(
    sources[0]?.id || null,
  );
  const [zoneDraft, setZoneDraft] = useState(null);
  const [labelDraft, setLabelDraft] = useState(null);
  const [editingZone, setEditingZone] = useState(null);
  const [placementDraft, setPlacementDraft] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const selectedSource =
    sources.find((source) => source.id === selectedSourceId) || null;

  useEffect(() => {
    if (!selectedSourceId && sources.length) setSelectedSourceId(sources[0].id);
  }, [sources, selectedSourceId]);

  useEffect(() => {
    setPlacementDraft(selectedSource?.placement || null);
  }, [
    selectedSourceId,
    selectedSource?.placement?.x,
    selectedSource?.placement?.y,
    selectedSource?.placement?.rotation_deg,
    selectedSource?.placement?.fov_deg,
  ]);

  const displaySources = useMemo(
    () =>
      sources.map((source) =>
        source.id === selectedSourceId && placementDraft
          ? { ...source, placement: placementDraft }
          : source,
      ),
    [sources, selectedSourceId, placementDraft],
  );
  const placementDirty = Boolean(
    selectedSource?.placement &&
      placementDraft &&
      (placementDraft.rotation_deg !== selectedSource.placement.rotation_deg ||
        placementDraft.fov_deg !== selectedSource.placement.fov_deg),
  );

  const instruction = {
    select:
      "Select a placed camera on the map or in the list to adjust its field of view.",
    wall: "Click each wall corner. Finish with the button below or press Enter.",
    zone: "Click at least three corners around the operating area, then finish the polygon.",
    label: "Click the map where the label should appear.",
    camera: selectedSource
      ? `Click the map to place ${selectedSource.name}.`
      : "Register a source before placing a camera.",
    erase:
      "Select a wall or label on the map to remove it. Zones are managed in the list.",
  }[tool];

  useEffect(() => {
    const onKeyDown = (event) => {
      if (event.key === "Escape") setDraft([]);
      if (event.target.closest?.("input, select, textarea, [role='dialog']"))
        return;
      if (event.key === "Enter" && (tool === "wall" || tool === "zone"))
        finishDraft();
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
      notify(message, "The floor map is up to date.");
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };

  const finishDraft = () => {
    if (tool === "wall") {
      if (draft.length < 2) {
        setError("A wall needs at least two points.");
        return;
      }
      const map = store.map || {};
      saveMap({ ...map, walls: [...(map.walls || []), draft] }, "Wall added");
      setDraft([]);
    } else if (tool === "zone") {
      if (draft.length < 3) {
        setError("A zone needs at least three points.");
        return;
      }
      setZoneDraft(draft);
      setDraft([]);
    }
  };

  const mapClick = async (event) => {
    const point = eventPoint(event, svgRef.current);
    if (!point) return;
    if (tool === "wall" || tool === "zone")
      setDraft((current) => [...current, point]);
    if (tool === "label") setLabelDraft(point);
    if (tool === "camera" && selectedSource) {
      setBusy(true);
      setError("");
      try {
        await api.put(`/sources/${selectedSource.id}/placement`, {
          x: point.x,
          y: point.y,
          rotation_deg: selectedSource.placement?.rotation_deg || 0,
          fov_deg: selectedSource.placement?.fov_deg || 70,
        });
        await onRefresh();
        notify(
          "Camera placed",
          `${selectedSource.name} is positioned on the map.`,
        );
        setTool("select");
      } catch (err) {
        setError(err.message);
      } finally {
        setBusy(false);
      }
    }
  };

  const addLabel = async (text) => {
    const map = store.map || {};
    await saveMap(
      { ...map, labels: [...(map.labels || []), { ...labelDraft, text }] },
      "Label added",
    );
    setLabelDraft(null);
    setTool("select");
  };

  const eraseWall = async (index) => {
    if (!window.confirm("Remove this wall from the floor map?")) return;
    const map = store.map || {},
      walls = [...(map.walls || [])];
    walls.splice(index, 1);
    await saveMap({ ...map, walls }, "Wall removed");
  };

  const eraseLabel = async (index) => {
    if (!window.confirm("Remove this label from the floor map?")) return;
    const map = store.map || {},
      labels = [...(map.labels || [])];
    labels.splice(index, 1);
    await saveMap({ ...map, labels }, "Label removed");
  };

  const savePlacement = async () => {
    if (!selectedSource?.placement || !placementDraft) return;
    setBusy(true);
    setError("");
    try {
      await api.put(`/sources/${selectedSource.id}/placement`, placementDraft);
      await onRefresh();
      notify(
        "Camera view saved",
        `${selectedSource.name} direction and field of view were updated.`,
      );
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };

  const clearPlacement = async () => {
    if (
      !selectedSource ||
      !window.confirm(`Remove ${selectedSource.name} from the floor map?`)
    )
      return;
    await api.del(`/sources/${selectedSource.id}/placement`);
    await onRefresh();
    notify("Camera unplaced", selectedSource.name);
  };

  const deleteZone = async (zone) => {
    if (
      !window.confirm(
        `Delete ${zone.name}? Existing events keep their stored zone ID.`,
      )
    )
      return;
    await api.del(`/zones/${zone.id}`);
    await onRefresh();
    notify("Zone deleted", zone.name);
  };

  return (
    <div className="space-workbench stack">
      <Panel
        title="Floor map workbench"
        subtitle="Draw geometry in metres and place logical source markers"
      >
        <div
          className="workbench-toolbar"
          role="toolbar"
          aria-label="Floor map tools"
        >
          {TOOLS.map(([value, label, Icon]) => (
            <button
              key={value}
              className={tool === value ? "active" : ""}
              onClick={() => {
                setTool(value);
                setDraft([]);
                setError("");
              }}
              aria-pressed={tool === value}
              disabled={busy || (value === "camera" && !sources.length)}
            >
              <Icon size={15} />
              {label}
            </button>
          ))}
        </div>
        <div className="workbench-status" role="status">
          <Crosshair size={15} />
          <span>
            <strong>{TOOLS.find(([value]) => value === tool)?.[1]}</strong>
            {instruction}
          </span>
          {draft.length > 0 && (
            <Badge tone="violet">
              {draft.length} point{draft.length === 1 ? "" : "s"}
            </Badge>
          )}
        </div>
        {sources.length > 0 && tool === "camera" && (
          <label className="field compact-field">
            <span>Camera to place</span>
            <select
              value={selectedSourceId || ""}
              onChange={(event) =>
                setSelectedSourceId(Number(event.target.value))
              }
            >
              {sources.map((source) => (
                <option key={source.id} value={source.id}>
                  {source.name}
                </option>
              ))}
            </select>
          </label>
        )}
        <MapSurface
          store={store}
          zones={zones}
          sources={displaySources}
          draft={draft}
          selectedSourceId={selectedSourceId}
          tool={tool}
          onMapClick={mapClick}
          onSourceSelect={setSelectedSourceId}
          onEraseWall={eraseWall}
          onEraseLabel={eraseLabel}
          svgRef={svgRef}
        />
        {(tool === "wall" || tool === "zone") && (
          <div className="draft-actions">
            <button
              className="button button-secondary"
              onClick={() => setDraft([])}
              disabled={!draft.length}
            >
              <RotateCcw size={14} />
              Clear points
            </button>
            <button
              className="button button-dark"
              onClick={finishDraft}
              disabled={busy || draft.length < (tool === "zone" ? 3 : 2)}
            >
              <CheckCircle2 size={14} />
              Finish {tool}
            </button>
          </div>
        )}
        {error && (
          <div className="form-error" role="alert">
            {error}
          </div>
        )}
      </Panel>

      <div className="space-detail-grid">
        <Panel
          title={`Zones · ${zones.length}`}
          subtitle="Semantic areas drive dwell, flow, occupancy, and thresholds"
          action={
            <button
              className="button button-secondary"
              onClick={() => {
                setTool("zone");
                setDraft([]);
              }}
            >
              <Plus size={14} />
              Draw zone
            </button>
          }
        >
          <div className="data-list">
            {zones.map((zone) => (
              <div key={zone.id}>
                <span
                  className="zone-swatch"
                  style={{ background: zone.color }}
                />
                <div>
                  <strong>{zone.name}</strong>
                  <small>
                    {zone.ztype.replaceAll("_", " ")} · {zone.polygon.length}{" "}
                    corners
                  </small>
                </div>
                <button
                  className="icon-button"
                  onClick={() => setEditingZone(zone)}
                  aria-label={`Edit ${zone.name}`}
                >
                  <Pencil size={15} />
                </button>
                <button
                  className="icon-button danger"
                  onClick={() => deleteZone(zone)}
                  aria-label={`Delete ${zone.name}`}
                >
                  <Trash2 size={15} />
                </button>
              </div>
            ))}
            {!zones.length && (
              <EmptyState title="No zones yet">
                Choose Draw zone, click the floor-map corners, and finish the
                polygon.
              </EmptyState>
            )}
          </div>
        </Panel>
        <Panel
          title={`Sources · ${sources.length}`}
          subtitle="Map placement is descriptive; workers configure pixel geometry from locally captured frames"
        >
          <div className="data-list">
            {sources.map((source) => (
              <button
                key={source.id}
                className={`camera-list-row ${source.id === selectedSourceId ? "selected" : ""}`}
                onClick={() => setSelectedSourceId(source.id)}
              >
                <span
                  className={`status-light ${source.observation_status === "active" ? "active" : "paused"}`}
                />
                <span>
                  <strong>{source.name}</strong>
                  <small>
                    {source.placement ? "Placed" : "Not placed"} ·{" "}
                    {source.calibrated
                      ? `Calibrated ±${Number(source.calibration?.error_m || 0).toFixed(2)}m`
                      : "Not calibrated"}
                  </small>
                </span>
                <Badge tone={source.calibrated ? "positive" : "warning"}>
                  {source.calibrated ? "ready" : "setup"}
                </Badge>
              </button>
            ))}
            {!sources.length && (
              <EmptyState title="Register a source first">
                An agent can create a logical source through StoreLens MCP before
                placing it on the map.
              </EmptyState>
            )}
          </div>
          {selectedSource && (
            <div className="camera-controls">
              <div className="section-heading">
                <div>
                  <span className="tiny-label">Selected camera</span>
                  <h3>{selectedSource.name}</h3>
                </div>
                {selectedSource.placement && (
                  <Badge tone="positive">
                    <MapPin size={12} />
                    Placed
                  </Badge>
                )}
              </div>
              {!selectedSource.placement ? (
                <button
                  className="button button-dark"
                  onClick={() => setTool("camera")}
                >
                  <MapPin size={14} />
                  Place on map
                </button>
              ) : (
                <>
                  <label className="range-field">
                    <span>
                      Direction{" "}
                      <strong>
                        {Math.round(
                          placementDraft?.rotation_deg ??
                            selectedSource.placement.rotation_deg,
                        )}
                        °
                      </strong>
                    </span>
                    <input
                      type="range"
                      min="-180"
                      max="180"
                      value={
                        placementDraft?.rotation_deg ??
                        selectedSource.placement.rotation_deg
                      }
                      onChange={(event) =>
                        setPlacementDraft((current) => ({
                          ...current,
                          rotation_deg: Number(event.target.value),
                        }))
                      }
                    />
                  </label>
                  <label className="range-field">
                    <span>
                      Field of view{" "}
                      <strong>
                        {Math.round(
                          placementDraft?.fov_deg ??
                            selectedSource.placement.fov_deg,
                        )}
                        °
                      </strong>
                    </span>
                    <input
                      type="range"
                      min="20"
                      max="160"
                      value={
                        placementDraft?.fov_deg ??
                        selectedSource.placement.fov_deg
                      }
                      onChange={(event) =>
                        setPlacementDraft((current) => ({
                          ...current,
                          fov_deg: Number(event.target.value),
                        }))
                      }
                    />
                  </label>
                  <div className="card-actions">
                    <button
                      className="button button-secondary"
                      onClick={savePlacement}
                      disabled={!placementDirty || busy}
                    >
                      <Save size={14} />
                      Save view
                    </button>
                    <button
                      className="button button-ghost danger"
                      onClick={clearPlacement}
                    >
                      <X size={14} />
                      Unplace
                    </button>
                  </div>
                  <p className="definition-note">
                    Calibration, decision ROIs, and projection surfaces are configured
                    by the agent from a frame captured on the worker device. The hosted
                    dashboard does not request camera frames.
                  </p>
                </>
              )}
            </div>
          )}
        </Panel>
      </div>
      {zoneDraft && (
        <ZoneEditorModal
          polygonPoints={zoneDraft}
          onClose={() => setZoneDraft(null)}
          onSaved={async () => {
            setZoneDraft(null);
            setTool("select");
            await onRefresh();
            notify(
              "Zone created",
              "Events can now be assigned to this operating area.",
            );
          }}
        />
      )}
      {editingZone && (
        <ZoneEditorModal
          zone={editingZone}
          polygonPoints={editingZone.polygon}
          onClose={() => setEditingZone(null)}
          onSaved={async () => {
            setEditingZone(null);
            await onRefresh();
            notify("Zone updated", "The zone definition is now active.");
          }}
        />
      )}
      {labelDraft && (
        <LabelModal onClose={() => setLabelDraft(null)} onSave={addLabel} />
      )}
    </div>
  );
}

function ZoneEditorModal({ zone, polygonPoints, onClose, onSaved }) {
  const [form, setForm] = useState({
    name: zone?.name || "",
    ztype: zone?.ztype || "area",
    color: zone?.color || "#7059ff",
  });
  const [saving, setSaving] = useState(false),
    [error, setError] = useState("");
  const save = async () => {
    setSaving(true);
    setError("");
    try {
      if (!form.name.trim())
        throw new Error("Give the zone a clear operational name.");
      const body = {
        name: form.name.trim(),
        ztype: form.ztype,
        color: form.color,
        polygon: polygonPoints,
      };
      zone
        ? await api.put(`/zones/${zone.id}`, body)
        : await api.post("/zones", body);
      onSaved();
    } catch (err) {
      setError(err.message);
    } finally {
      setSaving(false);
    }
  };
  return (
    <Modal
      title={zone ? `Edit ${zone.name}` : "Name this zone"}
      onClose={onClose}
      footer={
        <>
          <button className="button button-secondary" onClick={onClose}>
            Cancel
          </button>
          <button
            className="button button-dark"
            onClick={save}
            disabled={saving}
          >
            <Save size={14} />
            {saving ? "Saving…" : "Save zone"}
          </button>
        </>
      }
    >
      <div className="form-grid">
        <label className="field field-full">
          <span>Zone name</span>
          <input
            autoFocus
            value={form.name}
            onChange={(event) => setForm({ ...form, name: event.target.value })}
            placeholder="Main hall"
          />
        </label>
        <label className="field">
          <span>Operational type</span>
          <select
            value={form.ztype}
            onChange={(event) =>
              setForm({ ...form, ztype: event.target.value })
            }
          >
            {ZONE_TYPES.map((type) => (
              <option key={type} value={type}>
                {type.replaceAll("_", " ")}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span>Map colour</span>
          <input
            type="color"
            value={form.color}
            onChange={(event) =>
              setForm({ ...form, color: event.target.value })
            }
          />
        </label>
      </div>
      <p className="form-note">
        {polygonPoints.length} polygon corners. Names should describe a real
        operating area used by an analysis or threshold.
      </p>
      {error && (
        <div className="form-error" role="alert">
          {error}
        </div>
      )}
    </Modal>
  );
}

function LabelModal({ onClose, onSave }) {
  const [text, setText] = useState(""),
    [error, setError] = useState("");
  const save = () => {
    if (!text.trim()) {
      setError("Enter label text.");
      return;
    }
    onSave(text.trim());
  };
  return (
    <Modal
      title="Add map label"
      onClose={onClose}
      footer={
        <>
          <button className="button button-secondary" onClick={onClose}>
            Cancel
          </button>
          <button className="button button-dark" onClick={save}>
            <Type size={14} />
            Add label
          </button>
        </>
      }
    >
      <label className="field">
        <span>Label text</span>
        <input
          autoFocus
          value={text}
          onChange={(event) => setText(event.target.value)}
          placeholder="Staff only"
        />
      </label>
      {error && (
        <div className="form-error" role="alert">
          {error}
        </div>
      )}
    </Modal>
  );
}
