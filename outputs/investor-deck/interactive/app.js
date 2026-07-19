import * as THREE from "three";
import { OrbitControls } from "./vendor/OrbitControls.js";

const slides = [...document.querySelectorAll(".slide")];
const counter = document.getElementById("slideCounter");
const progress = document.querySelector("#progress i");
let current = 0;

function showSlide(next) {
  current = (next + slides.length) % slides.length;
  slides.forEach((slide, index) => slide.classList.toggle("active", index === current));
  counter.textContent = `${String(current + 1).padStart(2, "0")} / ${String(slides.length).padStart(2, "0")}`;
  progress.style.width = `${((current + 1) / slides.length) * 100}%`;
  history.replaceState(null, "", `#${current + 1}`);
  if (current === 5) resizeTwin();
}

document.getElementById("prevSlide").addEventListener("click", () => showSlide(current - 1));
document.getElementById("nextSlide").addEventListener("click", () => showSlide(current + 1));
document.getElementById("fullscreen").addEventListener("click", () => {
  if (!document.fullscreenElement) document.documentElement.requestFullscreen();
  else document.exitFullscreen();
});
window.addEventListener("keydown", (event) => {
  if (walkMode && current === 5 && ["ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight", "w", "a", "s", "d", "W", "A", "S", "D"].includes(event.key)) return;
  if (["ArrowRight", "PageDown", " "].includes(event.key)) showSlide(current + 1);
  if (["ArrowLeft", "PageUp"].includes(event.key)) showSlide(current - 1);
  if (event.key === "Home") showSlide(0);
  if (event.key === "End") showSlide(slides.length - 1);
});

// Interactive semantic twin -------------------------------------------------
const viewport = document.getElementById("twinViewport");
const scene = new THREE.Scene();
scene.background = new THREE.Color(0x0d0d12);
scene.fog = new THREE.Fog(0x0d0d12, 18, 36);

const camera = new THREE.PerspectiveCamera(48, 1, 0.1, 100);
camera.position.set(11, 10, 13);
const renderer = new THREE.WebGLRenderer({ antialias: true });
renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
renderer.shadowMap.enabled = true;
renderer.shadowMap.type = THREE.PCFSoftShadowMap;
viewport.appendChild(renderer.domElement);

const controls = new OrbitControls(camera, renderer.domElement);
controls.target.set(0, 1.2, 0);
controls.enableDamping = true;
controls.maxPolarAngle = Math.PI * 0.49;
controls.minDistance = 4;
controls.maxDistance = 28;

scene.add(new THREE.HemisphereLight(0xded9ff, 0x191525, 2.2));
const keyLight = new THREE.DirectionalLight(0xffffff, 3.4);
keyLight.position.set(6, 13, 5);
keyLight.castShadow = true;
scene.add(keyLight);
const violetLight = new THREE.PointLight(0x7462ff, 42, 15);
violetLight.position.set(-3, 5, -1);
scene.add(violetLight);

const selectable = [];
const layers = { cameras: new THREE.Group(), semantics: new THREE.Group(), tracks: new THREE.Group(), heatmap: new THREE.Group() };
Object.values(layers).forEach((group) => scene.add(group));

function material(color, roughness = .72, opacity = 1) {
  return new THREE.MeshStandardMaterial({ color, roughness, metalness: .08, transparent: opacity < 1, opacity });
}
function addBox(name, size, position, color, semantic, parent = scene) {
  const mesh = new THREE.Mesh(new THREE.BoxGeometry(...size), material(color));
  mesh.position.set(...position);
  mesh.castShadow = true;
  mesh.receiveShadow = true;
  mesh.userData = { name, semantic };
  parent.add(mesh);
  selectable.push(mesh);
  return mesh;
}
function outline(mesh, color = 0x7462ff) {
  const edge = new THREE.LineSegments(new THREE.EdgesGeometry(mesh.geometry), new THREE.LineBasicMaterial({ color, transparent: true, opacity: .88 }));
  edge.position.copy(mesh.position);
  edge.rotation.copy(mesh.rotation);
  layers.semantics.add(edge);
  return edge;
}

const floor = new THREE.Mesh(new THREE.PlaneGeometry(20, 14), material(0x202027, .94));
floor.rotation.x = -Math.PI / 2;
floor.receiveShadow = true;
floor.userData = { name: "Main floor", semantic: "Floor plane · world z=0 · active calibration" };
scene.add(floor);
selectable.push(floor);
const grid = new THREE.GridHelper(20, 20, 0x5148a8, 0x292735);
grid.position.y = .012;
scene.add(grid);

addBox("North wall", [20, 4, .18], [0, 2, -7], 0x24242b, "Structural wall · stable scene anchor");
addBox("West wall", [.18, 4, 14], [-10, 2, 0], 0x24242b, "Structural wall · stable scene anchor");
addBox("Checkout", [4.2, 1.1, 1.3], [6.3, .55, 4.9], 0xd8d2c7, "Checkout fixture · queue zone nearby");

[-5.4, -1.8, 1.8].forEach((x, index) => {
  const shelf = addBox(`Shelf ${String.fromCharCode(65 + index)}`, [1.2, 2.5, 8.4], [x, 1.25, -0.6], 0x4f4b58, `Semantic shelf volume · ${8 + index * 3} active detections`);
  outline(shelf, index === 1 ? 0x5fe7e7 : 0x7462ff);
  for (let row = 0; row < 3; row++) {
    const ledge = new THREE.Mesh(new THREE.BoxGeometry(1.28, .08, 8.5), material(0x9e98a7));
    ledge.position.set(x, .62 + row * .76, -.6);
    layers.semantics.add(ledge);
  }
});

const entranceZone = new THREE.Mesh(new THREE.PlaneGeometry(4.4, 2.8), new THREE.MeshBasicMaterial({ color: 0x5fe7e7, transparent: true, opacity: .18, side: THREE.DoubleSide }));
entranceZone.rotation.x = -Math.PI / 2;
entranceZone.position.set(6.3, .03, -5.2);
entranceZone.userData = { name: "Entrance zone", semantic: "Operational zone · anonymous traffic counting" };
layers.semantics.add(entranceZone);
selectable.push(entranceZone);
const queueZone = entranceZone.clone();
queueZone.material = new THREE.MeshBasicMaterial({ color: 0xffb75e, transparent: true, opacity: .2, side: THREE.DoubleSide });
queueZone.scale.set(1.25, 1.55, 1);
queueZone.position.set(5.9, .035, 2.7);
queueZone.userData = { name: "Queue zone", semantic: "Operational zone · dwell + occupancy rules" };
layers.semantics.add(queueZone);
selectable.push(queueZone);

function addPerson(x, z, color = 0xefece4) {
  const group = new THREE.Group();
  const body = new THREE.Mesh(new THREE.CapsuleGeometry(.24, .8, 6, 10), material(color, .55));
  body.position.y = 1.05;
  body.castShadow = true;
  group.add(body);
  group.position.set(x, 0, z);
  scene.add(group);
  return group;
}
[[-7.3,-3.5],[-3.6,2.1],[.1,-3.8],[3.8,.4],[5.7,2.7],[6,-4.9]].forEach((p, i) => addPerson(p[0], p[1], i === 4 ? 0xffcf84 : 0xe7e1f2));

function addCamera(name, x, z, rotY) {
  const group = new THREE.Group();
  const body = new THREE.Mesh(new THREE.BoxGeometry(.6,.34,.72), material(0xdedce6,.35));
  body.position.y = 3.5;
  group.add(body);
  const cone = new THREE.Mesh(new THREE.ConeGeometry(2.7, 5.2, 4, 1, true), new THREE.MeshBasicMaterial({ color: 0x7462ff, transparent: true, opacity: .12, side: THREE.DoubleSide, wireframe: false }));
  cone.rotation.x = Math.PI / 2;
  cone.position.set(0, 2.4, -2.5);
  group.add(cone);
  group.position.set(x, 0, z);
  group.rotation.y = rotY;
  group.userData = { name, semantic: "Fixed camera · calibrated pose · observation source" };
  layers.cameras.add(group);
  selectable.push(body);
  body.userData = group.userData;
}
addCamera("Camera 01", -8.8, 5.5, Math.PI * .1);
addCamera("Camera 02", 8.8, -5.5, Math.PI * 1.1);
addCamera("Camera 03", 8.6, 5.6, Math.PI * .65);

const trackPoints = [new THREE.Vector3(-7.8,.08,-5.6), new THREE.Vector3(-5.4,.08,-3.8), new THREE.Vector3(-2.7,.08,-2.4), new THREE.Vector3(.4,.08,-2.0), new THREE.Vector3(3.1,.08,-.5), new THREE.Vector3(5.8,.08,2.3)];
const trackCurve = new THREE.CatmullRomCurve3(trackPoints);
const track = new THREE.Mesh(new THREE.TubeGeometry(trackCurve, 64, .045, 8, false), new THREE.MeshBasicMaterial({ color: 0xffb75e }));
layers.tracks.add(track);

[[-7,-3.5,.95],[-3.6,2.1,.8],[.1,-3.8,1.25],[3.8,.4,.65],[5.7,2.7,1.15]].forEach(([x,z,s], i) => {
  const disc = new THREE.Mesh(new THREE.CircleGeometry(1.15 * s, 32), new THREE.MeshBasicMaterial({ color: i % 2 ? 0x7462ff : 0x5fe7e7, transparent: true, opacity: .2, depthWrite: false, side: THREE.DoubleSide }));
  disc.rotation.x = -Math.PI / 2;
  disc.position.set(x,.045,z);
  layers.heatmap.add(disc);
});

let walkMode = false;
const keys = new Set();
window.addEventListener("keydown", (e) => keys.add(e.key.toLowerCase()));
window.addEventListener("keyup", (e) => keys.delete(e.key.toLowerCase()));
document.getElementById("modeButton").addEventListener("click", () => {
  walkMode = !walkMode;
  controls.enabled = !walkMode;
  document.getElementById("modeButton").textContent = walkMode ? "Return to orbit" : "Enter walk mode";
  if (walkMode) {
    camera.position.set(7, 1.65, 5.6);
    camera.rotation.set(0, -2.35, 0);
  } else resetView();
});
function resetView() {
  walkMode = false;
  controls.enabled = true;
  document.getElementById("modeButton").textContent = "Enter walk mode";
  camera.position.set(11,10,13);
  controls.target.set(0,1.2,0);
  controls.update();
}
document.getElementById("resetView").addEventListener("click", resetView);

document.querySelectorAll("[data-toggle]").forEach((button) => {
  button.addEventListener("click", () => {
    const layer = layers[button.dataset.toggle];
    layer.visible = !layer.visible;
    button.classList.toggle("on", layer.visible);
  });
});

const raycaster = new THREE.Raycaster();
const pointer = new THREE.Vector2();
renderer.domElement.addEventListener("pointerdown", (event) => {
  const rect = renderer.domElement.getBoundingClientRect();
  pointer.x = ((event.clientX - rect.left) / rect.width) * 2 - 1;
  pointer.y = -((event.clientY - rect.top) / rect.height) * 2 + 1;
  raycaster.setFromCamera(pointer, camera);
  const hit = raycaster.intersectObjects(selectable, true).find((entry) => entry.object.userData?.name);
  if (hit) document.getElementById("selectionInfo").innerHTML = `<strong>${hit.object.userData.name}</strong><br>${hit.object.userData.semantic}`;
});

function resizeTwin() {
  const width = Math.max(1, viewport.clientWidth);
  const height = Math.max(1, viewport.clientHeight);
  renderer.setSize(width, height, false);
  camera.aspect = width / height;
  camera.updateProjectionMatrix();
}
window.addEventListener("resize", resizeTwin);
resizeTwin();

const clock = new THREE.Clock();
function animate() {
  const dt = Math.min(clock.getDelta(), .05);
  if (walkMode && current === 5) {
    const speed = 3.2 * dt;
    const turn = 1.35 * dt;
    if (keys.has("arrowleft") || keys.has("a")) camera.rotation.y += turn;
    if (keys.has("arrowright") || keys.has("d")) camera.rotation.y -= turn;
    const direction = new THREE.Vector3(0,0,-1).applyQuaternion(camera.quaternion);
    direction.y = 0;
    direction.normalize();
    if (keys.has("arrowup") || keys.has("w")) camera.position.addScaledVector(direction, speed);
    if (keys.has("arrowdown") || keys.has("s")) camera.position.addScaledVector(direction, -speed);
    camera.position.x = THREE.MathUtils.clamp(camera.position.x, -9.2, 9.2);
    camera.position.z = THREE.MathUtils.clamp(camera.position.z, -6.2, 6.2);
    camera.position.y = 1.65;
  } else controls.update();
  layers.heatmap.children.forEach((disc, index) => { disc.material.opacity = .14 + Math.sin(performance.now() * .0015 + index) * .05; });
  renderer.render(scene, camera);
  requestAnimationFrame(animate);
}
const initial = Number(location.hash.slice(1));
showSlide(Number.isFinite(initial) && initial > 0 ? initial - 1 : 0);
animate();
