import { useEffect, useMemo, useRef, useState } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import {
  Clock3,
  Pause,
  Play,
  Radio,
  RefreshCw,
  RotateCcw,
  Users,
} from "lucide-react";
import { api, assetUrl, formatPreciseDateTime } from "./api.js";
import {
  frameIsStale,
  highlightedZoneIds,
  latestFrameTracks,
  reconcileCompletedFrames,
} from "./live-state.js";
import { trackColor } from "./live-colors.js";
import { Badge } from "./components.jsx";
import { resultQuality } from "./status.js";
import { EmptyState, ErrorState, LoadingState, PageHeader, ResultValue, StatusPill } from "./ui.jsx";

const ACTIVE_SECONDS = .85;
const FADE_START_SECONDS = .2;
const MAX_INTERPOLATION_GAP_SECONDS = 3;
const TRAIL_SECONDS = 20;
const HISTORY_SECONDS = 10 * 60;

function scopedTrackKey(observation) {
  const entity = observation.entity_id || `observation-${observation.id}`;
  if (observation.identity_scope === "workspace") return entity;
  if (observation.identity_scope === "source") {
    return `${observation.source_id}:${entity}`;
  }
  return `${observation.worker_id || observation.job_id || observation.source_id}:${entity}`;
}

function groupTracks(observations) {
  const grouped = new Map();
  for (const observation of observations) {
    if (
      observation.entity_type !== "person" ||
      !observation.entity_id ||
      !observation.geometry?.point_map
    ) continue;
    const key = scopedTrackKey(observation);
    if (!grouped.has(key)) grouped.set(key, []);
    grouped.get(key).push(observation);
  }
  for (const rows of grouped.values()) rows.sort((left, right) => left.ts - right.ts);
  return grouped;
}

function interpolatePosition(rows, playhead) {
  let before = null;
  let after = null;
  for (const row of rows) {
    if (row.ts <= playhead) before = row;
    if (row.ts > playhead) {
      after = row;
      break;
    }
  }
  if (!before) return null;
  const from = before.geometry.point_map;
  if (!after || after.ts - before.ts > MAX_INTERPOLATION_GAP_SECONDS) {
    return { ...from, observation: before };
  }
  const to = after.geometry.point_map;
  const ratio = Math.max(0, Math.min(1, (playhead - before.ts) / (after.ts - before.ts)));
  return {
    x: from.x + (to.x - from.x) * ratio,
    y: from.y + (to.y - from.y) * ratio,
    observation: before,
  };
}

function trackAge(position, mode, playhead, liveNow) {
  if (!position) return Number.POSITIVE_INFINITY;
  if (mode === "live") {
    return liveNow - (position.observation.created_at || position.observation.ts);
  }
  return playhead - position.observation.ts;
}

function visibleTrackRows(tracks, playhead, mode, liveNow) {
  const rendered = [];
  for (const [key, rows] of tracks.entries()) {
    const position = interpolatePosition(rows, playhead);
    const age = trackAge(position, mode, playhead, liveNow);
    if (!position || age < 0 || age > ACTIVE_SECONDS) continue;
    const opacity = age <= FADE_START_SECONDS
      ? 1
      : Math.max(0, 1 - (age - FADE_START_SECONDS) / (ACTIVE_SECONDS - FADE_START_SECONDS));
    rendered.push({
      key,
      rows,
      position,
      age,
      opacity,
      color: trackColor(key),
      trail: rows.filter((row) => row.ts <= playhead && row.ts >= playhead - TRAIL_SECONDS),
    });
  }
  return rendered;
}


function zoneRings(zone) {
  const geometry = zone?.geometry;
  if (geometry?.type === "Polygon") {
    return [(geometry.coordinates?.[0] || []).map(([x, y]) => ({ x, y }))];
  }
  if (geometry?.type === "MultiPolygon") {
    return (geometry.coordinates || []).map((part) =>
      (part?.[0] || []).map(([x, y]) => ({ x, y })),
    );
  }
  return zone?.polygon?.length ? [zone.polygon] : [];
}

function disposeObject(object) {
  object.traverse((child) => {
    child.geometry?.dispose?.();
    const materials = Array.isArray(child.material) ? child.material : [child.material];
    for (const material of materials) {
      material?.map?.dispose?.();
      material?.dispose?.();
    }
  });
}

function LiveScene3D({ store, zones, sources, renderedTracks, resetToken, backgroundImageUrl = null }) {
  const mountRef = useRef(null);
  const sceneRef = useRef(null);
  const cameraRef = useRef(null);
  const controlsRef = useRef(null);
  const trackLayerRef = useRef(null);
  const zoneMaterialsRef = useRef(new Map());
  const width = Number(store?.width_m) || 20;
  const height = Number(store?.height_m) || 12;
  const floorPolygons = store?.map?.floor_polygons || [];
  const sceneKey = JSON.stringify({
    width,
    height,
    floorPolygons,
    walls: store?.map?.walls || [],
    zones: zones.map((zone) => ({ id: zone.id, geometry: zone.geometry, polygon: zone.polygon, color: zone.color })),
    sources: sources.map((source) => ({ id: source.id, placement: source.placement })),
    backgroundImageUrl,
  });
  // Keep the StoreLens map axes intact in the 3D world: map X -> world X and
  // map Y -> world Z. This lets an elevated view from +Z preserve the exact
  // left/right and top/bottom orientation of the 2D editor.
  const worldPoint = (point, y = 0) => new THREE.Vector3(point.x - width / 2, y, point.y - height / 2);

  useEffect(() => {
    const mount = mountRef.current;
    if (!mount) return undefined;
    const scene = new THREE.Scene();
    scene.background = new THREE.Color(0xe9e6dd);
    scene.fog = new THREE.Fog(0xe9e6dd, 24, 55);
    const camera = new THREE.PerspectiveCamera(34, 1, .05, 150);
    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    renderer.shadowMap.enabled = true;
    renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    mount.appendChild(renderer.domElement);

    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.dampingFactor = .08;
    controls.maxPolarAngle = Math.PI * .485;
    controls.minDistance = 4;
    controls.maxDistance = 60;

    const setDefaultView = () => {
      const span = Math.max(width, height, 6);
      // Keep the screen axes aligned with the 2D editor: map X stays horizontal
      // and increasing map Y points down-screen. The view has depth but no
      // lateral orbit, so the plan never appears mirrored by default.
      camera.position.set(0, span * 1.05, span * .32);
      controls.target.set(0, 0, 0);
      controls.update();
    };
    setDefaultView();
    controls.userData = { setDefaultView };

    scene.add(new THREE.HemisphereLight(0xffffff, 0xb8b3a7, 1.15));
    const sun = new THREE.DirectionalLight(0xffffff, 1.7);
    sun.position.set(-8, 16, 9);
    sun.castShadow = true;
    sun.shadow.mapSize.set(2048, 2048);
    scene.add(sun);

    const ground = new THREE.Mesh(
      new THREE.PlaneGeometry(Math.max(width * 1.35, 12), Math.max(height * 2.1, 12)),
      new THREE.MeshStandardMaterial({ color: 0xf4f2eb, roughness: .96 }),
    );
    ground.rotation.x = -Math.PI / 2;
    ground.position.y = -.035;
    ground.receiveShadow = true;
    scene.add(ground);
    const grid = new THREE.GridHelper(Math.max(width * 1.35, 12), Math.max(12, Math.ceil(width * 1.35)), 0xcac7bd, 0xdcd9d0);
    grid.position.y = -.02;
    scene.add(grid);

    for (const polygon of floorPolygons) {
      if (polygon.length < 3) continue;
      const shape = new THREE.Shape();
      polygon.forEach((point, index) => {
        const x = point.x - width / 2;
        const y = point.y - height / 2;
        if (index === 0) shape.moveTo(x, y); else shape.lineTo(x, y);
      });
      const geometry = new THREE.ExtrudeGeometry(shape, { depth: .08, bevelEnabled: false });
      geometry.rotateX(Math.PI / 2);
      const floor = new THREE.Mesh(
        geometry,
        new THREE.MeshStandardMaterial({ color: 0xcfe1cf, roughness: .82, metalness: 0 }),
      );
      floor.receiveShadow = true;
      scene.add(floor);
    }

    if (backgroundImageUrl) {
      const texture = new THREE.TextureLoader().load(backgroundImageUrl);
      texture.colorSpace = THREE.SRGBColorSpace;
      texture.anisotropy = renderer.capabilities.getMaxAnisotropy();
      const background = new THREE.Mesh(
        new THREE.PlaneGeometry(width, height),
        new THREE.MeshBasicMaterial({
          map: texture,
          transparent: true,
          opacity: .13,
          depthWrite: false,
          side: THREE.DoubleSide,
        }),
      );
      background.rotation.x = -Math.PI / 2;
      background.position.y = .012;
      background.renderOrder = 1;
      scene.add(background);
    }

    const zoneMaterials = new Map();
    for (const zone of zones) {
      const materials = [];
      for (const ring of zoneRings(zone)) {
        if (ring.length < 3) continue;
        const shape = new THREE.Shape();
        ring.forEach((point, index) => {
          const x = point.x - width / 2;
          const y = point.y - height / 2;
          if (index === 0) shape.moveTo(x, y); else shape.lineTo(x, y);
        });
        const geometry = new THREE.ShapeGeometry(shape);
        geometry.rotateX(Math.PI / 2);
        const material = new THREE.MeshBasicMaterial({ color: zone.color || "#7059ff", transparent: true, opacity: .18, side: THREE.DoubleSide });
        material.userData.baseColor = zone.color || "#7059ff";
        const mesh = new THREE.Mesh(geometry, material);
        mesh.position.y = .095;
        scene.add(mesh);
        materials.push(material);
      }
      if (materials.length) zoneMaterials.set(String(zone.id), materials);
    }
    zoneMaterialsRef.current = zoneMaterials;

    for (const wall of store?.map?.walls || []) {
      for (let index = 1; index < wall.length; index += 1) {
        const start = worldPoint(wall[index - 1]);
        const end = worldPoint(wall[index]);
        const dx = end.x - start.x;
        const dz = end.z - start.z;
        const length = Math.hypot(dx, dz);
        if (length < .001) continue;
        const mesh = new THREE.Mesh(
          new THREE.BoxGeometry(length, .48, .07),
          new THREE.MeshStandardMaterial({ color: 0xf8f7f1, roughness: .78 }),
        );
        mesh.position.set((start.x + end.x) / 2, .28, (start.z + end.z) / 2);
        mesh.rotation.y = Math.atan2(-dz, dx);
        mesh.castShadow = true;
        mesh.receiveShadow = true;
        scene.add(mesh);
      }
    }

    for (const source of sources.filter((item) => item.placement)) {
      const heading = THREE.MathUtils.degToRad(source.placement.rotation_deg || 0);
      const halfFov = THREE.MathUtils.degToRad((source.placement.fov_deg || 70) / 2);
      const fovLength = Math.min(Math.max(width, height) * .18, 3.5);
      const origin = worldPoint(source.placement, .105);
      const fovPoint = (angle) => new THREE.Vector3(
        origin.x + Math.cos(angle) * fovLength,
        origin.y,
        origin.z + Math.sin(angle) * fovLength,
      );
      const fovGeometry = new THREE.BufferGeometry();
      fovGeometry.setAttribute(
        "position",
        new THREE.Float32BufferAttribute([
          ...origin.toArray(),
          ...fovPoint(heading - halfFov).toArray(),
          ...fovPoint(heading + halfFov).toArray(),
        ], 3),
      );
      fovGeometry.setIndex([0, 1, 2]);
      fovGeometry.computeVertexNormals();
      scene.add(new THREE.Mesh(
        fovGeometry,
        new THREE.MeshBasicMaterial({
          color: 0x7059ff,
          transparent: true,
          opacity: .13,
          side: THREE.DoubleSide,
          depthWrite: false,
        }),
      ));

      const group = new THREE.Group();
      const color = new THREE.Color(0x7059ff);
      const body = new THREE.Mesh(new THREE.BoxGeometry(.26, .18, .34), new THREE.MeshStandardMaterial({ color, roughness: .55 }));
      body.castShadow = true;
      group.add(body);
      const lens = new THREE.Mesh(new THREE.CylinderGeometry(.07, .07, .08, 16), new THREE.MeshStandardMaterial({ color: 0x1d1c1a, metalness: .35 }));
      lens.rotation.x = Math.PI / 2;
      lens.position.z = -.2;
      group.add(lens);
      const point = worldPoint(source.placement, .55);
      group.position.copy(point);
      // StoreLens heading is (cos theta, sin theta) in map X/Y. Map Y is +Z
      // here and the model lens points down local -Z.
      group.rotation.y = -heading - Math.PI / 2;
      scene.add(group);
    }

    const trackLayer = new THREE.Group();
    scene.add(trackLayer);
    trackLayerRef.current = trackLayer;
    sceneRef.current = scene;
    cameraRef.current = camera;
    controlsRef.current = controls;

    const resize = () => {
      const rect = mount.getBoundingClientRect();
      const nextWidth = Math.max(1, rect.width);
      const nextHeight = Math.max(1, rect.height);
      renderer.setSize(nextWidth, nextHeight, false);
      camera.aspect = nextWidth / nextHeight;
      camera.updateProjectionMatrix();
    };
    const observer = new ResizeObserver(resize);
    observer.observe(mount);
    resize();
    let frame;
    const render = () => {
      controls.update();
      renderer.render(scene, camera);
      frame = window.requestAnimationFrame(render);
    };
    render();
    return () => {
      window.cancelAnimationFrame(frame);
      observer.disconnect();
      controls.dispose();
      disposeObject(scene);
      renderer.dispose();
      renderer.domElement.remove();
      sceneRef.current = null;
      cameraRef.current = null;
      controlsRef.current = null;
      trackLayerRef.current = null;
      zoneMaterialsRef.current = new Map();
    };
  }, [sceneKey]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    controlsRef.current?.userData?.setDefaultView?.();
  }, [resetToken]);

  useEffect(() => {
    const layer = trackLayerRef.current;
    if (!layer) return;
    while (layer.children.length) {
      const child = layer.children.pop();
      disposeObject(child);
    }
    for (const track of renderedTracks) {
      const color = new THREE.Color(track.color);
      if (track.trail.length > 1) {
        const points = track.trail.map((row) => worldPoint(row.geometry.point_map, .12));
        points.push(worldPoint(track.position, .12));
        const geometry = new THREE.BufferGeometry().setFromPoints(points);
        const material = new THREE.LineBasicMaterial({ color, transparent: true, opacity: .38 * track.opacity });
        layer.add(new THREE.Line(geometry, material));
      }
      const group = new THREE.Group();
      const bodyMaterial = new THREE.MeshStandardMaterial({
        color,
        emissive: color,
        emissiveIntensity: .28,
        transparent: true,
        opacity: track.opacity,
        roughness: .48,
      });
      const body = new THREE.Mesh(new THREE.CylinderGeometry(.1, .14, .34, 18), bodyMaterial);
      body.position.y = .24;
      body.castShadow = true;
      group.add(body);
      const head = new THREE.Mesh(new THREE.SphereGeometry(.115, 18, 12), bodyMaterial.clone());
      head.position.y = .51;
      head.castShadow = true;
      group.add(head);
      const ring = new THREE.Mesh(
        new THREE.RingGeometry(.18, .23, 28),
        new THREE.MeshBasicMaterial({ color, transparent: true, opacity: .52 * track.opacity, side: THREE.DoubleSide }),
      );
      ring.rotation.x = -Math.PI / 2;
      ring.position.y = .105;
      group.add(ring);
      group.position.copy(worldPoint(track.position));
      layer.add(group);
    }

    // A lighting cue, not a measurement. `highlightedZoneIds` documents why the
    // authoritative count still comes from the server.
    const highlighted = highlightedZoneIds(renderedTracks, zones, zoneRings);
    for (const [zoneId, materials] of zoneMaterialsRef.current.entries()) {
      const occupied = highlighted.has(zoneId);
      for (const material of materials) {
        material.color.set(occupied ? "#ef2929" : material.userData.baseColor);
        material.opacity = occupied ? .52 : .18;
        material.needsUpdate = true;
      }
    }
  }, [renderedTracks, zones, width, height]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div className="live-floor-map">
      <div
        ref={mountRef}
        className="live-three-canvas"
        role="img"
        aria-label={`Interactive 3D floor model with ${renderedTracks.length} person tracks in the latest processed frames`}
      />
      <div className="live-map-status">
        <span className="live-map-status-dot" />
        Latest frames: {renderedTracks.length} {renderedTracks.length === 1 ? "person" : "people"}
      </div>
    </div>
  );
}

function OperationalLivePage({ liveTick = 0 }) {
  const [state, setState] = useState({ loading: true, error: null, data: null });
  const [sourceId, setSourceId] = useState("all");
  const [identityMode, setIdentityMode] = useState("fused");
  const [replayOpen, setReplayOpen] = useState(false);
  const [playhead, setPlayhead] = useState(null);
  const [playing, setPlaying] = useState(true);
  const [speed, setSpeed] = useState(1);
  const [mode, setMode] = useState("live");
  const [liveNow, setLiveNow] = useState(() => Date.now() / 1000);
  const [resetToken, setResetToken] = useState(0);
  const modeRef = useRef("live");
  const animationRef = useRef(null);
  const lastFrameRef = useRef(null);

  const refresh = async ({ quiet = false } = {}) => {
    if (!quiet) setState((current) => ({ ...current, loading: !current.data, error: null }));
    const since = Date.now() / 1000 - HISTORY_SECONDS;
    const sourceQuery = sourceId === "all" ? "" : `&source_id=${sourceId}`;
    try {
      const [store, zones, sources, result, latest, fused] = await Promise.all([
        api.get("/store"),
        api.get("/zones"),
        api.get("/sources"),
        api.get(`/observations?kind=detection&since=${since}&limit=5000${sourceQuery}`),
        api.get(`/observations/latest-frames?entity_type=person${sourceQuery}`),
        api.get("/multiview/current?entity_type=person"),
      ]);
      const observations = result.observations
        .filter((row) => row.entity_type === "person" && row.geometry?.point_map)
        .sort((left, right) => left.ts - right.ts);
      const incomingFrames = latest.frames.map((frame) => ({
        ...frame,
        stale_after_s: latest.stale_after_s,
      }));
      setState((current) => ({
        loading: false,
        error: null,
        data: {
          store,
          zones,
          sources,
          observations,
          sourceFilter: sourceId,
          latestFrames: reconcileCompletedFrames(
            current.data?.sourceFilter === sourceId ? current.data.latestFrames : [],
            incomingFrames,
          ),
          fused,
        },
      }));
      if (incomingFrames.length && modeRef.current === "live") {
        setPlayhead(Math.max(...incomingFrames.map((frame) => frame.timestamp)));
      }
    } catch (error) {
      setState((current) => ({ ...current, loading: false, error }));
    }
  };

  useEffect(() => { refresh(); }, [sourceId]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (!liveTick) return;
    const timer = window.setTimeout(() => refresh({ quiet: true }), 250);
    return () => window.clearTimeout(timer);
  }, [liveTick]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (mode !== "live") return undefined;
    const timer = window.setInterval(() => setLiveNow(Date.now() / 1000), 1000);
    return () => window.clearInterval(timer);
  }, [mode]);

  const tracks = useMemo(
    () => groupTracks(state.data?.observations || []),
    [state.data?.observations],
  );
  const frameTimestamps = (state.data?.latestFrames || []).map((frame) => frame.timestamp);
  const minTs = state.data?.observations.at(0)?.ts ?? (frameTimestamps.length ? Math.min(...frameTimestamps) : null);
  const maxTs = Math.max(
    state.data?.observations.at(-1)?.ts ?? Number.NEGATIVE_INFINITY,
    frameTimestamps.length ? Math.max(...frameTimestamps) : Number.NEGATIVE_INFINITY,
  );
  const boundedMaxTs = Number.isFinite(maxTs) ? maxTs : null;

  useEffect(() => {
    if (!playing || mode !== "replay" || playhead == null || boundedMaxTs == null) return undefined;
    const animate = (now) => {
      if (lastFrameRef.current == null) lastFrameRef.current = now;
      const delta = Math.min((now - lastFrameRef.current) / 1000, .25) * speed;
      lastFrameRef.current = now;
      setPlayhead((current) => {
        const next = current + delta;
        if (next >= boundedMaxTs) {
          setPlaying(false);
          return boundedMaxTs;
        }
        return next;
      });
      animationRef.current = window.requestAnimationFrame(animate);
    };
    animationRef.current = window.requestAnimationFrame(animate);
    return () => {
      window.cancelAnimationFrame(animationRef.current);
      lastFrameRef.current = null;
    };
  }, [playing, mode, speed, boundedMaxTs, playhead == null]);

  const goLive = () => {
    modeRef.current = "live";
    setMode("live");
    setPlaying(true);
    setLiveNow(Date.now() / 1000);
    setPlayhead(boundedMaxTs);
  };
  const replay = () => {
    if (boundedMaxTs == null) return;
    modeRef.current = "replay";
    setMode("replay");
    setPlayhead(Math.max(minTs || boundedMaxTs, boundedMaxTs - 60));
    setPlaying(true);
  };

  if (state.loading && !state.data) return <LoadingState label="Loading live tracks…" />;
  if (state.error && !state.data) return <ErrorState error={state.error} retry={refresh} />;

  const data = state.data;
  const currentPlayhead = playhead ?? boundedMaxTs ?? Date.now() / 1000;
  const replayTracks = visibleTrackRows(tracks, currentPlayhead, mode, liveNow);
  const latestTracks = latestFrameTracks(data.latestFrames).map((track) => ({
    ...track,
    color: trackColor(track.colorKey),
    age: Math.max(0, liveNow - (track.frame.source_last_ingestion_at || track.frame.timestamp)),
  }));
  const fusedTracks = (data.fused?.entities || []).map((entity) => {
    const observation = {
      entity_id: entity.fused_entity_id, entity_type: entity.entity_type,
      zone_id: entity.zone_id, zone_name: data.zones.find((zone) => zone.id === entity.zone_id)?.name,
      source_id: entity.members?.[0]?.source_id, geometry: { point_map: entity.point_map },
    };
    return {
      key: entity.fused_entity_id, colorKey: entity.fused_entity_id,
      color: trackColor(entity.fused_entity_id), age: entity.freshness_s,
      opacity: entity.quality === "known" ? 1 : .45, trail: [], rows: [observation],
      position: { ...entity.point_map, observation }, frame: { stale: entity.quality !== "known" },
    };
  });
  const active = mode === "live" ? (identityMode === "fused" ? fusedTracks : latestTracks) : replayTracks;
  const staleFrames = data.latestFrames.filter((frame) => frameIsStale(frame, liveNow));
  /* The confidence StoreLens itself reports. Combined mode takes the group's
   * own quality; per-camera mode has no cross-camera claim to make, so it is
   * only as good as the freshness of the cameras being shown. */
  const groupQuality = data.fused?.groups?.length
    ? (data.fused.groups.every((group) => group.quality === "known") ? "known"
      : data.fused.groups.some((group) => group.quality !== "unknown") ? "partial" : "unknown")
    : "unknown";
  const liveQuality = mode === "replay" ? "known"
    : identityMode === "fused" ? groupQuality
      : data.latestFrames.length === 0 ? "unknown"
        : staleFrames.length ? "partial" : "known";
  const recentEvents = data.observations
    .filter((row) => row.ts <= currentPlayhead)
    .slice(-12)
    .reverse();

  return (
    <>
      <PageHeader
        title="Live"
        actions={
          <>
            <label className="select-control">
              <span className="sr-only">Identity view</span>
              <select value={identityMode} onChange={(event) => setIdentityMode(event.target.value)} disabled={mode === "replay"}>
                <option value="fused">Combined</option>
                <option value="source">Per camera</option>
              </select>
            </label>
            <label className="select-control">
              <span className="sr-only">Camera source</span>
              <select value={sourceId} onChange={(event) => setSourceId(event.target.value)}>
                <option value="all">All cameras</option>
                {data.sources.map((source) => <option key={source.id} value={source.id}>{source.name}</option>)}
              </select>
            </label>
            <button className="icon-button" onClick={() => refresh()} aria-label="Refresh live tracks">
              <RefreshCw size={16} />
            </button>
          </>
        }
      />
      {state.error && <div className="inline-warning">Couldn't refresh: {state.error.message}</div>}
      {mode === "live" && identityMode === "source" && staleFrames.length > 0 && (
        <div className="inline-warning">
          {staleFrames.length} {staleFrames.length === 1 ? "camera has" : "cameras have"} stopped sending. The last thing they saw is still shown.
        </div>
      )}
      {mode === "live" && identityMode === "fused" && data.fused?.groups?.some((group) => group.quality !== "known") && (
        <div className="inline-warning">
          Not every camera is reporting, so the count below may be low.
        </div>
      )}
      <div className="live-toolbar" role="toolbar" aria-label="Playback">
        {mode === "live" ? (
          <>
            <button onClick={() => { replay(); setReplayOpen(true); }}
                    disabled={!data.observations.length}>
              <RotateCcw size={14} aria-hidden="true" /> Replay the last minute
            </button>
            <button onClick={() => setResetToken((value) => value + 1)}>
              <RotateCcw size={14} aria-hidden="true" /> Reset view
            </button>
          </>
        ) : (
          <>
            <button className="active" onClick={() => { goLive(); setReplayOpen(false); }}>
              <Radio size={14} aria-hidden="true" /> Back to live
            </button>
            <button onClick={() => setPlaying((value) => !value)}>
              {playing ? <Pause size={14} aria-hidden="true" /> : <Play size={14} aria-hidden="true" />}
              {playing ? "Pause" : "Play"}
            </button>
            <label>
              Speed
              <select value={speed} onChange={(event) => setSpeed(Number(event.target.value))}>
                <option value="1">1x</option><option value="2">2x</option><option value="4">4x</option>
              </select>
            </label>
            <button onClick={() => setResetToken((value) => value + 1)}>
              <RotateCcw size={14} aria-hidden="true" /> Reset view
            </button>
            <time>{formatPreciseDateTime(currentPlayhead)}</time>
          </>
        )}
      </div>

      {!data.observations.length && !data.latestFrames.length ? (
        <EmptyState tone="no-data" title="No live people data yet">
          Person tracking isn't producing data for this workspace yet. Check Sources or Setup.
        </EmptyState>
      ) : (
        <div className="live-layout">
          <LiveScene3D
            store={data.store}
            zones={data.zones}
            sources={data.sources}
            renderedTracks={active}
            resetToken={resetToken}
          />
          <aside className="live-rail">
            <div className="live-summary-grid">
              <div className="live-headline">
                <span>People now</span>
                {/* The scene is drawn from whatever evidence exists; the number
                    carries the platform's own confidence in it. */}
                <ResultValue value={active.length} quality={liveQuality} />
              </div>
            </div>
            <section className="live-rail-section">
              <div className="live-rail-heading"><Users size={15} aria-hidden="true" /><div><strong>Current tracks</strong></div></div>
              <div className="live-track-list">
                {active.slice(0, 12).map((track) => {
                  const latest = track.position.observation;
                  const key = track.key;
                  return (
                    <div key={key}>
                      <i style={{ background: track.color }} />
                      <span><strong>{latest.entity_id}</strong><small>{latest.entity_type} · {latest.zone_name || "unassigned floor"}</small></span>
                      <time>{track.age.toFixed(1)}s</time>
                    </div>
                  );
                })}
                {!active.length && <p className="live-rail-empty">No people in the latest frame.</p>}
              </div>
            </section>
            <section className="live-rail-section">
              <div className="live-rail-heading"><Clock3 size={15} aria-hidden="true" /><div><strong>Recent activity</strong></div></div>
              <div className="live-event-list">
                {recentEvents.map((row) => {
                  const key = scopedTrackKey(row);
                  return <div key={row.id}><i style={{ background: trackColor(key) }} /><time>{formatPreciseDateTime(row.ts).split(", ").at(-1)}</time><span>{row.entity_id}</span><Badge tone="neutral">person</Badge></div>;
                })}
              </div>
            </section>

          </aside>
        </div>
      )}
    </>
  );
}

function DemoLivePage({ demoReplay }) {
  const [context, setContext] = useState({ loading: true, error: null, store: null, zones: [], sources: [] });
  const [resetToken, setResetToken] = useState(0);
  const debug = new URLSearchParams(window.location.search).get("debug") === "1";
  useEffect(() => {
    let cancelled = false;
    Promise.all([api.get("/store"), api.get("/zones"), api.get("/sources")])
      .then(([store, zones, sources]) => { if (!cancelled) setContext({ loading: false, error: null, store, zones, sources }); })
      .catch((error) => { if (!cancelled) setContext((value) => ({ ...value, loading: false, error })); });
    return () => { cancelled = true; };
  }, [demoReplay.session?.id]);
  if (context.loading) return <LoadingState label="Loading synchronized demo map…" />;
  if (context.error) return <ErrorState error={context.error} />;
  const replay = demoReplay.replay;
  const tracks = (replay?.entities || []).map((entity) => {
    const observation = {
      entity_id: entity.runtime_id, entity_type: entity.entity_type,
      zone_id: entity.zone_id,
      zone_name: context.zones.find((zone) => Number(zone.id) === Number(entity.zone_id))?.name,
      geometry: { point_map: entity.point_map },
    };
    return {
      key: entity.runtime_id, colorKey: entity.runtime_id, color: trackColor(entity.runtime_id),
      age: Math.max(0, replay.videoTime - Number(replay.derivedSample?.video_time_s || 0)),
      opacity: entity.quality === "known" ? 1 : .45, trail: [], rows: [observation],
      position: { ...entity.point_map, observation }, frame: { stale: entity.quality !== "known" },
    };
  });
  const kpi = replay?.kpi;
  return <>
    <PageHeader title="Live" description="The four demo cameras, combined into one floor map." />
    <div className="live-toolbar" role="toolbar" aria-label="Replay view">
      <button onClick={() => setResetToken((value) => value + 1)}>
        <RotateCcw size={14} aria-hidden="true" /> Reset view
      </button>
      {/* Clock internals are for developers checking synchronization, not for
          the person watching the demo. */}
      {debug && (
        <time>
          Media {replay.videoTime.toFixed(3)}s · frame {replay.frameIndex}
          {" · derived "}{replay.derivedSample?.video_time_s?.toFixed(3) ?? "—"}s
          {" · epoch "}{replay.epoch}
        </time>
      )}
    </div>
    <div className="live-layout" data-demo-tour="live-floor-map"><LiveScene3D store={context.store} zones={context.zones} sources={context.sources} renderedTracks={tracks} resetToken={resetToken} backgroundImageUrl={assetUrl("/demo/plan.png")} /><aside className="live-rail">
      <div className="live-summary-grid">
        <div className="live-headline">
          <span>People in Aisle 04</span>
          <ResultValue value={kpi?.value} quality={kpi?.quality} />
        </div>
      </div>
      <section className="live-rail-section">
        <div className="live-rail-heading"><Users size={15} aria-hidden="true" /><div><strong>Current tracks</strong></div></div>
        <div className="live-track-list">
          {tracks.slice(0, 16).map((track, index) => (
            <div key={track.key}>
              <i style={{ background: track.color }} />
              <span>
                <strong>Person {index + 1}</strong>
                <small>{track.position.observation.zone_name || "On the floor"}</small>
              </span>
              {debug && <time>{track.key}</time>}
            </div>
          ))}
        </div>
      </section>
    </aside></div>
  </>;
}

export function LivePage(props) {
  if (props.demoReplay?.session && props.demoReplay?.cache) return <DemoLivePage demoReplay={props.demoReplay} />;
  return <OperationalLivePage liveTick={props.liveTick} />;
}
