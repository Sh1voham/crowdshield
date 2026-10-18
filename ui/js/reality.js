// Reality lock: the same analysis projected onto the real camera image through the inverse
// homography — density, contours, pressure rings, people marks and the gate line.
// Video comes from the gateway (/api/video.mjpg: PipeGen's VMS stream or the video file the
// pipeline plays). Without a stream it falls back to a still frame, labelled as such.

import { homography, invert3, applyH, flatGrid, upsample } from "./field/math.js";
import { isolines } from "./field/contours.js";
import { cssVar, isNum } from "./util.js";

export class RealityLock {
  constructor(root) {
    this.root = root;
    this.img = root.querySelector("#reality-img");
    this.canvas = root.querySelector("#reality-canvas");
    this.ctx = this.canvas.getContext("2d");
    this.stillLabel = root.querySelector("#reality-still");
    this.credit = root.querySelector("#reality-credit");
    this.opacity = root.querySelector("#reality-opacity");
    this.on = false; this.calib = null; this.state = null; this.stillTimer = null;
    this.img.addEventListener("error", () => this._still());
    this.img.addEventListener("load", () => this.layout());
    new ResizeObserver(() => this.layout()).observe(root);
  }

  async enable() {
    this.on = true;
    this.root.classList.add("on");
    try {
      const cfg = await (await fetch("/api/config")).json();
      this.setCalibration(cfg.calibration);
      const fr = await (await fetch("/api/frames")).json();
      const v = String(fr.video || "");
      this.credit.textContent = /MOT(20|17)/.test(v) ? `Video: MOTChallenge ${v.split("/").pop()} (research licence, CC BY-NC-SA 3.0)` : "";
    } catch (e) { /* the gateway may be absent (simulator mode) */ }
    if (!this.calib) { this.stillLabel.style.display = "block"; this.stillLabel.textContent = "No calibration: open Calibrate to align the camera"; return; }
    this.stillLabel.style.display = "none";
    this.img.src = "/api/video.mjpg?" + Date.now();
  }

  disable() {
    this.on = false;
    this.root.classList.remove("on");
    this.img.removeAttribute("src");
    clearInterval(this.stillTimer); this.stillTimer = null;
  }

  _still() {
    if (!this.on || this.stillTimer) return;
    this.stillLabel.style.display = "block"; this.stillLabel.textContent = "Still frame (no video stream)";
    const load = () => { this.img.src = "/api/frame.jpg?_=" + Date.now(); };
    load();
    this.stillTimer = setInterval(load, 2000);
  }

  setCalibration(c) {
    if (!c || !c.image_points || !c.world_points) { this.calib = null; return; }
    const Himg2floor = homography(c.image_points, c.world_points);
    this.calib = { ...c, H: invert3(Himg2floor), frame: c.frame_size || [1920, 1080] };
  }

  layout() {
    const W = this.root.clientWidth, H = this.root.clientHeight;
    const fw = this.img.naturalWidth || this.calib?.frame?.[0] || 1920, fh = this.img.naturalHeight || this.calib?.frame?.[1] || 1080;
    const s = Math.min(W / fw, H / fh), dw = fw * s, dh = fh * s, ox = (W - dw) / 2, oy = (H - dh) / 2;
    Object.assign(this.img.style, { left: `${ox}px`, top: `${oy}px`, width: `${dw}px`, height: `${dh}px` });
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    this.canvas.style.left = `${ox}px`; this.canvas.style.top = `${oy}px`;
    this.canvas.style.width = `${dw}px`; this.canvas.style.height = `${dh}px`;
    this.canvas.width = Math.max(2, dw * dpr); this.canvas.height = Math.max(2, dh * dpr);
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    // calibration pixels -> display pixels
    const cf = this.calib?.frame || [fw, fh];
    this.k = [dw / cf[0], dh / cf[1]];
    this.draw();
  }

  setState(s) {
    this.state = s;
    if (s?.camera && JSON.stringify(s.camera.image_points) !== this._camKey) {
      this._camKey = JSON.stringify(s.camera.image_points);
      this.setCalibration(s.camera);
      if (this.on) this.layout();
    }
    if (this.on) this.draw();
  }

  P(x, y) { const [u, v] = applyH(this.calib.H, x, y); return [u * this.k[0], v * this.k[1]]; }

  draw() {
    const c = this.ctx, s = this.state;
    c.clearRect(0, 0, this.canvas.width, this.canvas.height);
    if (!this.on || !s?.grid || !this.calib || !this.k) return;
    const a = (this.opacity.value | 0) / 100;
    const g = s.grid, rows = g.rows, cols = g.cols, cell = g.cell_m || 1;
    const den = flatGrid(g.density, rows, cols, 0);
    const hi = cssVar("--terrain-hi"), contour = cssVar("--contour");
    // density warped into image space (one quad per sub-cell)
    const up = upsample(den, rows, cols, cell, 2);
    c.globalAlpha = 1;
    for (let r = 0; r < up.rows; r++) for (let cc = 0; cc < up.cols; cc++) {
      const d = up.data[r * up.cols + cc];
      if (d < 0.6) continue;
      const x0 = cc * up.cellM, y0 = r * up.cellM, x1 = x0 + up.cellM, y1 = y0 + up.cellM;
      const p = [this.P(x0, y0), this.P(x1, y0), this.P(x1, y1), this.P(x0, y1)];
      c.fillStyle = hi; c.globalAlpha = a * Math.min(0.55, d / 12);
      c.beginPath(); c.moveTo(...p[0]); c.lineTo(...p[1]); c.lineTo(...p[2]); c.lineTo(...p[3]); c.closePath(); c.fill();
    }
    // contours, index lines at Building and Critical
    const thr = [s.thresholds?.amber_density ?? 4, s.thresholds?.red_density ?? 6];
    const up4 = upsample(den, rows, cols, cell, 4);
    for (const lvl of [2, 3, thr[0], 5, thr[1]]) {
      const lines = isolines(up4.data, up4.rows, up4.cols, up4.cellM, lvl);
      const idx = thr.includes(lvl);
      c.strokeStyle = contour; c.globalAlpha = a * (idx ? 1 : 0.5); c.lineWidth = idx ? 3 : 1.2;
      for (const ln of lines) { c.beginPath(); ln.points.forEach((q, i) => { const P = this.P(q[0], q[1]); i ? c.lineTo(...P) : c.moveTo(...P); }); c.stroke(); }
    }
    // pressure rings
    const pthr = s.pressure?.threshold || 0.02, press = flatGrid(g.pressure, rows, cols, 0);
    const t = performance.now() / 1000;
    c.strokeStyle = hi; c.lineWidth = 2;
    for (let k = 0; k < press.length; k++) {
      if (press[k] / pthr < 1 || den[k] < 3) continue;
      const x = (k % cols + 0.5) * cell, y = (Math.floor(k / cols) + 0.5) * cell;
      for (let j = 0; j < 2; j++) {
        const f = (t / 1.6 + j * 0.5) % 1, R = f * 3.2;
        c.globalAlpha = a * (1 - f);
        c.beginPath();
        for (let i = 0; i <= 28; i++) { const th = i / 28 * Math.PI * 2, P = this.P(x + R * Math.cos(th), y + R * Math.sin(th)); i ? c.lineTo(...P) : c.moveTo(...P); }
        c.stroke();
      }
    }
    // people marks at their image positions
    c.globalAlpha = Math.min(1, a + 0.2);
    c.fillStyle = cssVar("--person"); c.strokeStyle = "#000"; c.lineWidth = 1;
    for (const tr of s.tracks || []) {
      if (!isNum(tr.x)) continue;
      const P = this.P(tr.x, tr.y);
      c.beginPath(); c.arc(P[0], P[1], 3.2, 0, Math.PI * 2); c.fill(); c.stroke();
    }
    // gate
    const gt = s.gate;
    if (gt && isNum(gt.y0)) {
      const lvl = s.status?.level;
      c.globalAlpha = 1; c.lineWidth = 6; c.strokeStyle = cssVar(lvl === "RED" ? "--crit" : lvl === "AMBER" ? "--warn" : "--ok");
      const A = this.P(gt.x ?? s.area.width_m, gt.y0), B = this.P(gt.x ?? s.area.width_m, gt.y1);
      c.beginPath(); c.moveTo(...A); c.lineTo(...B); c.stroke();
    }
    c.globalAlpha = 1;
  }
}
