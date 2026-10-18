// Calibration page: 4 corners of a taped rectangle -> homography -> live metre grid preview.
import { homography, applyH, invert3 } from "./field/math.js";
import { cssVar } from "./util.js";

const $ = (id) => document.getElementById(id);
const img = $("frame"), ov = $("ov"), loupe = $("loupe"), ctx = ov.getContext("2d"), lctx = loupe.getContext("2d");
let pts = [];              // image pixels (natural resolution)
let drag = -1, checking = false, checkPts = [], hover = null;

const num = (id) => parseFloat($(id).value);
function worldRect() {
  const x0 = num("rx"), y0 = num("ry"), w = num("rw"), h = num("rh");
  return [[x0, y0], [x0 + w, y0], [x0 + w, y0 + h], [x0, y0 + h]];
}
function H() { return pts.length === 4 ? homography(pts, worldRect()) : null; }   // image -> floor
function scale() { return [img.clientWidth / (img.naturalWidth || 1), img.clientHeight / (img.naturalHeight || 1)]; }
function toImg(e) { const r = ov.getBoundingClientRect(), [kx, ky] = scale(); return [(e.clientX - r.left) / kx, (e.clientY - r.top) / ky]; }
function msg(t, kind = "ok") { $("msg").textContent = t; $("msg").dataset.kind = kind; }

function loadFrame(src) {
  img.onload = () => { $("frame-note").textContent = `${img.naturalWidth} × ${img.naturalHeight} px still frame`; resize(); };
  img.onerror = () => {
    $("frame-note").textContent = "No frame source yet: upload a snapshot of the camera view (a phone photo from the camera position works).";
    if (!loadFrame.retried) { loadFrame.retried = true; setTimeout(() => loadFrame("/api/frame.jpg?_=" + Date.now()), 1500); }
  };
  img.src = src;
}
function resize() { ov.width = img.clientWidth * devicePixelRatio; ov.height = img.clientHeight * devicePixelRatio; ctx.setTransform(devicePixelRatio, 0, 0, devicePixelRatio, 0, 0); draw(); }
new ResizeObserver(resize).observe(img);

function draw() {
  const [kx, ky] = scale();
  ctx.clearRect(0, 0, ov.width, ov.height);
  const h = H();
  if (h) {
    const inv = invert3(h), aw = num("aw"), ah = num("ah");
    const P = (x, y) => { const [u, v] = applyH(inv, x, y); return [u * kx, v * ky]; };
    // projected 1 m grid over the monitored area
    ctx.strokeStyle = "rgba(160, 82, 45, 0.85)"; ctx.lineWidth = 1;
    for (let x = 0; x <= aw + 1e-6; x += 1) { ctx.beginPath(); for (let y = 0; y <= ah; y += ah / 20) { const p = P(x, y); y ? ctx.lineTo(...p) : ctx.moveTo(...p); } ctx.stroke(); }
    for (let y = 0; y <= ah + 1e-6; y += 1) { ctx.beginPath(); for (let x = 0; x <= aw; x += aw / 20) { const p = P(x, y); x ? ctx.lineTo(...p) : ctx.moveTo(...p); } ctx.stroke(); }
    ctx.strokeStyle = cssVar("--ink"); ctx.lineWidth = 2.5;
    const c = [[0, 0], [aw, 0], [aw, ah], [0, ah]].map(([x, y]) => P(x, y));
    ctx.beginPath(); c.forEach((p, i) => (i ? ctx.lineTo(...p) : ctx.moveTo(...p))); ctx.closePath(); ctx.stroke();
    // gate
    const gx = $("gside").value === "left" ? 0 : aw, g0 = P(gx, num("gy0")), g1 = P(gx, num("gy1"));
    ctx.strokeStyle = cssVar("--ok"); ctx.lineWidth = 7; ctx.beginPath(); ctx.moveTo(...g0); ctx.lineTo(...g1); ctx.stroke();
  }
  // corner handles
  pts.forEach((p, i) => {
    const x = p[0] * kx, y = p[1] * ky;
    ctx.fillStyle = "#ffd400"; ctx.strokeStyle = "#000"; ctx.lineWidth = 2;
    ctx.beginPath(); ctx.arc(x, y, 7, 0, Math.PI * 2); ctx.fill(); ctx.stroke();
    ctx.fillStyle = "#000"; ctx.font = "bold 13px sans-serif"; ctx.fillText(String(i + 1), x + 10, y - 8);
  });
  if (pts.length > 1) { ctx.strokeStyle = "#ffd400"; ctx.lineWidth = 2; ctx.beginPath(); pts.forEach((p, i) => (i ? ctx.lineTo(p[0] * kx, p[1] * ky) : ctx.moveTo(p[0] * kx, p[1] * ky))); if (pts.length === 4) ctx.closePath(); ctx.stroke(); }
  // distance check
  if (checkPts.length) {
    ctx.strokeStyle = cssVar("--focus"); ctx.fillStyle = cssVar("--focus"); ctx.lineWidth = 3;
    ctx.beginPath(); checkPts.forEach((p, i) => (i ? ctx.lineTo(p[0] * kx, p[1] * ky) : ctx.moveTo(p[0] * kx, p[1] * ky))); ctx.stroke();
    for (const p of checkPts) { ctx.beginPath(); ctx.arc(p[0] * kx, p[1] * ky, 5, 0, Math.PI * 2); ctx.fill(); }
  }
  [...$("steps").children].forEach((li, i) => (li.dataset.done = String(i < pts.length)));
}

function drawLoupe(e) {
  if (!img.naturalWidth) return;
  const r = $("wrap").getBoundingClientRect(), [ix, iy] = toImg(e);
  loupe.style.display = "block";
  loupe.style.left = `${e.clientX - r.left + 24}px`; loupe.style.top = `${e.clientY - r.top - 170}px`;
  const z = 4, s = 150 / z;
  lctx.clearRect(0, 0, 150, 150);
  lctx.imageSmoothingEnabled = false;
  lctx.drawImage(img, ix - s / 2, iy - s / 2, s, s, 0, 0, 150, 150);
  lctx.strokeStyle = "#ffd400"; lctx.beginPath(); lctx.moveTo(75, 60); lctx.lineTo(75, 90); lctx.moveTo(60, 75); lctx.lineTo(90, 75); lctx.stroke();
}

ov.addEventListener("pointerdown", (e) => {
  const p = toImg(e), [kx] = scale();
  if (checking) {
    if (checkPts.length >= 2) checkPts = [];
    checkPts.push(p);
    if (checkPts.length === 2) {
      const h = H();
      if (!h) msg("Click the 4 corners first.", "error");
      else { const a = applyH(h, ...checkPts[0]), b = applyH(h, ...checkPts[1]); msg(`Distance: ${Math.hypot(a[0] - b[0], a[1] - b[1]).toFixed(2)} m — compare with your tape measure.`); }
    }
    draw(); return;
  }
  const near = pts.findIndex((q) => Math.hypot(q[0] - p[0], q[1] - p[1]) * kx < 14);
  if (near >= 0) { drag = near; ov.setPointerCapture(e.pointerId); return; }
  if (pts.length < 4) { pts.push(p); draw(); if (pts.length === 4) msg("All 4 corners set. Check the grid lines up with the floor, then save."); }
});
ov.addEventListener("pointermove", (e) => { hover = e; drawLoupe(e); if (drag >= 0) { pts[drag] = toImg(e); draw(); } });
ov.addEventListener("pointerup", () => { drag = -1; });
ov.addEventListener("pointerleave", () => { loupe.style.display = "none"; });
for (const id of ["rw", "rh", "rx", "ry", "aw", "ah", "gy0", "gy1", "gside"]) $(id).addEventListener("input", draw);
$("clear").addEventListener("click", () => { pts = []; checkPts = []; draw(); msg(""); });
$("check").addEventListener("click", () => { checking = !checking; checkPts = []; $("check").setAttribute("aria-pressed", String(checking)); msg(checking ? "Click two points on the floor." : ""); draw(); });
$("reload").addEventListener("click", () => loadFrame("/api/frame.jpg?_=" + Date.now()));
$("plane").addEventListener("change", () => { if ($("plane").value === "head") $("foot").value = "head_plane"; load(); });
$("upload").addEventListener("change", async () => {
  const f = $("upload").files[0]; if (!f) return;
  const r = await fetch("/api/frame", { method: "POST", body: await f.arrayBuffer() });
  if (r.ok) { msg("Snapshot uploaded."); loadFrame("/api/frame.jpg?source=upload&_=" + Date.now()); }
  else msg(`Upload refused: ${(await r.json().catch(() => ({}))).error || r.status}`, "error");
});

$("save").addEventListener("click", async () => {
  if (pts.length !== 4) { msg("Click the 4 corners of the taped rectangle first.", "error"); return; }
  const aw = num("aw"), ah = num("ah");
  const body = {
    image_points: pts.map((p) => [Math.round(p[0] * 10) / 10, Math.round(p[1] * 10) / 10]),
    world_points: worldRect(), frame_size: [img.naturalWidth, img.naturalHeight],
    area: { width_m: aw, height_m: ah }, zone: $("zone").value || "Gate A", foot_mode: $("foot").value, head_k: 6.5,
    gate: { x: $("gside").value === "left" ? 0 : aw, y0: num("gy0"), y1: num("gy1"), label: $("glabel").value || "Gate" },
    note: `clicked on /calibrate ${new Date().toISOString()}`,
  };
  const r = await fetch(`/api/calibration?plane=${$("plane").value}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const j = await r.json().catch(() => ({}));
  if (r.ok) msg(j.message || "Saved. The engine reloads it within 2 s.");
  else msg(`Not saved: ${(j.problems || [j.error]).join("; ")}`, "error");
});

async function load() {
  try {
    const c = await (await fetch(`/api/calibration?plane=${$("plane").value}`)).json();
    if (c.exists) {
      pts = c.image_points.map((p) => [...p]);
      const w = c.world_points, xs = w.map((p) => p[0]), ys = w.map((p) => p[1]);
      $("rx").value = Math.min(...xs); $("ry").value = Math.min(...ys);
      $("rw").value = Math.max(...xs) - Math.min(...xs); $("rh").value = Math.max(...ys) - Math.min(...ys);
      $("aw").value = c.area.width_m; $("ah").value = c.area.height_m; $("zone").value = c.zone || "";
      $("foot").value = c.foot_mode || "bbox_bottom";
      if (c.gate) { $("gside").value = (c.gate.x ?? c.area.width_m) <= 0.01 ? "left" : "right"; $("gy0").value = c.gate.y0; $("gy1").value = c.gate.y1; $("glabel").value = c.gate.label || "Gate"; }
      msg("Loaded the current calibration. Drag corners to adjust, or Clear points to start again.");
    }
  } catch (e) { msg("The calibration service needs the gateway (open this page from the gateway).", "error"); }
  draw();
}
loadFrame("/api/frame.jpg?_=" + Date.now());
load();
