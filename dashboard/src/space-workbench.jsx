import { useEffect, useMemo, useRef, useState } from "react";
import {
  Camera,
  CheckCircle2,
  Crosshair,
  Eraser,
  ExternalLink,
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
import { PlanDigitizer } from "./plan-digitizer.jsx";

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
      <circle className="camera-focus-ring" cx={x} cy={y} r=".31" />
      <circle className="camera-position" cx={x} cy={y} r=".19" />
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
        {(store?.map?.floor_polygons || []).map((floor, index) => (
          <polygon
            key={`imported-floor-${index}`}
            points={polygon(floor)}
            className="workbench-imported-floor"
          />
        ))}
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
  const [calibrating, setCalibrating] = useState(null);
  const [showSourceCreator, setShowSourceCreator] = useState(false);
  const [placementDraft, setPlacementDraft] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const selectedSource =
    sources.find((source) => source.id === selectedSourceId) || null;

  useEffect(() => {
    if (!sources.length) setSelectedSourceId(null);
    else if (!sources.some((source) => source.id === selectedSourceId))
      setSelectedSourceId(sources[0].id);
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
    try {
      await api.del(`/sources/${selectedSource.id}/placement`);
      await onRefresh();
      notify("Camera unplaced", selectedSource.name);
    } catch (err) {
      notify("Couldn't unplace camera", err.message, "error");
    }
  };

  const deleteZone = async (zone) => {
    if (
      !window.confirm(
        `Delete ${zone.name}? Existing events keep their stored zone ID.`,
      )
    )
      return;
    try {
      await api.del(`/zones/${zone.id}`);
      await onRefresh();
      notify("Zone deleted", zone.name);
    } catch (err) {
      notify("Couldn't delete zone", err.message, "error");
    }
  };

  const deleteSource = async () => {
    if (!selectedSource) return;
    if (!window.confirm(
      `Delete ${selectedSource.name}? Its placement, calibration, zone views, and projection surfaces will be removed. Historical observations retain their source ID.`,
    )) return;
    try {
      await api.del(`/sources/${selectedSource.id}`);
      localStorage.removeItem(`storelens.local-preview.${selectedSource.id}`);
      setSelectedSourceId(null);
      await onRefresh();
      notify("Source deleted", selectedSource.name);
    } catch (err) {
      notify("Couldn't delete source", err.message, "error");
    }
  };

  return (
    <div className="space-workbench stack">
      <PlanDigitizer onRefresh={onRefresh} notify={notify} />
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
          action={
            <button className="button button-secondary" onClick={() => setShowSourceCreator(true)}>
              <Plus size={14} /> Add source
            </button>
          }
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
              <EmptyState
                title="Add a camera source"
                action={<button className="button button-dark" onClick={() => setShowSourceCreator(true)}>Add source</button>}
              >
                Create a logical source, then place and calibrate it on the map.
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
                <div className="card-actions">
                  {selectedSource.placement && <Badge tone="positive"><MapPin size={12} /> Placed</Badge>}
                  <button className="icon-button danger" onClick={deleteSource} aria-label={`Delete ${selectedSource.name}`}><Trash2 size={15} /></button>
                </div>
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
                      className="button button-dark"
                      onClick={() => setCalibrating(selectedSource)}
                    >
                      <Crosshair size={14} />
                      {selectedSource.calibrated ? "Review calibration" : "Calibrate camera"}
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
              <LocalSourcePreview source={selectedSource} />
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
      {calibrating && (
        <CalibrationModal
          source={sources.find((item) => item.id === calibrating.id) || calibrating}
          store={store}
          zones={zones}
          sources={sources}
          onClose={() => setCalibrating(null)}
          onSaved={async () => {
            await onRefresh();
            notify("Calibration saved", "Future pixel detections can now be projected onto the floor plan.");
          }}
        />
      )}
      {showSourceCreator && (
        <SourceEditorModal
          onClose={() => setShowSourceCreator(false)}
          onSaved={async (source) => {
            setShowSourceCreator(false);
            setSelectedSourceId(source.id);
            await onRefresh();
            notify("Source created", `${source.name} is ready to place and calibrate.`);
          }}
        />
      )}
    </div>
  );
}

export function LocalSourcePreview({ source }) {
  const storageKey = `storelens.local-preview.${source.id}`;
  const demoAddress = source.locator?.local_secret_ref === "STORELENS_DEMO_STREAM_0"
    ? "http://127.0.0.1:8765/stream.mjpg"
    : "";
  const [address, setAddress] = useState(() => localStorage.getItem(storageKey) || demoAddress);
  const [connected, setConnected] = useState(() => localStorage.getItem(storageKey) || demoAddress);
  useEffect(() => {
    const value = localStorage.getItem(storageKey) || demoAddress;
    setAddress(value);
    setConnected(value);
  }, [storageKey, demoAddress]);
  const connect = () => {
    const value = address.trim();
    localStorage.setItem(storageKey, value);
    setConnected(value);
  };
  return (
    <div className="local-source-preview stack">
      <div className="section-heading">
        <div>
          <span className="tiny-label">Local demo footage</span>
          <h3>Browser preview</h3>
        </div>
      </div>
      <label className="field">
        <span>Local player address</span>
        <input value={address} onChange={(event) => setAddress(event.target.value)} placeholder="http://127.0.0.1:8765/stream.mjpg" />
        <small>Saved only in this browser; never sent to the StoreLens server.</small>
      </label>
      <div className="card-actions">
        <button className="button button-secondary" onClick={connect} disabled={!address.trim()}>Connect preview</button>
        {address.trim() && <a className="button button-ghost" href={address.trim()} target="_blank" rel="noreferrer"><ExternalLink size={14} /> Open separately</a>}
      </div>
      {connected && <iframe className="local-preview-frame" src={connected} title={`Local preview for ${source.name}`} allow="autoplay; fullscreen" />}
    </div>
  );
}

export function SourceEditorModal({ onClose, onSaved }) {
  const [form, setForm] = useState({
    name: "Looped hallway camera",
    kind: "http",
    local_secret_ref: "STORELENS_DEMO_STREAM_0",
  });
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const save = async () => {
    setSaving(true);
    setError("");
    try {
      if (!form.name.trim()) throw new Error("Enter a source name.");
      if (!form.local_secret_ref.trim()) throw new Error("Enter a safe local reference.");
      const source = await api.post("/sources", {
        name: form.name.trim(),
        kind: form.kind,
        connection_mode: "agent_local",
        locator: { local_secret_ref: form.local_secret_ref.trim() },
        capabilities: ["video"],
        metadata: { demo: true },
      });
      await onSaved(source);
    } catch (err) {
      setError(err.message);
    } finally {
      setSaving(false);
    }
  };
  return (
    <Modal
      title="Add camera source"
      onClose={onClose}
      footer={
        <>
          <button className="button button-secondary" onClick={onClose}>Cancel</button>
          <button className="button button-dark" onClick={save} disabled={saving}><Save size={14} /> {saving ? "Creating…" : "Create source"}</button>
        </>
      }
    >
      <div className="form-grid">
        <label className="field field-full">
          <span>Source name</span>
          <input autoFocus value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} />
        </label>
        <label className="field">
          <span>Video type</span>
          <select value={form.kind} onChange={(event) => setForm({ ...form, kind: event.target.value })}>
            <option value="http">HTTP / MJPEG</option>
            <option value="rtsp">RTSP</option>
            <option value="webrtc">WebRTC</option>
            <option value="webcam">Webcam</option>
            <option value="file">Local file</option>
          </select>
        </label>
        <label className="field">
          <span>Local stream reference</span>
          <input value={form.local_secret_ref} onChange={(event) => setForm({ ...form, local_secret_ref: event.target.value })} />
        </label>
      </div>
      <p className="form-note">
        StoreLens saves this non-secret reference, not a URL or credentials. For this
        demo the worker resolves STORELENS_DEMO_STREAM_0 on the same machine.
      </p>
      {error && <div className="form-error" role="alert">{error}</div>}
    </Modal>
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

function CalibrationMap({ store, zones, sources, svgRef, onClick, points, pending, testPoint }) {
  return (
    <MapSurface
      store={store}
      zones={zones}
      sources={sources}
      draft={[]}
      selectedSourceId={null}
      tool="select"
      onMapClick={onClick}
      onSourceSelect={() => {}}
      onEraseWall={() => {}}
      onEraseLabel={() => {}}
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
          Click the matching map position
        </text>
      )}
    </MapSurface>
  );
}

function CalibrationModal({ source, store, zones, sources, onClose, onSaved }) {
  const imageRef = useRef(null);
  const mapRef = useRef(null);
  const [pairs, setPairs] = useState(source.calibration?.points || []);
  const [pending, setPending] = useState(null);
  const [frameUrl, setFrameUrl] = useState("");
  const [frame, setFrame] = useState({
    width: source.calibration?.frame_w || 0,
    height: source.calibration?.frame_h || 0,
  });
  const [mode, setMode] = useState("pair");
  const [testPoint, setTestPoint] = useState(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => () => {
    if (frameUrl) URL.revokeObjectURL(frameUrl);
  }, [frameUrl]);

  const chooseFrame = (file) => {
    const nextUrl = URL.createObjectURL(file);
    setFrameUrl(nextUrl);
    setPending(null);
    setTestPoint(null);
  };

  const frameClick = async (event) => {
    const image = imageRef.current;
    if (!image || !image.naturalWidth) return;
    const rect = image.getBoundingClientRect();
    const point = {
      x: ((event.clientX - rect.left) / rect.width) * image.naturalWidth,
      y: ((event.clientY - rect.top) / rect.height) * image.naturalHeight,
    };
    if (mode === "test") {
      setError("");
      try {
        const result = await api.post(`/sources/${source.id}/project`, { points: [point] });
        setTestPoint(result.points[0]);
      } catch (err) {
        setError(err.message);
      }
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
      if (pairs.length < 4) throw new Error("Add at least four matching point pairs.");
      if (!frame.width || !frame.height) throw new Error("Choose the still frame used for calibration.");
      await api.put(`/sources/${source.id}/calibration`, {
        points: pairs,
        frame_w: frame.width,
        frame_h: frame.height,
      });
      await onSaved();
      setMode("test");
    } catch (err) {
      setError(err.message);
    } finally {
      setSaving(false);
    }
  };

  const clear = async () => {
    if (!window.confirm(`Clear the saved calibration for ${source.name}?`)) return;
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
      onClose={onClose}
      footer={
        <>
          <button className="button button-secondary" onClick={onClose}>Close</button>
          {source.calibrated && (
            <button className="button button-ghost danger" onClick={clear}>
              <Trash2 size={14} /> Clear saved calibration
            </button>
          )}
          <button className="button button-dark" onClick={save} disabled={saving || pairs.length < 4 || !frameUrl}>
            <Save size={14} /> {saving ? "Computing…" : "Compute & save"}
          </button>
        </>
      }
    >
      <div className="calibration-intro">
        <div>
          <span className="tiny-label">Guided floor homography</span>
          <h3>Match the same floor points in both views</h3>
          <p>
            Upload a still from this camera, then choose fixed floor points spread
            across the visible area. Four pairs is the minimum; six or more is safer.
          </p>
        </div>
        <Badge tone={pairs.length >= 4 ? "positive" : "warning"}>{pairs.length}/4 minimum pairs</Badge>
      </div>
      <div className="card-actions">
        <label className="button button-secondary">
          <FileUp size={14} /> {frameUrl ? "Replace still frame" : "Choose still frame"}
          <input type="file" accept="image/*" hidden onChange={(event) => event.target.files?.[0] && chooseFrame(event.target.files[0])} />
        </label>
        <span className="definition-note">The image stays in this browser and is not uploaded to StoreLens.</span>
      </div>
      <div className="calibration-mode" role="tablist" aria-label="Calibration mode">
        <button className={mode === "pair" ? "active" : ""} onClick={() => { setMode("pair"); setTestPoint(null); }} role="tab" aria-selected={mode === "pair"}>1. Match points</button>
        <button className={mode === "test" ? "active" : ""} onClick={() => { setMode("test"); setPending(null); }} role="tab" aria-selected={mode === "test"} disabled={!source.calibrated}>2. Test projection</button>
      </div>
      <div className="calibration-status" role="status">
        {!frameUrl
          ? "Choose a still frame from the selected camera."
          : mode === "test"
            ? "Click a floor position in the camera frame; the projected map point appears in orange."
            : pending
              ? "Camera point selected. Click the same physical location on the floor map."
              : "Click a fixed floor point in the camera frame to begin a pair."}
      </div>
      <div className="calibration-grid">
        <section>
          <h4>Camera frame</h4>
          {frameUrl ? (
            <div className={`calibration-frame ${pending ? "has-pending" : ""}`} onClick={frameClick} style={frame.width && frame.height ? { aspectRatio: `${frame.width} / ${frame.height}` } : undefined}>
              <img
                ref={imageRef}
                src={frameUrl}
                alt={`Local calibration frame for ${source.name}`}
                onLoad={(event) => setFrame({ width: event.currentTarget.naturalWidth, height: event.currentTarget.naturalHeight })}
              />
              {pairs.map((pair, index) => (
                <span key={index} className="calibration-frame-point" style={{ left: `${(pair.px.x / (frame.width || 1)) * 100}%`, top: `${(pair.px.y / (frame.height || 1)) * 100}%` }}>{index + 1}</span>
              ))}
              {pending && <span className="calibration-frame-point pending" style={{ left: `${(pending.x / (frame.width || 1)) * 100}%`, top: `${(pending.y / (frame.height || 1)) * 100}%` }}>+</span>}
            </div>
          ) : (
            <div className="calibration-frame calibration-frame-empty">Choose a still frame to begin.</div>
          )}
        </section>
        <section>
          <h4>Floor map</h4>
          <CalibrationMap store={store} zones={zones} sources={sources} svgRef={mapRef} onClick={mapClick} points={pairs} pending={pending} testPoint={testPoint} />
        </section>
      </div>
      {!!pairs.length && (
        <div className="calibration-pairs">
          <div><strong>Point pairs</strong><span>{frame.width} × {frame.height}px frame</span></div>
          <ol>
            {pairs.map((pair, index) => (
              <li key={index}>
                <span>{index + 1}</span>
                <code>px {Math.round(pair.px.x)}, {Math.round(pair.px.y)}</code>
                <code>map {pair.map.x.toFixed(2)}, {pair.map.y.toFixed(2)}m</code>
                <button className="icon-button" onClick={() => setPairs((current) => current.filter((_, itemIndex) => itemIndex !== index))} aria-label={`Remove point pair ${index + 1}`}><X size={14} /></button>
              </li>
            ))}
          </ol>
        </div>
      )}
      {source.calibrated && (
        <div className="quality-note">
          <CheckCircle2 size={15} />
          <div>
            <strong>Saved control-point error: ±{Number(source.calibration?.error_m || 0).toFixed(2)}m</strong>
            <p>Test several floor points before trusting automatic zone assignment.</p>
          </div>
        </div>
      )}
      {error && <div className="form-error" role="alert">{error}</div>}
    </Modal>
  );
}
