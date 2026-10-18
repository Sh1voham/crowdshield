// Canvas 2D fallback: the same layers from directly above, for machines without WebGL2
// (or ?renderer=2d). Density as a warm smooth image with contours, flow streaks, people
// with trails, pressure rings and the gate.

import { flatGrid, sampleGrid, upsample } from "./math.js";
import { isolines, labelAnchors } from "./contours.js";
import { cssVar, reducedMotion, clamp, isNum } from "../util.js";

export class Field2D {
  constructor(canvas, labelsEl) {
    this.canvas = canvas; this.labelsEl = labelsEl; this.kind = "canvas2d";
    this.ctx = canvas.getContext("2d");
    this.state = null; this.tracks = new Map(); this.particles = []; this.lowPower = false;
    this.off = document.createElement("canvas");
    this.ro = new ResizeObserver(() => this.resize()); this.ro.observe(canvas);
    this.resize();
    this.last = performance.now();
    canvas.addEventListener("pointermove", (e) => { const r = canvas.getBoundingClientRect(); this.hover = [e.clientX - r.left, e.clientY - r.top]; });
    canvas.addEventListener("pointerleave", () => { this.hover = null; });
    this.cpuMs = 0; this.gpuMs = null;
  }

  resize() {
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    this.cw = this.canvas.clientWidth; this.ch = this.canvas.clientHeight;
    this.canvas.width = Math.max(2, this.cw * dpr); this.canvas.height = Math.max(2, this.ch * dpr);
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }

  setState(s) {
    if (!s?.grid || !s.area) return;
    this.state = s;
    const g = s.grid;
    this.grid = { rows: g.rows, cols: g.cols, cell: g.cell_m || 1 };
    this.area = { w: s.area.width_m, h: s.area.height_m };
    this.den = flatGrid(g.density, g.rows, g.cols, 0);
    const pthr = s.pressure?.threshold || 0.02;
    this.press = flatGrid(g.pressure, g.rows, g.cols, 0).map((v) => v / pthr);
    this.vx = g.vx ? flatGrid(g.vx, g.rows, g.cols, NaN) : null;
    this.vy = g.vy ? flatGrid(g.vy, g.rows, g.cols, NaN) : null;
    this.thr = { amber: s.thresholds?.amber_density ?? 4, red: s.thresholds?.red_density ?? 6 };
    const up = upsample(this.den, g.rows, g.cols, this.grid.cell, 4);
    this.iso = {};
    for (const lvl of [1, 2, 3, this.thr.amber, 5, this.thr.red, 7]) this.iso[lvl] = isolines(up.data, up.rows, up.cols, up.cellM, lvl);
    this.anchors = { [this.thr.amber]: labelAnchors(this.iso[this.thr.amber]), [this.thr.red]: labelAnchors(this.iso[this.thr.red]) };
    this._heat();
    const now = performance.now() / 1000;
    const seen = new Set();
    for (const t of s.tracks || []) {
      if (t.id == null) continue;
      seen.add(t.id);
      const h = this.tracks.get(t.id) || []; h.push({ x: t.x, y: t.y, w: now }); while (h.length && now - h[0].w > 3) h.shift();
      this.tracks.set(t.id, h);
    }
    for (const [id, h] of this.tracks) if (!seen.has(id) && (!h.length || now - h[h.length - 1].w > 1)) this.tracks.delete(id);
    this.trackList = (s.tracks || []).filter((t) => isNum(t.x));
  }

  _heat() {
    const { rows, cols } = this.grid, o = this.off;
    o.width = cols; o.height = rows;
    const c = o.getContext("2d"), img = c.createImageData(cols, rows);
    const lo = hex(cssVar("--terrain-lo")), hi = hex(cssVar("--terrain-hi"));
    const scale = Math.max(8, this.thr.red * 1.33);
    for (let k = 0; k < rows * cols; k++) {
      const t = Math.pow(clamp(this.den[k] / scale, 0, 1), 0.8);
      img.data[k * 4] = lo[0] + (hi[0] - lo[0]) * t; img.data[k * 4 + 1] = lo[1] + (hi[1] - lo[1]) * t;
      img.data[k * 4 + 2] = lo[2] + (hi[2] - lo[2]) * t; img.data[k * 4 + 3] = 255;
    }
    c.putImageData(img, 0, 0);
  }

  // floor metres -> CSS pixels (top-down, fitted with margins)
  _fit() {
    const pad = 54, sx = (this.cw - pad * 2) / this.area.w, sy = (this.ch - pad * 2) / this.area.h, s = Math.min(sx, sy);
    this.s = s; this.ox = (this.cw - this.area.w * s) / 2; this.oy = (this.ch - this.area.h * s) / 2;
  }
  project(x, y) { return this.area ? [this.ox + x * this.s, this.oy + y * this.s, 0] : null; }
  unproject(px, py) { return this.area ? [(px - this.ox) / this.s, (py - this.oy) / this.s] : null; }
  densityAt(x, y) { return this.den ? sampleGrid(this.den, this.grid.rows, this.grid.cols, this.grid.cell, x, y) : 0; }
  pick(px, py) {
    const p = this.unproject(px, py); if (!p || !this.trackList) return null;
    let best = null, bd = 0.9;
    for (const t of this.trackList) { const d = Math.hypot(t.x - p[0], t.y - p[1]); if (d < bd) { bd = d; best = t; } }
    return best;
  }
  metresPerPixel() { return this.s ? 1 / this.s : null; }
  resetView() {} focusOn() {} setDim() {} setFuture(f) { this.future = f; } refreshTheme() { if (this.den) this._heat(); }
  setLowPower(on) { this.lowPower = on; }

  frame(now) {
    const t0 = performance.now();
    if (this.lowPower && now - this.last < 1000 / 15 - 2) return;
    const dt = Math.min(0.1, (now - this.last) / 1000); this.last = now;
    const c = this.ctx, ink = cssVar("--ink"), contour = cssVar("--contour");
    c.clearRect(0, 0, this.cw, this.ch);
    if (!this.area) return;
    this._fit();
    const { s, ox, oy } = this;
    // stage grid
    c.strokeStyle = cssVar("--hair"); c.lineWidth = 1;
    c.beginPath();
    for (let x = 0; x <= this.area.w; x++) { c.moveTo(ox + x * s, oy); c.lineTo(ox + x * s, oy + this.area.h * s); }
    for (let y = 0; y <= this.area.h; y++) { c.moveTo(ox, oy + y * s); c.lineTo(ox + this.area.w * s, oy + y * s); }
    c.stroke();
    // density image (smoothed by the browser's bilinear upscale)
    c.imageSmoothingEnabled = true; c.imageSmoothingQuality = "high";
    c.globalAlpha = 0.95;
    c.drawImage(this.off, ox, oy, this.area.w * s, this.area.h * s);
    c.globalAlpha = 1;
    // contours: fine every 1 /m², index at Building and Critical
    for (const [lvl, lines] of Object.entries(this.iso || {})) {
      const idx = +lvl === this.thr.amber || +lvl === this.thr.red;
      c.strokeStyle = contour; c.globalAlpha = idx ? 1 : 0.4; c.lineWidth = idx ? 2.6 : 1;
      for (const ln of lines) { c.beginPath(); ln.points.forEach((p, i) => (i ? c.lineTo(ox + p[0] * s, oy + p[1] * s) : c.moveTo(ox + p[0] * s, oy + p[1] * s))); c.stroke(); }
    }
    c.globalAlpha = 1;
    // walls and gate
    const g = this.state.gate, lvl = this.state.status?.level;
    c.strokeStyle = ink; c.lineWidth = 3; c.globalAlpha = 0.5;
    c.strokeRect(ox, oy, this.area.w * s, this.area.h * s);
    c.globalAlpha = 1;
    if (g && isNum(g.y0)) {
      c.strokeStyle = cssVar(lvl === "RED" ? "--crit" : lvl === "AMBER" ? "--warn" : "--ok"); c.lineWidth = 7;
      const gx = ox + (g.x ?? this.area.w) * s;
      c.beginPath(); c.moveTo(gx, oy + g.y0 * s); c.lineTo(gx, oy + g.y1 * s); c.stroke();
    }
    // flow particles (CPU), static streamlines with reduced motion
    this._particles(c, dt, !reducedMotion());
    // trails and people
    const nowS = performance.now() / 1000;
    c.strokeStyle = cssVar("--person"); c.lineWidth = 1.2;
    for (const [, h] of this.tracks) {
      for (let i = 1; i < h.length; i++) { c.globalAlpha = Math.max(0, 1 - (nowS - h[i].w) / 3) * 0.5; c.beginPath(); c.moveTo(ox + h[i - 1].x * s, oy + h[i - 1].y * s); c.lineTo(ox + h[i].x * s, oy + h[i].y * s); c.stroke(); }
    }
    c.globalAlpha = 1;
    const warm = cssVar("--person-warm"), cool = cssVar("--person");
    for (const t of this.trackList || []) {
      const d = this.densityAt(t.x, t.y);
      c.fillStyle = d > this.thr.amber * 0.75 ? warm : cool;
      c.beginPath(); c.arc(ox + t.x * s, oy + t.y * s, Math.max(1.8, s * 0.11), 0, Math.PI * 2); c.fill();
    }
    // pressure rings
    const motion = !reducedMotion(), time = now / 1000;
    for (let k = 0; this.press && k < this.press.length; k++) {
      if (this.press[k] < 1 || this.den[k] < 3) continue;
      const r = Math.floor(k / this.grid.cols), col = k % this.grid.cols;
      const x = ox + (col + 0.5) * this.grid.cell * s, y = oy + (r + 0.5) * this.grid.cell * s;
      c.strokeStyle = cssVar("--terrain-hi"); c.lineWidth = 2;
      for (let j = 0; j < 2; j++) {
        const f = motion ? (time / 1.6 + j * 0.5) % 1 : 0.35 + 0.4 * j;
        c.globalAlpha = (1 - f) * 0.9; c.beginPath(); c.arc(x, y, f * 3.2 * s, 0, Math.PI * 2); c.stroke();
      }
    }
    c.globalAlpha = 1;
    this._labels();
    this.cpuMs = this.cpuMs * 0.9 + (performance.now() - t0) * 0.1;
  }

  _particles(c, dt, motion) {
    if (!this.vx) return;
    const n = this.lowPower ? 600 : 1500, { s, ox, oy } = this;
    while (this.particles.length < n) this.particles.push(this._spawn());
    c.strokeStyle = cssVar("--particle"); c.lineWidth = 1;
    for (const p of this.particles) {
      const vx = sampleGrid(this.vx, this.grid.rows, this.grid.cols, this.grid.cell, p.x, p.y);
      const vy = sampleGrid(this.vy, this.grid.rows, this.grid.cols, this.grid.cell, p.x, p.y);
      if (!isNum(vx) || !isNum(vy)) { Object.assign(p, this._spawn()); continue; }
      if (motion) { p.x += vx * dt; p.y += vy * dt; p.age += dt; }
      const len = motion ? 0.35 : 1.4;
      c.globalAlpha = 0.55 * Math.min(1, p.age * 2) * Math.max(0, 1 - p.age / p.life);
      c.beginPath(); c.moveTo(ox + p.x * s, oy + p.y * s); c.lineTo(ox + (p.x - vx * len) * s, oy + (p.y - vy * len) * s); c.stroke();
      if (p.age > p.life || p.x < 0 || p.y < 0 || p.x > this.area.w || p.y > this.area.h) Object.assign(p, this._spawn());
    }
    c.globalAlpha = 1;
  }

  _spawn() {
    let best = null, bd = -1;
    for (let k = 0; k < 4; k++) {
      const x = Math.random() * (this.area?.w || 1), y = Math.random() * (this.area?.h || 1), d = this.densityAt(x, y) + Math.random() * 0.4;
      if (d > bd) { bd = d; best = { x, y }; }
    }
    return { ...best, age: 0, life: 2 + Math.random() * 2 };
  }

  _labels() {
    const want = new Map();
    for (const lvl of [this.thr.amber, this.thr.red]) (this.anchors?.[lvl] || []).slice(0, 5).forEach((a, i) => want.set(`c${lvl}-${i}`, ["contour", `${lvl} /m²`, a]));
    const g = this.state.gate;
    if (g && isNum(g.y0)) want.set("gate", ["gate", g.label || "Gate", [Math.min(this.area.w, g.x ?? this.area.w) + 1.0, (g.y0 + g.y1) / 2]]);
    const pk = this.state.density?.peak_cell;
    if (pk && this.state.density.max >= 0.5) want.set("peak", ["peak", this.state.density.max.toFixed(1), [(pk[1] + 0.5) * this.grid.cell, (pk[0] + 0.5) * this.grid.cell - 0.8]]);
    this._els ||= new Map();
    for (const [k, [cls, text, p]] of want) {
      let el = this._els.get(k);
      if (!el) { el = document.createElement("div"); el.className = "lbl " + cls; this.labelsEl.appendChild(el); this._els.set(k, el); }
      el.textContent = text;
      if (k === "gate") el.dataset.level = this.state.status?.level || "GREEN";
      const q = this.project(p[0], p[1]);
      el.style.transform = `translate(${q[0]}px, ${q[1]}px) translate(-50%, -50%)`; el.style.left = "0"; el.style.top = "0";
    }
    for (const [k, el] of this._els) if (!want.has(k)) { el.remove(); this._els.delete(k); }
  }

  destroy() { this.ro.disconnect(); }
}

function hex(h) {
  const m = /^#?([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})/i.exec(h || "#000000");
  return m ? [parseInt(m[1], 16), parseInt(m[2], 16), parseInt(m[3], 16)] : [0, 0, 0];
}
