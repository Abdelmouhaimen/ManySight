import * as THREE from "three";
import { OrbitControls } from "./vendor/OrbitControls.js";

const slides = [...document.querySelectorAll(".slide")];
const panels = [...document.querySelectorAll(".step-panel")];
const stepTabs = [...document.querySelectorAll(".step-tab")];
let currentSlide = 0;

function showSlide(index) {
  currentSlide = Math.max(0, Math.min(slides.length - 1, index));
  slides.forEach((slide, i) => slide.classList.toggle("active", i === currentSlide));
  document.getElementById("slideCounter").textContent = `${String(currentSlide + 1).padStart(2, "0")} / ${String(slides.length).padStart(2, "0")}`;
  document.getElementById("slideProgress").style.width = `${((currentSlide + 1) / slides.length) * 100}%`;
  history.replaceState(null, "", `#${currentSlide + 1}`);
  if (currentSlide === 1) requestAnimationFrame(resizeStore);
  if (currentSlide === 2 && document.querySelector('[data-analysis-panel="insights3d"]')?.classList.contains("active")) requestAnimationFrame(resizeAnalysis3d);
  if (currentSlide === 3) requestAnimationFrame(resizeLive);
  if (currentSlide === 6) requestAnimationFrame(resizeAccessibility);
}

function showStep(name) {
  stepTabs.forEach((tab) => tab.classList.toggle("active", tab.dataset.step === name));
  panels.forEach((panel) => panel.classList.toggle("active", panel.dataset.panel === name));
  if (name === "model") requestAnimationFrame(resizeStore);
}

document.getElementById("prevSlide").addEventListener("click", () => showSlide(currentSlide - 1));
document.getElementById("nextSlide").addEventListener("click", () => showSlide(currentSlide + 1));
document.getElementById("fullscreen").addEventListener("click", () => document.fullscreenElement ? document.exitFullscreen() : document.documentElement.requestFullscreen());
window.addEventListener("keydown", (event) => {
  if (["ArrowRight", "PageDown", " "].includes(event.key)) showSlide(currentSlide + 1);
  if (["ArrowLeft", "PageUp"].includes(event.key)) showSlide(currentSlide - 1);
});
stepTabs.forEach((tab) => tab.addEventListener("click", () => showStep(tab.dataset.step)));
document.querySelectorAll("[data-go]").forEach((button) => button.addEventListener("click", () => showStep(button.dataset.go)));
document.querySelectorAll("[data-slide-target]").forEach((button) => button.addEventListener("click", () => showSlide(Number(button.dataset.slideTarget) - 1)));
document.querySelectorAll("[data-analysis-view]").forEach((button) => button.addEventListener("click", () => {
  document.querySelectorAll("[data-analysis-view]").forEach((item) => item.classList.toggle("active", item === button));
  document.querySelectorAll("[data-analysis-panel]").forEach((panel) => panel.classList.toggle("active", panel.dataset.analysisPanel === button.dataset.analysisView));
  if (button.dataset.analysisView === "insights3d") requestAnimationFrame(resizeAnalysis3d);
}));

document.querySelectorAll(".camera-card").forEach((card) => {
  card.addEventListener("click", () => {
    document.querySelectorAll(".camera-card").forEach((item) => item.classList.toggle("active", item === card));
    document.getElementById("selectedCamera").textContent = card.dataset.camera;
    document.getElementById("selectedArea").textContent = card.dataset.area;
    document.getElementById("selectedCoverage").textContent = card.dataset.coverage;
    document.getElementById("selectedPose").textContent = card.dataset.pose;
    document.getElementById("selectedQuality").textContent = card.dataset.quality;
    document.querySelector(".calibration-preview i").textContent = `${card.dataset.area} view`;
  });
});

// Annotation and training-data experience --------------------------------
function showAnnotationView(view) {
  document.querySelectorAll("[data-annotation-view]").forEach((button) => button.classList.toggle("active", button.dataset.annotationView === view));
  document.querySelectorAll("[data-annotation-panel]").forEach((panel) => panel.classList.toggle("active", panel.dataset.annotationPanel === view));
}
document.querySelectorAll("[data-annotation-view]").forEach((button) => button.addEventListener("click", () => showAnnotationView(button.dataset.annotationView)));
document.querySelectorAll("[data-open-dataset]").forEach((button) => button.addEventListener("click", () => showAnnotationView("dataset")));

const annotationScene = document.getElementById("annotationScene");
const annotationCamera = document.getElementById("annotationCamera");
document.querySelectorAll("[data-annotation-scene]").forEach((button) => button.addEventListener("click", () => {
  document.querySelectorAll("[data-annotation-scene]").forEach((item) => item.classList.toggle("active", item === button));
  const wetFloor = button.dataset.annotationScene === "wetfloor";
  annotationScene.src = wetFloor ? "./assets/wet-floor.png" : "./assets/fridge-open.png";
  annotationScene.alt = wetFloor ? "Wet floor camera frame" : "Open refrigerator camera frame";
  annotationCamera.textContent = wetFloor ? "CAM-05 · Main aisle" : "CAM-02 · Fresh cabinets";
  const recommended = wetFloor ? "wet_floor" : "fridge_open";
  document.querySelectorAll("[data-annotation-label]").forEach((item) => item.classList.toggle("active", item.dataset.annotationLabel === recommended));
  document.getElementById("saveFrame").classList.remove("saved");
  document.getElementById("saveFrame").textContent = "Save frame for training";
}));
document.querySelectorAll("[data-annotation-label]").forEach((button) => button.addEventListener("click", () => {
  document.querySelectorAll("[data-annotation-label]").forEach((item) => item.classList.toggle("active", item === button));
}));
let annotationDatasetCount = 24;
document.getElementById("saveFrame").addEventListener("click", (event) => {
  annotationDatasetCount += 1;
  document.getElementById("datasetBadge").textContent = annotationDatasetCount;
  document.getElementById("datasetCount").textContent = annotationDatasetCount;
  event.currentTarget.textContent = "Saved to training data ✓";
  event.currentTarget.classList.add("saved");
});
document.getElementById("annotationTime").textContent = new Date().toLocaleTimeString("en-GB", { hour12: false }) + ".420";

let activeDatasetLabel = "all";
function filterDataset() {
  const camera = document.getElementById("cameraFilter").value;
  document.querySelectorAll("#datasetGrid article").forEach((item) => {
    const labelMatch = activeDatasetLabel === "all" || item.dataset.label === activeDatasetLabel;
    const cameraMatch = camera === "all" || item.dataset.camera === camera;
    item.classList.toggle("hidden", !(labelMatch && cameraMatch));
  });
}
document.querySelectorAll("[data-dataset-label]").forEach((button) => button.addEventListener("click", () => {
  activeDatasetLabel = button.dataset.datasetLabel;
  document.querySelectorAll("[data-dataset-label]").forEach((item) => item.classList.toggle("active", item === button));
  filterDataset();
}));
document.getElementById("cameraFilter").addEventListener("change", filterDataset);

// Agent-native product surface -------------------------------------------
const agentPrompts = {
  insight: {
    question: "How many customers visited between 2pm and 4pm?",
    title: "642 customers", badge: "Verified",
    text: "I queried distinct tracked visits in the selected interval across all entrance cameras.",
    source: "Source: tracked visits · 14:00–16:00 · 6 cameras",
    result: `<div><span>14:00</span><i style="height:35%"></i></div><div><span>14:30</span><i style="height:58%"></i></div><div><span>15:00</span><i style="height:82%"></i></div><div><span>15:30</span><i style="height:67%"></i></div><div><span>16:00</span><i style="height:44%"></i></div>`,
    workflow: ["Understand request", "Query analytics", "Return evidence"]
  },
  model: {
    question: "Run our custom queue model on both checkout cameras and publish the result.",
    title: "Queue model launched", badge: "Running",
    text: "I found the deployed model, registered a job for CAM-05 and CAM-06, and created a live queue insight.",
    source: "Job: checkout-queue-v4 · 2 sources · heartbeat healthy",
    result: `<p><b>✓</b><strong>Load custom model</strong><span>queue-v4</span></p><p><b>✓</b><strong>Start camera workers</strong><span>2 online</span></p><p><b>✓</b><strong>Publish insight</strong><span>Live</span></p>`,
    workflow: ["Discover model", "Launch workers", "Publish insight"]
  },
  finetune: {
    question: "Export every wet_floor frame, fine-tune a detector, and prepare it for review.",
    title: "Fine-tuning prepared", badge: "Review",
    text: "I collected the labeled frames with camera provenance, created a train/validation split, and prepared the training run.",
    source: "Dataset: wet_floor · 184 frames · 4 cameras · version 3",
    result: `<p><b>✓</b><strong>Extract labeled data</strong><span>184 frames</span></p><p><b>✓</b><strong>Validate dataset</strong><span>92% reviewed</span></p><p><b>→</b><strong>Start fine-tuning</strong><span>Awaiting approval</span></p>`,
    workflow: ["Extract dataset", "Fine-tune model", "Evaluate & deploy"]
  }
};
document.querySelectorAll("[data-agent-prompt]").forEach((button) => button.addEventListener("click", () => {
  const prompt = agentPrompts[button.dataset.agentPrompt];
  document.querySelectorAll("[data-agent-prompt]").forEach((item) => item.classList.toggle("active", item === button));
  document.querySelectorAll("[data-agent-job]").forEach((item) => item.classList.toggle("active", item.dataset.agentJob === button.dataset.agentPrompt));
  document.getElementById("agentQuestion").textContent = prompt.question;
  document.getElementById("agentAnswerTitle").textContent = prompt.title;
  document.getElementById("agentAnswerBadge").textContent = prompt.badge;
  document.getElementById("agentAnswerText").textContent = prompt.text;
  const result = document.getElementById("agentResult");
  result.className = `agent-result ${button.dataset.agentPrompt === "insight" ? "insight-result" : "workflow-result"}`;
  result.innerHTML = prompt.result;
  document.getElementById("agentSource").textContent = prompt.source;
  document.getElementById("agentWorkflow").innerHTML = prompt.workflow.map((step, index) => `${index ? "<i></i>" : ""}<span class="done">${step}</span>`).join("");
}));

document.querySelectorAll("[data-domain]").forEach((card) => card.addEventListener("click", () => {
  document.querySelectorAll("[data-domain]").forEach((item) => item.classList.toggle("active", item === card));
}));

// Static semantic store ------------------------------------------------------
const viewport = document.getElementById("storeViewport");
const scene = new THREE.Scene();
scene.background = new THREE.Color(0xe5dfd4);
scene.fog = new THREE.Fog(0xe5dfd4, 28, 52);

const camera = new THREE.PerspectiveCamera(36, 1, 0.1, 100);
camera.position.set(19, 17, 22);
const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false });
renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
renderer.shadowMap.enabled = true;
renderer.shadowMap.type = THREE.PCFSoftShadowMap;
renderer.outputColorSpace = THREE.SRGBColorSpace;
viewport.appendChild(renderer.domElement);

const controls = new OrbitControls(camera, renderer.domElement);
controls.target.set(0, 0.7, 0);
controls.enableDamping = true;
controls.dampingFactor = 0.06;
controls.minDistance = 11;
controls.maxDistance = 40;
controls.maxPolarAngle = Math.PI * 0.48;

scene.add(new THREE.HemisphereLight(0xfffdf7, 0x9a9488, 2.5));
const sun = new THREE.DirectionalLight(0xffffff, 3.2);
sun.position.set(9, 18, 10);
sun.castShadow = true;
sun.shadow.mapSize.set(2048, 2048);
sun.shadow.camera.left = -20; sun.shadow.camera.right = 20; sun.shadow.camera.top = 20; sun.shadow.camera.bottom = -20;
scene.add(sun);
const fill = new THREE.DirectionalLight(0xcce0d7, 1.1); fill.position.set(-12, 8, -8); scene.add(fill);

const groups = { zones: new THREE.Group(), cameras: new THREE.Group(), labels: new THREE.Group(), fixtures: new THREE.Group() };
Object.values(groups).forEach((group) => scene.add(group));
const selectable = [];
const zoneTargets = {
  entry: new THREE.Vector3(-9, 0, 7), fresh: new THREE.Vector3(-9, 0, -6), "zone-a": new THREE.Vector3(-4, 0, 0),
  "zone-b": new THREE.Vector3(4, 0, 0), "checkout-1": new THREE.Vector3(2.5, 0, 6.5), "checkout-2": new THREE.Vector3(6.5, 0, 6.5), exit: new THREE.Vector3(10.5, 0, 7.3)
};

function mat(color, roughness = .8, opacity = 1) {
  return new THREE.MeshStandardMaterial({ color, roughness, metalness: .03, transparent: opacity < 1, opacity, depthWrite: opacity > .45 });
}
function box(name, size, position, color, type, parent = groups.fixtures, rotationY = 0) {
  const mesh = new THREE.Mesh(new THREE.BoxGeometry(...size), mat(color));
  mesh.position.set(...position);
  mesh.rotation.y = rotationY;
  mesh.castShadow = true;
  mesh.receiveShadow = true;
  mesh.userData = { name, type };
  parent.add(mesh);
  selectable.push(mesh);
  return mesh;
}
function addEdges(mesh, color = 0x7d8178, opacity = .38) {
  const edges = new THREE.LineSegments(new THREE.EdgesGeometry(mesh.geometry), new THREE.LineBasicMaterial({ color, transparent: true, opacity }));
  edges.position.copy(mesh.position); edges.rotation.copy(mesh.rotation); groups.fixtures.add(edges);
}
function textSprite(text, position, color = "#353933", scale = 1) {
  const canvas = document.createElement("canvas");
  canvas.width = 512; canvas.height = 128;
  const ctx = canvas.getContext("2d");
  ctx.font = "700 44px Inter, Arial";
  ctx.textAlign = "center"; ctx.textBaseline = "middle";
  ctx.fillStyle = "rgba(250,248,243,.92)";
  ctx.roundRect(8, 15, 496, 98, 24); ctx.fill();
  ctx.strokeStyle = "rgba(76,77,70,.22)"; ctx.lineWidth = 3; ctx.stroke();
  ctx.fillStyle = color; ctx.fillText(text, 256, 64);
  const texture = new THREE.CanvasTexture(canvas); texture.colorSpace = THREE.SRGBColorSpace;
  const sprite = new THREE.Sprite(new THREE.SpriteMaterial({ map: texture, transparent: true, depthTest: false }));
  sprite.position.set(...position); sprite.scale.set(4.2 * scale, 1.05 * scale, 1);
  groups.labels.add(sprite); return sprite;
}
function addZone(key, label, x, z, w, d, color) {
  const zone = new THREE.Mesh(new THREE.PlaneGeometry(w, d), new THREE.MeshBasicMaterial({ color, transparent: true, opacity: .24, side: THREE.DoubleSide, depthWrite: false }));
  zone.rotation.x = -Math.PI / 2; zone.position.set(x, .035, z); zone.userData = { name: label, type: `Operational zone · ${key}` };
  groups.zones.add(zone); selectable.push(zone);
  const border = new THREE.LineSegments(new THREE.EdgesGeometry(new THREE.PlaneGeometry(w, d)), new THREE.LineBasicMaterial({ color, transparent: true, opacity: .7 }));
  border.rotation.x = -Math.PI / 2; border.position.set(x, .045, z); groups.zones.add(border);
  textSprite(label, [x, .22, z], "#31362f", .72);
}

const floor = new THREE.Mesh(new THREE.PlaneGeometry(28, 20), mat(0xf4f0e7, .95));
floor.rotation.x = -Math.PI / 2; floor.receiveShadow = true; scene.add(floor);
const grid = new THREE.GridHelper(28, 28, 0xc9c3b8, 0xd8d2c7); grid.position.y = .012; scene.add(grid);

box("North wall", [28, 4.5, .18], [0, 2.25, -10], 0xf8f5ed, "Architecture");
box("West wall", [.18, 4.5, 20], [-14, 2.25, 0], 0xf8f5ed, "Architecture");
box("East wall", [.18, 1.3, 20], [14, .65, 0], 0xeee9de, "Architecture");

addZone("entry", "ENTRY", -9.5, 7.3, 6, 4.3, 0x96beb4);
addZone("fresh", "FRESH ZONE", -9.3, -5.8, 7.3, 7.5, 0x83aab9);
addZone("zone-a", "ZONE A", -3.5, -.4, 6.5, 11.5, 0xe6bd71);
addZone("zone-b", "ZONE B", 4.0, -.4, 7.0, 11.5, 0xc9a5b4);
addZone("checkout-1", "CHECKOUT 1", 2.6, 6.7, 3.3, 4.1, 0xe58363);
addZone("checkout-2", "CHECKOUT 2", 6.5, 6.7, 3.3, 4.1, 0xe58363);
addZone("exit", "EXIT", 10.8, 7.3, 3.8, 4.3, 0x9caf7d);

// Fresh wall: chilled display cases.
for (let i = 0; i < 5; i++) {
  const fridge = box(`Fridge ${String(i + 1).padStart(2, "0")}`, [2.2, 2.7, 1.15], [-12.2 + i * 2.45, 1.35, -9.15], i % 2 ? 0xb8cbd0 : 0xa9c3c8, "Refrigerated display");
  addEdges(fridge, 0x58727a, .45);
  const glass = box(`Fridge ${i + 1} door`, [1.82, 1.85, .05], [-12.2 + i * 2.45, 1.42, -8.55], 0xd9ecef, "Glass door");
  glass.material.transparent = true; glass.material.opacity = .55;
}

// Six merchandising rows with numbered aisle labels.
const shelfRows = [
  [-5.6, -2.8, "ROW 01"], [-2.8, -2.8, "ROW 02"], [.4, -2.8, "ROW 03"],
  [3.2, -2.8, "ROW 04"], [6.0, -2.8, "ROW 05"], [8.8, -2.8, "ROW 06"]
];
shelfRows.forEach(([x, z, label], index) => {
  const shelf = box(label, [1.25, 2.15, 7.0], [x, 1.075, z], index < 2 ? 0xb5c2b5 : 0xc8bdad, `Merchandise shelf · ${label}`);
  addEdges(shelf, index < 2 ? 0x6c8376 : 0x80776b, .55);
  for (let level = 0; level < 3; level++) box(`${label} shelf ${level + 1}`, [1.34, .07, 7.08], [x, .45 + level * .7, z], 0xf1ece1, "Shelf plane");
  textSprite(label, [x, 2.55, z + 2.2], index < 2 ? "#516d5d" : "#5d574f", .52);
});

// Produce islands and static fixtures in the fresh zone.
for (let i = 0; i < 3; i++) {
  const produce = box(`Fresh island ${i + 1}`, [2.15, .85, 1.55], [-10.7 + i * 2.4, .43, -4.0], i === 1 ? 0xd9b46e : 0xa9bd8c, "Fresh produce display");
  addEdges(produce, 0x6f765d, .45);
}

// Checkouts, gates, baskets and signs.
box("Checkout 1", [3.0, 1.0, 1.15], [2.6, .5, 6.5], 0xe17455, "Checkout counter");
box("Checkout 2", [3.0, 1.0, 1.15], [6.5, .5, 6.5], 0xd76246, "Checkout counter");
box("Entry gate", [4.4, .12, .16], [-9.5, 1.1, 9.4], 0x29322d, "Entry gate");
box("Exit gate", [3.6, .12, .16], [10.8, 1.1, 9.4], 0x29322d, "Exit gate");
for (let i = 0; i < 3; i++) box(`Basket stack ${i + 1}`, [.8, .55, .8], [-12.2 + i, .28, 5.4], 0xe2a06e, "Basket stack");

// Posters on the north wall.
const posterColors = [0xe87d5c, 0x95b7aa, 0xe3bc72, 0x819fa9];
[-10, -4.5, 1, 6.5].forEach((x, i) => {
  const poster = box(`Poster ${i + 1}`, [3.2, 2.05, .06], [x, 2.55, -9.86], posterColors[i], "Promotional poster");
  addEdges(poster, 0xffffff, .55);
});

function addCamera(name, position, target, color = 0x4f665b) {
  const group = new THREE.Group();
  const body = new THREE.Mesh(new THREE.BoxGeometry(.55, .34, .7), mat(0xf4f1e9, .45)); body.castShadow = true; group.add(body);
  const lens = new THREE.Mesh(new THREE.CylinderGeometry(.13, .18, .18, 16), mat(0x2c3933, .35)); lens.rotation.x = Math.PI / 2; lens.position.z = -.42; group.add(lens);
  group.position.copy(position);
  const direction = target.clone().sub(position).normalize();
  group.quaternion.setFromUnitVectors(new THREE.Vector3(0, 0, -1), direction);
  const length = position.distanceTo(target) * .92;
  const radiusX = Math.min(2.35, length * .27);
  const radiusY = Math.min(1.45, length * .17);
  const origin = new THREE.Vector3(0, 0, -.45);
  const z = -length - .45;
  const corners = [
    new THREE.Vector3(-radiusX, -radiusY, z), new THREE.Vector3(radiusX, -radiusY, z),
    new THREE.Vector3(radiusX, radiusY, z), new THREE.Vector3(-radiusX, radiusY, z)
  ];
  const frustumSegments = [];
  corners.forEach((corner) => frustumSegments.push(origin, corner));
  for (let i = 0; i < 4; i++) frustumSegments.push(corners[i], corners[(i + 1) % 4]);
  const frustum = new THREE.LineSegments(
    new THREE.BufferGeometry().setFromPoints(frustumSegments),
    new THREE.LineBasicMaterial({ color, transparent: true, opacity: .55 })
  );
  group.add(frustum);
  group.add(new THREE.Line(
    new THREE.BufferGeometry().setFromPoints([origin, new THREE.Vector3(0, 0, z)]),
    new THREE.LineBasicMaterial({ color, transparent: true, opacity: .28 })
  ));
  body.userData = { name, type: "Fixed camera · calibrated" }; selectable.push(body);
  groups.cameras.add(group);
  const label = textSprite(name, [position.x, position.y + .6, position.z], "#41564a", .46); label.userData.cameraLabel = true;
}
addCamera("CAM-01", new THREE.Vector3(-13.1, 3.6, 8.6), new THREE.Vector3(-7, 0, 4));
addCamera("CAM-02", new THREE.Vector3(-12.7, 3.7, -8.8), new THREE.Vector3(-7, 0, -3));
addCamera("CAM-03", new THREE.Vector3(-1.0, 3.9, -9.4), new THREE.Vector3(-3, 0, 0));
addCamera("CAM-04", new THREE.Vector3(12.7, 3.9, -8.8), new THREE.Vector3(4, 0, 0));
addCamera("CAM-05", new THREE.Vector3(1.2, 3.6, 9.0), new THREE.Vector3(3, 0, 4));
addCamera("CAM-06", new THREE.Vector3(12.8, 3.6, 8.8), new THREE.Vector3(7, 0, 4));

document.querySelectorAll("[data-layer]").forEach((button) => button.addEventListener("click", () => {
  const group = groups[button.dataset.layer];
  group.visible = !group.visible; button.classList.toggle("on", group.visible);
}));
document.getElementById("resetStore").addEventListener("click", () => {
  camera.position.set(19, 17, 22); controls.target.set(0, .7, 0); controls.update();
});
document.querySelectorAll("[data-focus]").forEach((button) => button.addEventListener("click", () => {
  const target = zoneTargets[button.dataset.focus];
  controls.target.copy(target); camera.position.set(target.x + 10, 10, target.z + 12); controls.update();
  document.getElementById("objectInspector").innerHTML = `<small>SELECTED ZONE</small><strong>${button.querySelector("span").textContent}</strong><span>Static fixtures and camera coverage</span>`;
}));

const raycaster = new THREE.Raycaster();
const pointer = new THREE.Vector2();
renderer.domElement.addEventListener("pointerdown", (event) => {
  const bounds = renderer.domElement.getBoundingClientRect();
  pointer.x = ((event.clientX - bounds.left) / bounds.width) * 2 - 1;
  pointer.y = -((event.clientY - bounds.top) / bounds.height) * 2 + 1;
  raycaster.setFromCamera(pointer, camera);
  const hit = raycaster.intersectObjects(selectable, true).find((item) => item.object.userData?.name);
  if (hit) document.getElementById("objectInspector").innerHTML = `<small>SELECTED OBJECT</small><strong>${hit.object.userData.name}</strong><span>${hit.object.userData.type}</span>`;
});

// Historical analysis in the digital twin ---------------------------------
const analysisViewport = document.getElementById("analysis3dViewport");
const analysisScene = new THREE.Scene();
analysisScene.background = new THREE.Color(0xe4ded3);
analysisScene.fog = new THREE.Fog(0xe4ded3, 27, 48);
const analysisCamera = new THREE.PerspectiveCamera(35, 1, .1, 100);
analysisCamera.position.set(18, 15, 21);
const analysisRenderer = new THREE.WebGLRenderer({ antialias: true });
analysisRenderer.setPixelRatio(Math.min(devicePixelRatio, 2));
analysisRenderer.shadowMap.enabled = true;
analysisRenderer.outputColorSpace = THREE.SRGBColorSpace;
analysisViewport.appendChild(analysisRenderer.domElement);
const analysisControls = new OrbitControls(analysisCamera, analysisRenderer.domElement);
analysisControls.target.set(0, .7, 0);
analysisControls.enableDamping = true;
analysisControls.maxPolarAngle = Math.PI * .49;
analysisControls.minDistance = 10;
analysisControls.maxDistance = 38;
analysisScene.add(new THREE.HemisphereLight(0xfffdf8, 0x918c82, 2.7));
const analysisSun = new THREE.DirectionalLight(0xffffff, 3);
analysisSun.position.set(8, 18, 10); analysisSun.castShadow = true; analysisScene.add(analysisSun);

function sceneBox(parent, size, position, color, opacity = 1) {
  const mesh = new THREE.Mesh(new THREE.BoxGeometry(...size), mat(color, .8, opacity));
  mesh.position.set(...position); mesh.castShadow = true; mesh.receiveShadow = true; parent.add(mesh); return mesh;
}
function sceneLabel(parent, text, position, scale = 1, foreground = "#3e443e", background = "rgba(250,248,243,.92)") {
  const canvas = document.createElement("canvas"); canvas.width = 512; canvas.height = 128;
  const ctx = canvas.getContext("2d"); ctx.font = "700 40px Inter, Arial"; ctx.textAlign = "center"; ctx.textBaseline = "middle";
  ctx.fillStyle = background; ctx.roundRect(8, 15, 496, 98, 24); ctx.fill();
  ctx.fillStyle = foreground; ctx.fillText(text, 256, 64);
  const texture = new THREE.CanvasTexture(canvas); texture.colorSpace = THREE.SRGBColorSpace;
  const sprite = new THREE.Sprite(new THREE.SpriteMaterial({ map: texture, transparent: true, depthTest: false }));
  sprite.position.set(...position); sprite.scale.set(4.0 * scale, 1.0 * scale, 1); parent.add(sprite); return sprite;
}

const analysisArchitecture = new THREE.Group();
const shelfCells = [];
const heatmapGroup = new THREE.Group();
const flowGroup = new THREE.Group();
analysisScene.add(analysisArchitecture, heatmapGroup, flowGroup);
const analysisFloor = new THREE.Mesh(new THREE.PlaneGeometry(28, 20), mat(0xf6f2e9, .98));
analysisFloor.rotation.x = -Math.PI / 2; analysisFloor.receiveShadow = true; analysisArchitecture.add(analysisFloor);
const analysisGrid = new THREE.GridHelper(28, 28, 0xcfc8bc, 0xded7cc); analysisGrid.position.y = .01; analysisArchitecture.add(analysisGrid);
sceneBox(analysisArchitecture, [28, 4.2, .16], [0, 2.1, -10], 0xf8f5ed);
sceneBox(analysisArchitecture, [.16, 4.2, 20], [-14, 2.1, 0], 0xf8f5ed);
sceneBox(analysisArchitecture, [.16, 1.1, 20], [14, .55, 0], 0xeee9de);
for (let i = 0; i < 5; i++) sceneBox(analysisArchitecture, [2.25, 2.65, 1.1], [-11.8 + i * 2.45, 1.33, -9.15], i % 2 ? 0xb8cbd0 : 0xaac2c6);
sceneBox(analysisArchitecture, [3, 1, 1.15], [2.8, .5, 6.6], 0xde7458);
sceneBox(analysisArchitecture, [3, 1, 1.15], [6.7, .5, 6.6], 0xd96347);
sceneLabel(analysisArchitecture, "ENTRY", [-9.5, .3, 8.4], .58);
sceneLabel(analysisArchitecture, "EXIT", [10.7, .3, 8.4], .58);

for (let row = 0; row < 6; row++) {
  const x = -7.5 + row * 3;
  sceneBox(analysisArchitecture, [1.4, .18, 8.2], [x, .09, -.8], 0x6d716a);
  for (let column = 0; column < 4; column++) {
    const z = -4.0 + column * 2.15;
    for (let level = 0; level < 3; level++) {
      const impressions = 95 + ((row * 73 + column * 91 + level * 41) % 415);
      const attention = +(2.4 + ((row * 31 + column * 17 + level * 29) % 68) / 10).toFixed(1);
      const buys = 4 + ((row * 19 + column * 13 + level * 11) % 54);
      const cell = sceneBox(analysisArchitecture, [1.34, .57, 1.84], [x, .47 + level * .68, z], 0x90b5ae);
      cell.userData = { row: row + 1, column: column + 1, level: level + 1, impressions, attention, buys, shelfCell: true };
      shelfCells.push(cell);
    }
  }
  sceneLabel(analysisArchitecture, `ROW ${String(row + 1).padStart(2, "0")}`, [x, 2.72, -2.5], .45);
}

function seededRandom(seed = 91427) {
  let value = seed >>> 0;
  return () => { value += 0x6D2B79F5; let t = value; t = Math.imul(t ^ t >>> 15, t | 1); t ^= t + Math.imul(t ^ t >>> 7, t | 61); return ((t ^ t >>> 14) >>> 0) / 4294967296; };
}
function interpolateHeatColor(t) {
  const stops = [
    [0, [68, 1, 84]], [.24, [59, 82, 139]], [.5, [33, 145, 140]], [.74, [94, 201, 98]], [1, [253, 231, 37]]
  ];
  for (let i = 1; i < stops.length; i++) {
    if (t <= stops[i][0]) {
      const [aT, a] = stops[i - 1], [bT, b] = stops[i]; const p = (t - aT) / (bT - aT);
      return a.map((channel, index) => Math.round(channel + (b[index] - channel) * p));
    }
  }
  return stops.at(-1)[1];
}
function createStoreHeatmap() {
  const canvas = document.createElement("canvas"); canvas.width = 560; canvas.height = 400;
  const ctx = canvas.getContext("2d", { willReadFrequently: true });
  const random = seededRandom();
  const toCanvas = ([x, z]) => [(x + 14) / 28 * canvas.width, (z + 10) / 20 * canvas.height];
  const routes = [
    { repeats: 105, width: 9, points: [[-13,8.3],[-9.5,7.2],[-6,5],[-2,4],[3,4],[7,4.8],[10,6.4],[13,8.3]] },
    { repeats: 82, width: 10, points: [[-13,8.3],[-10,5],[-10,0],[-10,-6],[-6,-6],[-3,-3],[2,5.5],[9,7],[13,8.3]] },
    { repeats: 48, width: 7, points: [[-12,8],[-7.1,5],[-6,1],[-6,-5],[-2,-6],[2.7,5.7],[9,7],[13,8.3]] },
    { repeats: 54, width: 7, points: [[-12,8],[-6,5],[-3,2],[-3,-5],[0,-6],[2.7,5.7],[9,7],[13,8.3]] },
    { repeats: 58, width: 7, points: [[-12,8],[-5,5],[0,2],[0,-5],[3,-6],[6.7,5.7],[9,7],[13,8.3]] },
    { repeats: 62, width: 7, points: [[-12,8],[-3,5],[3,2],[3,-5],[6,-6],[6.7,5.7],[9,7],[13,8.3]] },
    { repeats: 68, width: 8, points: [[-12,8],[0,5],[6,2],[6,-5],[9,-5],[10,2],[9,7],[13,8.3]] },
    { repeats: 76, width: 9, points: [[-12,8],[2,5],[9,3],[10,-4],[12,-6],[12,2],[10,7],[13,8.3]] },
    { repeats: 38, width: 7, points: [[-10,-6],[-6,-7],[-1,-7],[4,-7],[9,-6],[12,-4]] },
    { repeats: 46, width: 8, points: [[-9,6],[-5,6],[0,6],[2.8,6.3],[6.7,6.3],[10,7]] }
  ];
  ctx.globalCompositeOperation = "lighter";
  routes.forEach((route) => {
    for (let pass = 0; pass < route.repeats; pass++) {
      const jittered = route.points.map(([x, z], index) => {
        const endpoint = index === 0 || index === route.points.length - 1;
        const spread = endpoint ? .12 : .52;
        return new THREE.Vector3(x + (random() - .5) * spread, 0, z + (random() - .5) * spread);
      });
      const curve = new THREE.CatmullRomCurve3(jittered);
      const samples = curve.getPoints(54);
      ctx.beginPath();
      samples.forEach((point, index) => {
        const [x, y] = toCanvas([point.x, point.z]);
        if (index === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
      });
      ctx.strokeStyle = "#ffffff";
      ctx.globalAlpha = .0055 + random() * .0045;
      ctx.lineWidth = route.width + random() * 3;
      ctx.lineCap = "round"; ctx.lineJoin = "round";
      ctx.shadowColor = "rgba(255,255,255,.55)"; ctx.shadowBlur = 7;
      ctx.stroke();
    }
  });
  ctx.globalCompositeOperation = "source-over"; ctx.globalAlpha = 1; ctx.shadowBlur = 0;
  const pixels = ctx.getImageData(0, 0, canvas.width, canvas.height);
  for (let i = 0; i < pixels.data.length; i += 4) {
    const density = pixels.data[i + 3] / 255;
    if (density < .006) { pixels.data[i + 3] = 0; continue; }
    const t = Math.min(1, Math.pow(density * 1.08, 1.32));
    const [r, g, b] = interpolateHeatColor(t);
    pixels.data[i] = r; pixels.data[i + 1] = g; pixels.data[i + 2] = b;
    pixels.data[i + 3] = Math.round(54 + t * 184);
  }
  ctx.putImageData(pixels, 0, 0);
  const texture = new THREE.CanvasTexture(canvas); texture.colorSpace = THREE.SRGBColorSpace; texture.minFilter = THREE.LinearFilter; texture.magFilter = THREE.LinearFilter;
  return texture;
}
const storeHeatmap = new THREE.Mesh(
  new THREE.PlaneGeometry(27.75, 19.75),
  new THREE.MeshBasicMaterial({ map: createStoreHeatmap(), transparent: true, opacity: .82, depthWrite: false, side: THREE.DoubleSide, polygonOffset: true, polygonOffsetFactor: -2 })
);
storeHeatmap.rotation.x = -Math.PI / 2; storeHeatmap.position.y = .042; heatmapGroup.add(storeHeatmap);

function addFlow(points, radius, color, opacity = .72) {
  const curve = new THREE.CatmullRomCurve3(points.map(([x, z]) => new THREE.Vector3(x, .13, z)));
  const tube = new THREE.Mesh(new THREE.TubeGeometry(curve, 48, radius, 8, false), new THREE.MeshBasicMaterial({ color, transparent: true, opacity, depthWrite: false }));
  flowGroup.add(tube); return tube;
}
addFlow([[-12, 8.2], [-9.3, 7.1], [-7.2, 4.4]], .27, 0xe66d4d, .86);
addFlow([[-7.2, 4.4], [-9.1, 1.0], [-9.4, -5.5]], .13, 0x5d9cad);
addFlow([[-7.2, 4.4], [-4.5, 2.0], [-3.7, -.7]], .17, 0xd79d4a);
addFlow([[-7.2, 4.4], [-1.5, 4.2], [3.8, .0]], .12, 0x9b7e98);
addFlow([[-9.4, -5.5], [-4.6, -3.5], [2.7, 5.6]], .085, 0x5d9cad, .62);
addFlow([[-3.7, -.7], [-.5, 2.2], [2.7, 5.6]], .12, 0xd79d4a, .68);
addFlow([[3.8, 0], [5.2, 2.8], [6.7, 5.7]], .11, 0x9b7e98, .68);
addFlow([[2.7, 5.6], [6.7, 6.0], [9.0, 6.9]], .15, 0xd9694d, .76);
addFlow([[6.7, 5.7], [8.0, 6.3], [9.0, 6.9]], .14, 0xd9694d, .76);
addFlow([[9.0, 6.9], [10.8, 7.5], [13.0, 8.3]], .23, 0x6f9f82, .84);

let currentShelfMetric = "impressions";
let hoveredShelfCell = null;
const metricRanges = { impressions: [95, 510], attention: [2.4, 9.2], buys: [4, 58] };
function metricColor(value, metric) {
  const [min, max] = metricRanges[metric]; const t = Math.max(0, Math.min(1, (value - min) / (max - min)));
  const low = new THREE.Color(0x6e9eb0), mid = new THREE.Color(0xedbd59), high = new THREE.Color(0xe65f45);
  return t < .55 ? low.lerp(mid, t / .55) : mid.lerp(high, (t - .55) / .45);
}
function setShelfMetric(metric) {
  currentShelfMetric = metric;
  shelfCells.forEach((cell) => cell.material.color.copy(metricColor(cell.userData[metric], metric)));
  document.querySelectorAll("[data-shelf-metric]").forEach((button) => button.classList.toggle("active", button.dataset.shelfMetric === metric));
  if (hoveredShelfCell) renderShelfTooltip(hoveredShelfCell.userData);
}
document.querySelectorAll("[data-shelf-metric]").forEach((button) => button.addEventListener("click", () => setShelfMetric(button.dataset.shelfMetric)));
document.querySelectorAll("[data-analysis-layer]").forEach((button) => button.addEventListener("click", () => {
  const group = button.dataset.analysisLayer === "heatmap" ? heatmapGroup : flowGroup;
  group.visible = !group.visible; button.classList.toggle("active", group.visible);
}));
document.getElementById("resetAnalysis3d").addEventListener("click", () => {
  analysisCamera.position.set(18, 15, 21); analysisControls.target.set(0, .7, 0); analysisControls.update();
});
const analysisRaycaster = new THREE.Raycaster();
const analysisPointer = new THREE.Vector2();
const shelfTooltip = document.getElementById("shelfMetricTooltip");
const shelfMetricLabels = { impressions: "Impressions", attention: "Average attention", buys: "Purchases" };
function metricDisplay(data, metric) {
  if (metric === "attention") return `${data.attention}s`;
  return String(data[metric]);
}
function renderShelfTooltip(data) {
  shelfTooltip.innerHTML = `
    <small>ROW ${String(data.row).padStart(2, "0")} · COLUMN ${data.column} · LEVEL ${data.level}</small>
    <strong>Shelf performance</strong>
    <div class="hover-primary"><span>${shelfMetricLabels[currentShelfMetric]}</span><strong>${metricDisplay(data, currentShelfMetric)}</strong></div>
    <div class="hover-values">
      <span>Impressions<b>${data.impressions}</b></span>
      <span>Avg. attention<b>${data.attention}s</b></span>
      <span>Purchases<b>${data.buys}</b></span>
    </div>`;
}
function clearShelfHover() {
  if (hoveredShelfCell) {
    hoveredShelfCell.material.emissive.setHex(0x000000);
    hoveredShelfCell.material.emissiveIntensity = 0;
    hoveredShelfCell.scale.set(1, 1, 1);
  }
  hoveredShelfCell = null;
  shelfTooltip.classList.remove("visible");
}
analysisRenderer.domElement.addEventListener("pointermove", (event) => {
  const bounds = analysisRenderer.domElement.getBoundingClientRect();
  analysisPointer.x = ((event.clientX - bounds.left) / bounds.width) * 2 - 1;
  analysisPointer.y = -((event.clientY - bounds.top) / bounds.height) * 2 + 1;
  analysisRaycaster.setFromCamera(analysisPointer, analysisCamera);
  const hit = analysisRaycaster.intersectObjects(shelfCells, false)[0];
  if (!hit) { clearShelfHover(); return; }
  if (hoveredShelfCell !== hit.object) {
    clearShelfHover();
    hoveredShelfCell = hit.object;
    hoveredShelfCell.material.emissive.setHex(0xffffff);
    hoveredShelfCell.material.emissiveIntensity = .24;
    hoveredShelfCell.scale.set(1.045, 1.045, 1.045);
  }
  const data = hit.object.userData;
  renderShelfTooltip(data);
  shelfTooltip.style.left = `${Math.max(8, Math.min(bounds.width - 225, event.clientX - bounds.left))}px`;
  shelfTooltip.style.top = `${Math.max(8, Math.min(bounds.height - 145, event.clientY - bounds.top))}px`;
  shelfTooltip.classList.add("visible");
  document.getElementById("selectedShelfMetric").innerHTML = `<small>SELECTED SHELF CELL</small><strong>Row ${String(data.row).padStart(2, "0")} · C${data.column} · Level ${data.level}</strong><span>${currentShelfMetric === "attention" ? `${data.attention}s average attention` : `${data[currentShelfMetric]} ${currentShelfMetric}`}</span>`;
});
analysisRenderer.domElement.addEventListener("pointerleave", clearShelfHover);
setShelfMetric("impressions");

// Live operational twin ----------------------------------------------------
const liveViewport = document.getElementById("liveViewport");
const liveScene = new THREE.Scene();
liveScene.background = new THREE.Color(0xded8cd);
liveScene.fog = new THREE.Fog(0xded8cd, 28, 50);
const liveCamera = new THREE.PerspectiveCamera(35, 1, .1, 100); liveCamera.position.set(18, 15, 21);
const liveRenderer = new THREE.WebGLRenderer({ antialias: true });
liveRenderer.setPixelRatio(Math.min(devicePixelRatio, 2)); liveRenderer.shadowMap.enabled = true; liveRenderer.outputColorSpace = THREE.SRGBColorSpace; liveViewport.appendChild(liveRenderer.domElement);
const liveControls = new OrbitControls(liveCamera, liveRenderer.domElement); liveControls.target.set(0, .7, 0); liveControls.enableDamping = true; liveControls.maxPolarAngle = Math.PI * .49; liveControls.minDistance = 10; liveControls.maxDistance = 38;
liveScene.add(new THREE.HemisphereLight(0xfffdf8, 0x918c82, 2.7));
const liveSun = new THREE.DirectionalLight(0xffffff, 3); liveSun.position.set(8, 18, 10); liveSun.castShadow = true; liveScene.add(liveSun);
const liveArchitecture = new THREE.Group();
const liveGroups = { tracks: new THREE.Group(), cameras: new THREE.Group(), states: new THREE.Group() };
liveScene.add(liveArchitecture, liveGroups.tracks, liveGroups.cameras, liveGroups.states);
const liveFloor = new THREE.Mesh(new THREE.PlaneGeometry(28, 20), mat(0xf6f2e9, .98)); liveFloor.rotation.x = -Math.PI / 2; liveFloor.receiveShadow = true; liveArchitecture.add(liveFloor);
const liveGrid = new THREE.GridHelper(28, 28, 0xcfc8bc, 0xded7cc); liveGrid.position.y = .01; liveArchitecture.add(liveGrid);
sceneBox(liveArchitecture, [28, 4.2, .16], [0, 2.1, -10], 0xf8f5ed);
sceneBox(liveArchitecture, [.16, 4.2, 20], [-14, 2.1, 0], 0xf8f5ed);
sceneBox(liveArchitecture, [.16, 1.1, 20], [14, .55, 0], 0xeee9de);
for (let i = 0; i < 5; i++) sceneBox(liveArchitecture, [2.25, 2.65, 1.1], [-11.8 + i * 2.45, 1.33, -9.15], i % 2 ? 0xb8cbd0 : 0xaac2c6);
for (let i = 0; i < 6; i++) sceneBox(liveArchitecture, [1.35, 2.1, 7.5], [-7.5 + i * 3, 1.05, -.8], i < 2 ? 0xb3c2b5 : 0xc7bdad);
sceneBox(liveArchitecture, [3, 1, 1.15], [2.8, .5, 6.6], 0xde7458); sceneBox(liveArchitecture, [3, 1, 1.15], [6.7, .5, 6.6], 0xd96347);

function addLiveCamera(position, target) {
  const group = new THREE.Group();
  const body = new THREE.Mesh(new THREE.BoxGeometry(.48, .3, .62), mat(0xf4f1e9)); group.add(body);
  const lens = new THREE.Mesh(new THREE.CylinderGeometry(.1, .14, .16, 14), mat(0x29352f)); lens.rotation.x = Math.PI / 2; lens.position.z = -.37; group.add(lens);
  const status = new THREE.Mesh(new THREE.SphereGeometry(.075, 12, 8), new THREE.MeshBasicMaterial({ color: 0x67b27c })); status.position.set(.19, .17, -.18); group.add(status);
  group.position.copy(position); group.quaternion.setFromUnitVectors(new THREE.Vector3(0, 0, -1), target.clone().sub(position).normalize()); liveGroups.cameras.add(group);
}
addLiveCamera(new THREE.Vector3(-13.1, 3.6, 8.6), new THREE.Vector3(-7, 0, 4));
addLiveCamera(new THREE.Vector3(-12.7, 3.7, -8.8), new THREE.Vector3(-7, 0, -3));
addLiveCamera(new THREE.Vector3(-1, 3.9, -9.4), new THREE.Vector3(-3, 0, 0));
addLiveCamera(new THREE.Vector3(12.7, 3.9, -8.8), new THREE.Vector3(4, 0, 0));
addLiveCamera(new THREE.Vector3(1.2, 3.6, 9), new THREE.Vector3(3, 0, 4));
addLiveCamera(new THREE.Vector3(12.8, 3.6, 8.8), new THREE.Vector3(7, 0, 4));
sceneLabel(liveGroups.states, "GATE OPEN", [-9.5, 1.55, 8.7], .48, "#587b64");
sceneLabel(liveGroups.states, "3.2°C", [-9.7, 3.3, -8.5], .45, "#527b87");
sceneLabel(liveGroups.states, "Q1 · 3", [2.8, 1.65, 6.6], .45, "#b55b43");
sceneLabel(liveGroups.states, "Q2 · 1", [6.7, 1.65, 6.6], .45, "#6b7f6e");

const livePeople = [];
const livePaths = [
  [[-12,8.2],[-9,6.6],[-7,3],[-9,-4],[-4,-5],[1,1],[2.8,5.8],[9,7],[13,8.2]],
  [[-12,8.2],[-8,6],[-4,3],[-3,-2],[0,-4],[4,0],[6.7,5.8],[9,7],[13,8.2]],
  [[-12,8.2],[-9,5],[-5,1],[-1,3],[3,0],[2.8,5.8],[9,7],[13,8.2]],
  [[-12,8.2],[-8,6],[-2,5],[4,2],[7,-3],[6.7,5.8],[9,7],[13,8.2]],
  [[-12,8.2],[-10,3],[-8,-5],[-3,-4],[1,-1],[6,2],[6.7,5.8],[9,7],[13,8.2]],
  [[-12,8.2],[-7,5],[-4,0],[0,2],[5,-2],[2.8,5.8],[9,7],[13,8.2]]
];
const personColors = [0xe46f52,0x5e9daf,0xd5a34e,0x8b7192,0x6e9d78,0xc77c84];
function addLivePerson(pathPoints, index) {
  const curve = new THREE.CatmullRomCurve3(pathPoints.map(([x,z]) => new THREE.Vector3(x, 0, z)));
  const trail = new THREE.Line(new THREE.BufferGeometry().setFromPoints(curve.getPoints(70).map((p) => new THREE.Vector3(p.x, .06, p.z))), new THREE.LineBasicMaterial({ color: personColors[index], transparent: true, opacity: .46 })); liveGroups.tracks.add(trail);
  const avatar = new THREE.Group();
  const body = new THREE.Mesh(new THREE.CylinderGeometry(.23, .28, .72, 14), mat(personColors[index], .65)); body.position.y = .55; body.castShadow = true; avatar.add(body);
  const head = new THREE.Mesh(new THREE.SphereGeometry(.22, 16, 10), mat(0xe2b691, .7)); head.position.y = 1.08; head.castShadow = true; avatar.add(head);
  const ring = new THREE.Mesh(new THREE.RingGeometry(.34,.42,24), new THREE.MeshBasicMaterial({ color: personColors[index], side: THREE.DoubleSide, transparent: true, opacity: .8 })); ring.rotation.x = -Math.PI/2; ring.position.y = .025; avatar.add(ring);
  liveGroups.tracks.add(avatar); livePeople.push({ avatar, curve, phase: index / livePaths.length, speed: .018 + index * .0018 });
}
livePaths.forEach(addLivePerson);
let livePaused = false;
document.querySelectorAll("[data-live-layer]").forEach((button) => button.addEventListener("click", () => {
  const group = liveGroups[button.dataset.liveLayer]; group.visible = !group.visible; button.classList.toggle("active", group.visible);
}));
document.getElementById("pauseLive").addEventListener("click", (event) => { livePaused = !livePaused; event.currentTarget.textContent = livePaused ? "Resume" : "Pause"; event.currentTarget.classList.toggle("active", livePaused); });
document.getElementById("resetLive").addEventListener("click", () => { liveCamera.position.set(18,15,21); liveControls.target.set(0,.7,0); liveControls.update(); });

// Agentic accessibility recommendation ----------------------------------
const accessViewport = document.getElementById("accessibilityViewport");
const accessScene = new THREE.Scene();
accessScene.background = new THREE.Color(0xe3ddd2);
accessScene.fog = new THREE.Fog(0xe3ddd2, 22, 42);
const accessCamera = new THREE.PerspectiveCamera(36, 1, .1, 100);
accessCamera.position.set(15, 13.5, 16);
const accessRenderer = new THREE.WebGLRenderer({ antialias: true });
accessRenderer.setPixelRatio(Math.min(devicePixelRatio, 2));
accessRenderer.shadowMap.enabled = true;
accessRenderer.outputColorSpace = THREE.SRGBColorSpace;
accessViewport.appendChild(accessRenderer.domElement);
const accessControls = new OrbitControls(accessCamera, accessRenderer.domElement);
accessControls.target.set(0, .45, 0);
accessControls.enableDamping = true;
accessControls.maxPolarAngle = Math.PI * .48;
accessControls.minDistance = 9;
accessControls.maxDistance = 32;
accessScene.add(new THREE.HemisphereLight(0xfffdf8, 0x8f8a80, 2.8));
const accessSun = new THREE.DirectionalLight(0xffffff, 3.1);
accessSun.position.set(7, 15, 9); accessSun.castShadow = true; accessScene.add(accessSun);

const accessArchitecture = new THREE.Group();
const accessAuditGroup = new THREE.Group();
const accessProposalGroup = new THREE.Group();
accessScene.add(accessArchitecture, accessAuditGroup, accessProposalGroup);
const accessFloor = new THREE.Mesh(new THREE.PlaneGeometry(18, 12), mat(0xf5f1e8, .98));
accessFloor.rotation.x = -Math.PI / 2; accessFloor.receiveShadow = true; accessArchitecture.add(accessFloor);
const accessGrid = new THREE.GridHelper(18, 18, 0xcac4b9, 0xdcd6cc); accessGrid.position.y = .012; accessArchitecture.add(accessGrid);
sceneBox(accessArchitecture, [18, 3.4, .14], [0, 1.7, -6], 0xf8f5ed);
sceneBox(accessArchitecture, [.14, 3.4, 12], [-9, 1.7, 0], 0xf8f5ed);
sceneBox(accessArchitecture, [.14, 1, 12], [9, .5, 0], 0xeee9df);
sceneBox(accessArchitecture, [4.1, 2.8, .12], [6.7, 1.4, -3.0], 0xe9e4da);
sceneBox(accessArchitecture, [.12, 2.8, 3.1], [4.7, 1.4, -4.45], 0xe9e4da);
sceneLabel(accessArchitecture, "ENTRANCE", [-7.15, .26, 5.25], .46);
sceneLabel(accessArchitecture, "RESTROOM", [6.85, .26, -4.8], .46);

function addAccessTable(parent, x, z, chairOffset = 1.05) {
  const table = new THREE.Mesh(new THREE.CylinderGeometry(.7, .7, .12, 28), mat(0xd4b58c));
  table.position.set(x, .72, z); table.castShadow = true; parent.add(table);
  sceneBox(parent, [.18, .65, .18], [x, .34, z], 0x594f45);
  [[chairOffset,0],[-chairOffset,0],[0,chairOffset],[0,-chairOffset]].forEach(([dx,dz]) => sceneBox(parent, [.46,.55,.46], [x + dx,.28,z + dz], 0x9eb5b7));
}
function addAccessRoute(parent, points, color, width = .16, opacity = .9) {
  const curve = new THREE.CatmullRomCurve3(points.map(([x,z]) => new THREE.Vector3(x, .09, z)));
  const route = new THREE.Mesh(new THREE.TubeGeometry(curve, 64, width, 10, false), new THREE.MeshBasicMaterial({ color, transparent: true, opacity, depthWrite: false }));
  parent.add(route); return route;
}
function addTurningCircle(parent, x, z, pass, label) {
  const color = pass ? 0x67a77b : 0xdb6a4e;
  const disk = new THREE.Mesh(new THREE.CircleGeometry(1.12, 48), new THREE.MeshBasicMaterial({ color, transparent: true, opacity: .18, side: THREE.DoubleSide, depthWrite: false }));
  disk.rotation.x = -Math.PI / 2; disk.position.set(x, .045, z); parent.add(disk);
  const ring = new THREE.Mesh(new THREE.RingGeometry(1.06, 1.13, 48), new THREE.MeshBasicMaterial({ color, transparent: true, opacity: .88, side: THREE.DoubleSide, depthWrite: false }));
  ring.rotation.x = -Math.PI / 2; ring.position.set(x, .055, z); parent.add(ring);
  sceneLabel(parent, label, [x, .34, z], .34, pass ? "#4e765b" : "#a84d3a");
}
function addAccessIssue(parent, x, z, label) {
  const halo = new THREE.Mesh(new THREE.RingGeometry(.38, .56, 30), new THREE.MeshBasicMaterial({ color: 0xd9563e, transparent: true, opacity: .9, side: THREE.DoubleSide, depthWrite: false }));
  halo.rotation.x = -Math.PI / 2; halo.position.set(x, .08, z); parent.add(halo);
  const pin = new THREE.Mesh(new THREE.SphereGeometry(.17, 18, 12), new THREE.MeshBasicMaterial({ color: 0xd9563e })); pin.position.set(x, .34, z); parent.add(pin);
  sceneLabel(parent, label, [x, .72, z], .38, "#a84331");
}

// Current model: three geometric constraints interrupt the required route.
sceneBox(accessAuditGroup, [5.2, 1.05, 1.2], [-2.6, .53, -3.8], 0xd87456);
sceneLabel(accessAuditGroup, "ORDER", [-2.6, 1.38, -3.8], .4);
sceneBox(accessAuditGroup, [3.2, .85, 1.1], [2.1, .43, -3.65], 0xe6a15c);
sceneLabel(accessAuditGroup, "PICK-UP", [2.1, 1.14, -3.65], .36);
sceneBox(accessAuditGroup, [1.35, 1.25, 2.2], [-.15, .63, -1.75], 0xa6b9a8);
sceneLabel(accessAuditGroup, "PROMO", [-.15, 1.55, -1.75], .32);
addAccessTable(accessAuditGroup, -4.9, 1.35);
addAccessTable(accessAuditGroup, -.1, 1.55, .9);
addAccessTable(accessAuditGroup, 3.4, 1.45, .88);
addAccessTable(accessAuditGroup, 6.3, 1.2, .9);
addAccessRoute(accessAuditGroup, [[-7.2,5.15],[-6.1,3.1],[-3.7,-1.5],[-2.5,-3.0]], 0xd29b4d, .18);
addAccessRoute(accessAuditGroup, [[-2.5,-3.0],[-.5,-2.65],[2.05,-3.05]], 0xd95d43, .16);
addAccessRoute(accessAuditGroup, [[2.05,-3.05],[2.2,-.9],[3.4,.9]], 0xd95d43, .15);
addAccessRoute(accessAuditGroup, [[3.4,.9],[5.25,-1.2],[6.55,-4.25]], 0xd95d43, .14);
addTurningCircle(accessAuditGroup, -7.15, 5.05, true, "ENTRY OK");
addTurningCircle(accessAuditGroup, -2.6, -2.75, false, "COUNTER 1.2 M");
addTurningCircle(accessAuditGroup, 6.55, -4.2, false, "RESTROOM 1.1 M");
addAccessIssue(accessAuditGroup, -.45, -2.45, "0.86 M");
addAccessIssue(accessAuditGroup, 2.35, .25, "0.94 M");
addAccessIssue(accessAuditGroup, 5.35, -2.0, "0.78 M");

// Proposed model: the same program with a continuous accessible route.
sceneBox(accessProposalGroup, [5.2, 1.05, 1.2], [-3.15, .53, -4.45], 0xd87456);
sceneLabel(accessProposalGroup, "ORDER", [-3.15, 1.38, -4.45], .4);
const proposedPickup = sceneBox(accessProposalGroup, [1.1, .85, 3.2], [1.9, .43, -3.45], 0xe6a15c);
proposedPickup.rotation.y = -.08;
sceneLabel(accessProposalGroup, "PICK-UP", [1.9, 1.14, -3.45], .36);
addAccessTable(accessProposalGroup, -5.05, 1.55);
addAccessTable(accessProposalGroup, -.6, 2.45, 1.05);
addAccessTable(accessProposalGroup, 4.15, 2.1, 1.05);
addAccessTable(accessProposalGroup, 7.0, .9, 1.0);
addAccessRoute(accessProposalGroup, [[-7.2,5.15],[-6.1,3.15],[-4.5,.1],[-3.15,-3.55],[.2,-3.0],[1.9,-2.55],[2.5,-.2],[4.15,1.5],[5.5,-.6],[6.55,-4.25]], 0x66a378, .24, .88);
addTurningCircle(accessProposalGroup, -7.15, 5.05, true, "ENTRY 1.5 M");
addTurningCircle(accessProposalGroup, -3.15, -3.3, true, "COUNTER 1.5 M");
addTurningCircle(accessProposalGroup, 6.55, -4.2, true, "RESTROOM 1.5 M");
sceneLabel(accessProposalGroup, "ROW 01 +0.55 M", [-.15, 1.25, -1.75], .38, "#5b7d65", "rgba(232,244,235,.95)");
sceneLabel(accessProposalGroup, "LOW-IMPRESSION ISLAND REMOVED", [.3, .35, -.9], .42, "#5b7d65", "rgba(232,244,235,.95)");
accessProposalGroup.visible = false;

const auditFindings = `
  <article class="pass"><b>✓</b><div><strong>Entrance → ordering</strong><small>1.34 m clear</small></div></article>
  <article class="fail"><b>!</b><div><strong>Ordering → pick-up</strong><small>0.86 m at display edge</small></div></article>
  <article class="fail"><b>!</b><div><strong>Pick-up → seating</strong><small>0.94 m between chairs</small></div></article>
  <article class="fail"><b>!</b><div><strong>Seating → restroom</strong><small>0.78 m at door approach</small></div></article>`;
const proposalFindings = `
  <article class="pass"><b>✓</b><div><strong>Continuous essential route</strong><small>Minimum width: 1.22 m</small></div></article>
  <article class="pass"><b>✓</b><div><strong>Entrance clearance</strong><small>1.50 m turning circle</small></div></article>
  <article class="pass"><b>✓</b><div><strong>Service clearance</strong><small>1.50 m turning circle</small></div></article>
  <article class="pass"><b>✓</b><div><strong>Restroom clearance</strong><small>1.50 m turning circle</small></div></article>`;
function setAccessMode(mode) {
  const proposed = mode === "proposal";
  accessAuditGroup.visible = !proposed; accessProposalGroup.visible = proposed;
  document.querySelectorAll("[data-access-mode]").forEach((button) => button.classList.toggle("active", button.dataset.accessMode === mode));
  document.getElementById("accessViewerEyebrow").textContent = proposed ? "PROPOSED MODEL · ACCESSIBLE LAYOUT" : "CURRENT MODEL · ACCESSIBILITY OVERLAY";
  document.getElementById("accessViewerTitle").textContent = proposed ? "One continuous route connects every service" : "Three points interrupt the route";
  const score = document.getElementById("accessScore"); score.textContent = proposed ? "94 / 100" : "62 / 100"; score.className = proposed ? "pass" : "fail";
  document.getElementById("accessModeLabel").textContent = proposed ? "Recommended layout" : "Audit overlay";
  document.getElementById("accessPanelTitle").textContent = proposed ? "The agent changed only what the evidence justified." : "One route must connect every essential service.";
  document.getElementById("accessFindings").innerHTML = proposed ? proposalFindings : auditFindings;
  document.getElementById("accessRecommendationTitle").textContent = proposed ? "A better layout, with a reason for every move." : "Move three elements, not the whole layout.";
  document.getElementById("accessRecommendationBody").textContent = proposed ? "The design preserves service capacity, opens the wheelchair route, and removes a display that had low impressions but created high congestion." : "Shift Row 01 by 0.55 m, rotate the pick-up counter, and remove the low-impression promo island.";
  document.getElementById("accessOutcome").textContent = proposed ? "Validated: 1.22 m minimum clear width" : "Proposed result: 1.2 m continuous route";
  document.getElementById("accessOutcomeDetail").textContent = proposed ? "The same agent can optimize queueing, merchandising, safety, energy, or any measurable spatial objective." : "The same audit loop can optimize queueing, merchandising, safety, energy, or any measurable spatial objective.";
}
document.querySelectorAll("[data-access-mode]").forEach((button) => button.addEventListener("click", () => setAccessMode(button.dataset.accessMode)));

function resizeAnalysis3d() {
  const width = Math.max(1, analysisViewport.clientWidth), height = Math.max(1, analysisViewport.clientHeight);
  analysisRenderer.setSize(width, height, false); analysisCamera.aspect = width / height; analysisCamera.updateProjectionMatrix();
}
function resizeLive() {
  const width = Math.max(1, liveViewport.clientWidth), height = Math.max(1, liveViewport.clientHeight);
  liveRenderer.setSize(width, height, false); liveCamera.aspect = width / height; liveCamera.updateProjectionMatrix();
}
function resizeAccessibility() {
  const width = Math.max(1, accessViewport.clientWidth), height = Math.max(1, accessViewport.clientHeight);
  accessRenderer.setSize(width, height, false); accessCamera.aspect = width / height; accessCamera.updateProjectionMatrix();
}

function resizeStore() {
  const width = Math.max(1, viewport.clientWidth); const height = Math.max(1, viewport.clientHeight);
  renderer.setSize(width, height, false); camera.aspect = width / height; camera.updateProjectionMatrix();
}
window.addEventListener("resize", () => { resizeStore(); resizeAnalysis3d(); resizeLive(); resizeAccessibility(); });
const animationClock = new THREE.Clock();
let liveElapsed = 0;
function animate() {
  const delta = animationClock.getDelta();
  controls.update(); renderer.render(scene, camera);
  analysisControls.update(); analysisRenderer.render(analysisScene, analysisCamera);
  liveControls.update();
  if (!livePaused) {
    liveElapsed += delta;
    livePeople.forEach((person) => {
      const t = (liveElapsed * person.speed + person.phase) % 1;
      const position = person.curve.getPointAt(t); const next = person.curve.getPointAt((t + .004) % 1);
      person.avatar.position.set(position.x, 0, position.z); person.avatar.rotation.y = Math.atan2(next.x - position.x, next.z - position.z);
    });
  }
  document.getElementById("liveClock").textContent = new Date().toLocaleTimeString("en-GB", { hour12: false });
  liveRenderer.render(liveScene, liveCamera);
  accessControls.update(); accessRenderer.render(accessScene, accessCamera);
  requestAnimationFrame(animate);
}

const initialSlide = Number(location.hash.slice(1));
showSlide(Number.isFinite(initialSlide) && initialSlide > 0 ? initialSlide - 1 : 0);
showStep("scan"); resizeStore(); resizeAnalysis3d(); resizeLive(); resizeAccessibility(); animate();
