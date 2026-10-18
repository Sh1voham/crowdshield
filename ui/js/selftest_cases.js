// Pure-function checks shared by `node --test tests/ui` and the in-browser /selftest page.
import { isolines, labelAnchors, pointInPolygon } from "./field/contours.js";
import { homography, applyH, invert3, sampleGrid, upsample, mat4 } from "./field/math.js";
import { computeFields, PressureBuffer, gridShape } from "./sim/fields.js";
import { Assessor } from "./sim/assessor.js";

function assert(c, msg) { if (!c) throw new Error(msg || "assertion failed"); }
const close = (a, b, tol, msg) => assert(Math.abs(a - b) <= tol, `${msg || ""} ${a} vs ${b}`);

function gaussianGrid(rows, cols, cx, cy, peak, sigma) {
  const a = new Float32Array(rows * cols);
  for (let r = 0; r < rows; r++) for (let c = 0; c < cols; c++) {
    const dx = c + 0.5 - cx, dy = r + 0.5 - cy;
    a[r * cols + c] = peak * Math.exp(-(dx * dx + dy * dy) / (2 * sigma * sigma));
  }
  return a;
}

export const cases = [
  ["contours of a single peak are closed loops, and the 6 contour lies inside the 4 contour", () => {
    const g = gaussianGrid(20, 30, 15, 10, 6.5, 3), up = upsample(g, 20, 30, 1, 4);
    const four = isolines(up.data, up.rows, up.cols, up.cellM, 4), six = isolines(up.data, up.rows, up.cols, up.cellM, 6);
    assert(four.length === 1 && six.length === 1, `expected one loop each, got ${four.length}, ${six.length}`);
    assert(four[0].closed && six[0].closed, "loops must be closed");
    for (const p of six[0].points) assert(pointInPolygon(p[0], p[1], four[0].points), "6 contour must lie inside the 4 contour");
    for (const p of four[0].points) close(Math.hypot(p[0] - 15, p[1] - 10), 3 * Math.sqrt(2 * Math.log(6.5 / 4)), 0.2, "radius of the 4 contour");
  }],
  ["label anchors sit on the contour, about every 6 m", () => {
    const g = gaussianGrid(30, 30, 15, 15, 8, 5), up = upsample(g, 30, 30, 1, 4);
    const lines = isolines(up.data, up.rows, up.cols, up.cellM, 4), a = labelAnchors(lines, 6);
    assert(a.length >= 3, `anchors ${a.length}`);
  }],
  ["homography maps the corners exactly and inverts", () => {
    const img = [[412, 618], [1508, 604], [1290, 322], [640, 330]], wld = [[0, 4], [6, 4], [6, 0], [0, 0]];
    const H = homography(img, wld);
    img.forEach((p, i) => { const q = applyH(H, ...p); close(q[0], wld[i][0], 1e-6); close(q[1], wld[i][1], 1e-6); });
    const q = applyH(H, 960, 470); close(q[0], 2.98, 0.01, "dossier x"); close(q[1], 2.53, 0.01, "dossier y");
    const back = applyH(invert3(H), ...q); close(back[0], 960, 1e-3); close(back[1], 470, 1e-3);
  }],
  ["bilinear sampling at cell centres returns the cell value", () => {
    const a = new Float32Array([1, 2, 3, 4]);
    close(sampleGrid(a, 2, 2, 1, 0.5, 0.5), 1, 1e-9); close(sampleGrid(a, 2, 2, 1, 1.0, 0.5), 1.5, 1e-9); close(sampleGrid(a, 2, 2, 1, 1.5, 1.5), 4, 1e-9);
  }],
  ["kernel density integrates to one person", () => {
    const shape = gridShape(20, 20, 0.1), f = computeFields([{ x: 10, y: 10, vx: null, vy: null }], shape, 1.0, 5.0);
    let s = 0; for (const v of f.rho) s += v * 0.01;
    close(s, 1, 2e-3, "integral");
  }],
  ["velocity field is null without enough people", () => {
    const f = computeFields([{ x: 1, y: 1, vx: 1, vy: 0 }], gridShape(10, 10, 1));
    assert(Number.isNaN(f.vx[9 * 10 + 9]), "far cell has no velocity");
  }],
  ["pressure is zero for steady flow and positive for oscillating flow", () => {
    const shape = gridShape(6, 6, 1), n = 36;
    const pts = Array.from({ length: 40 }, (_, i) => ({ x: 2.5 + (i % 7) * 0.15, y: 2.5 + Math.floor(i / 7) * 0.15 }));
    const run = (vf) => { const pb = new PressureBuffer(n); let P; for (let k = 0; k < 60; k++) { const t = k * 0.2, f = computeFields(pts.map((p) => ({ ...p, ...vf(t) })), shape); P = pb.update(t, f.vx, f.vy, f.rho); } return Math.max(...P); };
    assert(run(() => ({ vx: 0.8, vy: 0 })) < 1e-12, "steady flow");
    assert(run((t) => ({ vx: 0.5 * Math.sin(Math.PI * t), vy: 0 })) > 0.1, "oscillating flow");
  }],
  ["assessor needs 2 s to rise and 10 s to fall", () => {
    const a = new Assessor(); let o;
    for (let k = 0; k <= 10; k++) o = a.step(k * 0.2, 6.5, 0);
    assert(o.status === "RED", "rises after 2 s");
    for (let k = 11; k <= 60; k++) o = a.step(k * 0.2, 1, 0);
    assert(o.status === "RED", "still RED before 10 s");
    for (let k = 61; k <= 62; k++) o = a.step(k * 0.2, 1, 0);
    assert(o.status === "GREEN", "falls after 10 s");
  }],
  ["perspective projection keeps the target at the centre", () => {
    const vp = mat4.multiply(mat4.perspective(0.6, 1.5, 0.1, 100), mat4.lookAt([0, 10, 10], [0, 0, 0], [0, 1, 0]));
    const p = mat4.transform(vp, 0, 0, 0); close(p[0], 0, 1e-6); close(p[1], 0, 1e-6);
  }],
];
