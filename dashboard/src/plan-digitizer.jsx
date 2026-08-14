import { useEffect, useMemo, useRef, useState } from "react";
import {
  CheckCircle2,
  Crosshair,
  ImagePlus,
  MousePointer2,
  RotateCcw,
  Ruler,
  ScanLine,
  Undo2,
} from "lucide-react";
import { api, assetUrl, demoSessionId } from "./api.js";
import { Modal, Panel } from "./components.jsx";

const EMPTY_DRAWING = {
  polygons: [],
  current: [],
  scale: [],
  origin: null,
  rectify: [],
};

const MODES = [
  ["polygon", "Trace polygon", ScanLine],
  ["scale", "Known distance", Ruler],
  ["origin", "Set origin", Crosshair],
  ["rectify", "Perspective corners", MousePointer2],
];

const HINTS = {
  polygon: "Click around a walkable boundary, then close it. You can trace more than one polygon.",
  scale: "Click the two endpoints of a distance you know.",
  origin: "Click where metric coordinate (0, 0) should be.",
  rectify: "Click top-left, top-right, bottom-right, then bottom-left.",
};

function points(pointsList = []) {
  return pointsList.map((point) => `${point.x},${point.y}`).join(" ");
}

function svgPoint(event, svg) {
  if (!svg) return null;
  const point = svg.createSVGPoint();
  point.x = event.clientX;
  point.y = event.clientY;
  const transform = svg.getScreenCTM();
  if (!transform) return null;
  const result = point.matrixTransform(transform.inverse());
  return { x: result.x, y: result.y };
}

function solveLinear(matrix, values) {
  const rows = matrix.map((row, index) => [...row, values[index]]);
  for (let column = 0; column < values.length; column += 1) {
    let pivot = column;
    for (let row = column + 1; row < rows.length; row += 1) {
      if (Math.abs(rows[row][column]) > Math.abs(rows[pivot][column])) pivot = row;
    }
    [rows[column], rows[pivot]] = [rows[pivot], rows[column]];
    if (Math.abs(rows[column][column]) < 1e-10) throw new Error("Perspective corners are degenerate.");
    const divisor = rows[column][column];
    for (let index = column; index <= values.length; index += 1) rows[column][index] /= divisor;
    for (let row = 0; row < rows.length; row += 1) {
      if (row === column) continue;
      const factor = rows[row][column];
      for (let index = column; index <= values.length; index += 1) {
        rows[row][index] -= factor * rows[column][index];
      }
    }
  }
  return rows.map((row) => row.at(-1));
}

function homography(from, to) {
  const matrix = [];
  const values = [];
  from.forEach((point, index) => {
    const target = to[index];
    matrix.push([point.x, point.y, 1, 0, 0, 0, -target.x * point.x, -target.x * point.y]);
    values.push(target.x);
    matrix.push([0, 0, 0, point.x, point.y, 1, -target.y * point.x, -target.y * point.y]);
    values.push(target.y);
  });
  return solveLinear(matrix, values);
}

function distance(a, b) {
  return Math.hypot(b.x - a.x, b.y - a.y);
}

async function loadImage(url) {
  return new Promise((resolve, reject) => {
    const image = new Image();
    image.onload = () => resolve(image);
    image.onerror = () => reject(new Error("The plan image could not be decoded."));
    image.src = url;
  });
}

async function canvasBlob(canvas) {
  return new Promise((resolve, reject) => canvas.toBlob(
    (blob) => (blob ? resolve(blob) : reject(new Error("The corrected image could not be created."))),
    "image/png",
  ));
}

async function prepareImage(file) {
  const sourceUrl = URL.createObjectURL(file);
  try {
    const image = await loadImage(sourceUrl);
    const scale = Math.min(1, 1800 / Math.max(image.naturalWidth, image.naturalHeight));
    const width = Math.max(2, Math.round(image.naturalWidth * scale));
    const height = Math.max(2, Math.round(image.naturalHeight * scale));
    const canvas = document.createElement("canvas");
    canvas.width = width;
    canvas.height = height;
    canvas.getContext("2d").drawImage(image, 0, 0, width, height);
    const blob = await canvasBlob(canvas);
    return { url: URL.createObjectURL(blob), width, height };
  } finally {
    URL.revokeObjectURL(sourceUrl);
  }
}

async function rectifyImage(imageState, corners) {
  const [tl, tr, br, bl] = corners;
  const width = Math.max(2, Math.round(Math.max(distance(tl, tr), distance(bl, br))));
  const height = Math.max(2, Math.round(Math.max(distance(tl, bl), distance(tr, br))));
  const destination = [
    { x: 0, y: 0 }, { x: width - 1, y: 0 },
    { x: width - 1, y: height - 1 }, { x: 0, y: height - 1 },
  ];
  const h = homography(destination, corners);
  const image = await loadImage(imageState.url);
  const source = document.createElement("canvas");
  source.width = imageState.width;
  source.height = imageState.height;
  const sourceContext = source.getContext("2d", { willReadFrequently: true });
  sourceContext.drawImage(image, 0, 0, source.width, source.height);
  const sourceData = sourceContext.getImageData(0, 0, source.width, source.height).data;
  const output = document.createElement("canvas");
  output.width = width;
  output.height = height;
  const outputContext = output.getContext("2d");
  const outputImage = outputContext.createImageData(width, height);
  for (let y = 0; y < height; y += 1) {
    for (let x = 0; x < width; x += 1) {
      const denominator = h[6] * x + h[7] * y + 1;
      const sourceX = Math.round((h[0] * x + h[1] * y + h[2]) / denominator);
      const sourceY = Math.round((h[3] * x + h[4] * y + h[5]) / denominator);
      if (sourceX < 0 || sourceX >= source.width || sourceY < 0 || sourceY >= source.height) continue;
      const fromIndex = (sourceY * source.width + sourceX) * 4;
      const toIndex = (y * width + x) * 4;
      outputImage.data[toIndex] = sourceData[fromIndex];
      outputImage.data[toIndex + 1] = sourceData[fromIndex + 1];
      outputImage.data[toIndex + 2] = sourceData[fromIndex + 2];
      outputImage.data[toIndex + 3] = 255;
    }
  }
  outputContext.putImageData(outputImage, 0, 0);
  const blob = await canvasBlob(output);
  return { url: URL.createObjectURL(blob), width, height };
}

function PlanDigitizerModal({ onClose, onSaved, backgroundUrl = null }) {
  const svgRef = useRef(null);
  const ownedUrls = useRef(new Set());
  const [original, setOriginal] = useState(null);
  const [image, setImage] = useState(null);
  const [drawing, setDrawing] = useState(EMPTY_DRAWING);
  const [mode, setMode] = useState("polygon");
  const [knownDistance, setKnownDistance] = useState(0);
  const [useScaleOrigin, setUseScaleOrigin] = useState(true);
  const [yAxisUp, setYAxisUp] = useState(true);
  const [busy, setBusy] = useState(Boolean(backgroundUrl));
  const [error, setError] = useState("");

  useEffect(() => {
    if (!backgroundUrl) return undefined;
    let cancelled = false;
    const loadBackground = async () => {
      setBusy(true);
      try {
        const response = await fetch(backgroundUrl);
        if (!response.ok) throw new Error("The bird's-eye demo plan could not be loaded.");
        const prepared = await prepareImage(await response.blob());
        ownedUrls.current.add(prepared.url);
        if (cancelled) return;
        setOriginal(prepared);
        setImage(prepared);
        setError("");
      } catch (err) {
        if (!cancelled) setError(err.message);
      } finally {
        if (!cancelled) setBusy(false);
      }
    };
    loadBackground();
    return () => { cancelled = true; };
  }, [backgroundUrl]);

  useEffect(() => () => {
    ownedUrls.current.forEach((url) => URL.revokeObjectURL(url));
    ownedUrls.current.clear();
  }, []);

  const scale = drawing.scale.length === 2 && knownDistance > 0
    ? distance(drawing.scale[0], drawing.scale[1]) / knownDistance
    : 0;
  const dimensions = useMemo(() => {
    const all = drawing.polygons.flat();
    if (!all.length || !scale) return null;
    const xs = all.map((point) => point.x);
    const ys = all.map((point) => point.y);
    return {
      width: (Math.max(...xs) - Math.min(...xs)) / scale,
      height: (Math.max(...ys) - Math.min(...ys)) / scale,
    };
  }, [drawing.polygons, scale]);

  const chooseImage = async (file) => {
    setBusy(true);
    setError("");
    try {
      const prepared = await prepareImage(file);
      ownedUrls.current.add(prepared.url);
      setOriginal(prepared);
      setImage(prepared);
      setDrawing(EMPTY_DRAWING);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };

  const clickCanvas = (event) => {
    const point = svgPoint(event, svgRef.current);
    if (!point) return;
    setDrawing((current) => {
      if (mode === "polygon") return { ...current, current: [...current.current, point] };
      if (mode === "scale") return { ...current, scale: current.scale.length >= 2 ? [point] : [...current.scale, point] };
      if (mode === "origin") return { ...current, origin: point };
      return { ...current, rectify: current.rectify.length >= 4 ? [point] : [...current.rectify, point] };
    });
  };

  const closePolygon = () => {
    if (drawing.current.length < 3) return setError("A floor polygon needs at least three points.");
    setDrawing((current) => ({
      ...current,
      polygons: [...current.polygons, current.current],
      current: [],
    }));
    setError("");
  };

  const undo = () => setDrawing((current) => {
    if (mode === "polygon") {
      if (current.current.length) return { ...current, current: current.current.slice(0, -1) };
      if (current.polygons.length) return { ...current, current: current.polygons.at(-1), polygons: current.polygons.slice(0, -1) };
    }
    if (mode === "scale") return { ...current, scale: current.scale.slice(0, -1) };
    if (mode === "origin") return { ...current, origin: null };
    return { ...current, rectify: current.rectify.slice(0, -1) };
  });

  const applyRectification = async () => {
    if (drawing.rectify.length !== 4) return;
    setBusy(true);
    setError("");
    try {
      const corrected = await rectifyImage(image, drawing.rectify);
      ownedUrls.current.add(corrected.url);
      setImage(corrected);
      setDrawing(EMPTY_DRAWING);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };

  const restoreOriginal = () => {
    if (!original || image.url === original.url) return;
    setImage(original);
    setDrawing(EMPTY_DRAWING);
  };

  const save = async () => {
    const origin = useScaleOrigin ? drawing.scale[0] : drawing.origin;
    if (!drawing.polygons.length) return setError("Close at least one walkable floor polygon.");
    if (drawing.scale.length !== 2 || knownDistance <= 0) return setError("Mark two scale points and enter their positive distance in metres.");
    if (!origin) return setError("Set an origin, or use the first scale point as the origin.");
    if (!window.confirm("Replace the floor map with this metric trace? Existing camera placements and floor calibrations will be cleared so they cannot project into the new coordinate frame.")) return;
    setBusy(true);
    setError("");
    try {
      const result = await api.post("/store/blueprint", {
        image_width: image.width,
        image_height: image.height,
        polygons_px: drawing.polygons,
        scale_points_px: drawing.scale,
        known_distance_m: Number(knownDistance),
        origin_px: origin,
        y_axis_up: yAxisUp,
      });
      await onSaved(result);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal
      wide
      title="Digitize a metric floor plan"
      onClose={onClose}
      footer={image ? <>
        <button className="button button-secondary" onClick={onClose}>Cancel</button>
        <button className="button button-dark" onClick={save} disabled={busy}>
          <CheckCircle2 size={14} /> {busy ? "Saving…" : "Use this floor plan"}
        </button>
      </> : null}
    >
      {!image ? (
        <label className="blueprint-upload">
          <ImagePlus size={30} />
          <strong>{busy ? (backgroundUrl ? "Loading bird's-eye demo plan…" : "Preparing image…") : "Choose a photographed or scanned plan"}</strong>
          <span>{backgroundUrl ? "The warehouse bird's-eye view will appear behind the tracing tools." : "JPG, PNG, or WebP. The image stays in this browser."}</span>
          <input type="file" accept="image/jpeg,image/png,image/webp" hidden disabled={busy} onChange={(event) => event.target.files?.[0] && chooseImage(event.target.files[0])} />
        </label>
      ) : (
        <div className="blueprint-digitizer stack">
          <div className="blueprint-toolbar" role="toolbar" aria-label="Blueprint tracing tools">
            <label className="blueprint-toolbar-upload"><ImagePlus size={14} /> Replace background<input type="file" accept="image/jpeg,image/png,image/webp" hidden disabled={busy} onChange={(event) => event.target.files?.[0] && chooseImage(event.target.files[0])} /></label>
            {MODES.map(([value, label, Icon]) => <button key={value} className={mode === value ? "active" : ""} onClick={() => setMode(value)}><Icon size={14} /> {label}</button>)}
            <button onClick={closePolygon} disabled={drawing.current.length < 3}><CheckCircle2 size={14} /> Close polygon</button>
            <button onClick={undo}><Undo2 size={14} /> Undo</button>
            <button onClick={() => setDrawing(EMPTY_DRAWING)}><RotateCcw size={14} /> Clear</button>
          </div>
          {backgroundUrl && image === original && <div className="blueprint-background-note"><CheckCircle2 size={14} /> NVIDIA warehouse bird's-eye plan loaded as the tracing background.</div>}
          <div className="blueprint-hint">{HINTS[mode]}</div>
          <svg ref={svgRef} className="blueprint-canvas" viewBox={`0 0 ${image.width} ${image.height}`} onClick={clickCanvas} role="img" aria-label="Plan tracing canvas">
            <image href={image.url} width={image.width} height={image.height} opacity=".72" preserveAspectRatio="none" />
            {drawing.polygons.map((polygon, index) => <polygon key={index} points={points(polygon)} className="blueprint-floor-shape" />)}
            {!!drawing.current.length && <polyline points={points(drawing.current)} className="blueprint-current-shape" />}
            {!!drawing.scale.length && <polyline points={points(drawing.scale)} className="blueprint-scale-line" />}
            {!!drawing.rectify.length && <polyline points={points(drawing.rectify)} className="blueprint-rectify-line" />}
            {[...drawing.polygons.flat(), ...drawing.current].map((point, index) => <circle key={`floor-${index}`} cx={point.x} cy={point.y} r={Math.max(4, image.width / 350)} className="blueprint-floor-point" />)}
            {drawing.scale.map((point, index) => <circle key={`scale-${index}`} cx={point.x} cy={point.y} r={Math.max(4, image.width / 350)} className="blueprint-scale-point" />)}
            {drawing.rectify.map((point, index) => <circle key={`rectify-${index}`} cx={point.x} cy={point.y} r={Math.max(4, image.width / 350)} className="blueprint-rectify-point" />)}
            {drawing.origin && <g className="blueprint-origin"><path d={`M ${drawing.origin.x - 14} ${drawing.origin.y} H ${drawing.origin.x + 14} M ${drawing.origin.x} ${drawing.origin.y - 14} V ${drawing.origin.y + 14}`} /></g>}
          </svg>
          <div className="blueprint-actions">
            <button className="button button-secondary" onClick={applyRectification} disabled={drawing.rectify.length !== 4 || busy}>Apply perspective correction</button>
            <button className="button button-ghost" onClick={restoreOriginal} disabled={image.url === original.url}>Restore original</button>
            <span>{drawing.polygons.length} closed polygon{drawing.polygons.length === 1 ? "" : "s"} · {drawing.scale.length}/2 scale points</span>
          </div>
          <div className="form-grid">
            <label className="field"><span>Known distance (metres)</span><input type="number" min="0" step="0.1" value={knownDistance} onChange={(event) => setKnownDistance(Number(event.target.value))} /></label>
            <label className="check-field"><input type="checkbox" checked={useScaleOrigin} onChange={(event) => setUseScaleOrigin(event.target.checked)} /> Use first scale point as origin</label>
            <label className="check-field"><input type="checkbox" checked={yAxisUp} onChange={(event) => setYAxisUp(event.target.checked)} /> Metric Y axis points upward</label>
          </div>
          {scale > 0 && <div className="blueprint-metric-summary"><strong>{scale.toFixed(2)} px/m</strong><span>{(1 / scale).toFixed(6)} m/px</span>{dimensions && <span>Floor bounds: {dimensions.width.toFixed(2)} × {dimensions.height.toFixed(2)} m</span>}</div>}
        </div>
      )}
      {error && <div className="form-error" role="alert">{error}</div>}
    </Modal>
  );
}

export function PlanDigitizer({ onRefresh, notify }) {
  const [open, setOpen] = useState(false);
  const demoBackground = demoSessionId() ? assetUrl("/demo/plan.png") : null;
  return <>
    <Panel
      title="Floor plan"
      subtitle="Trace a photographed plan and calibrate it directly in metres"
      action={<button className="button button-dark" onClick={() => setOpen(true)}><ImagePlus size={14} /> Digitize plan</button>}
    >
      <p className="form-note">{demoBackground ? "The demo keeps the real warehouse bird's-eye plan behind both the tracing canvas and the floor map workbench. You can replace it while digitizing." : "The source image is processed locally in your browser. StoreLens receives only the traced polygons, metric scale, and coordinate metadata."}</p>
    </Panel>
    {open && <PlanDigitizerModal backgroundUrl={demoBackground} onClose={() => setOpen(false)} onSaved={async (result) => {
      setOpen(false);
      await onRefresh();
      notify("Metric floor plan saved", `${result.polygon_count} polygon${result.polygon_count === 1 ? "" : "s"} · ${result.width_m.toFixed(2)} × ${result.height_m.toFixed(2)} m · ${result.invalidated_calibrations} calibration${result.invalidated_calibrations === 1 ? "" : "s"} cleared`);
    }} />}
  </>;
}
