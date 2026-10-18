// Seismograph: a scrolling trace of the pressure index (× threshold) with the Building and
// Critical bands shaded, and peak density as a second, separate trace below. Alert ticks on
// the time axis. Never a dual axis; nulls break the line instead of being interpolated.

import { cssVar, isNum, lowerBoundW } from "./seis_util.js";

export class Seismograph {
  constructor(canvas, store) {
    this.canvas = canvas; this.store = store; this.ctx = canvas.getContext("2d");
    this.windowS = 300; this.events = [];
    this.ro = new ResizeObserver(() => this.resize()); this.ro.observe(canvas); this.resize();
    canvas.addEventListener("pointermove", (e) => { const r = canvas.getBoundingClientRect(); this.hover = e.clientX - r.left; });
    canvas.addEventListener("pointerleave", () => { this.hover = null; });
  }
  resize() {
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    this.w = this.canvas.clientWidth; this.h = this.canvas.clientHeight;
    this.canvas.width = Math.max(2, this.w * dpr); this.canvas.height = Math.max(2, this.h * dpr);
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }
  mark(w, level) { this.events.push({ w, level }); if (this.events.length > 50) this.events.shift(); }

  draw() {
    const c = this.ctx, W = this.w, H = this.h, hist = this.store.history;
    c.clearRect(0, 0, W, H);
    const end = this.store.mode === "replay" && this.store.replayAt ? this.store.replayAt : Date.now() / 1000;
    const start = end - this.windowS;
    const left = 46, right = W - 12, top = 26;
    const pH = (H - top - 10) * 0.58, dTop = top + pH + 8, dH = H - dTop - 8;
    const x = (w) => left + ((w - start) / this.windowS) * (right - left);
    const ink = cssVar("--ink"), dim = cssVar("--ink-dim"), hair = cssVar("--hair");
    const thr = this.store.view?.thresholds || {};
    // pressure bands: Building from 0.5x, Critical from 1x (the threshold)
    const pMax = 3;
    const py = (v) => top + pH - Math.min(v, pMax) / pMax * pH;
    c.fillStyle = cssVar("--warn"); c.globalAlpha = 0.07; c.fillRect(left, py(1), right - left, py(0.5) - py(1));
    c.fillStyle = cssVar("--crit"); c.globalAlpha = 0.09; c.fillRect(left, py(pMax), right - left, py(1) - py(pMax));
    c.globalAlpha = 1;
    c.strokeStyle = hair; c.lineWidth = 1;
    c.beginPath(); c.moveTo(left, py(1)); c.lineTo(right, py(1)); c.moveTo(left, top + pH); c.lineTo(right, top + pH); c.stroke();
    c.fillStyle = dim; c.font = `13px ${cssVar("--font-sign")}`; c.textAlign = "right"; c.textBaseline = "middle";
    c.fillText("1×", left - 6, py(1)); c.fillText("3×", left - 6, py(3)); c.fillText("0", left - 6, py(0));
    // density scale 0..max(8, red)
    const dMax = Math.max(8, (thr.red_density || 6) * 1.33);
    const dy = (v) => dTop + dH - Math.min(v, dMax) / dMax * dH;
    c.strokeStyle = hair; c.beginPath(); c.moveTo(left, dy(thr.red_density || 6)); c.lineTo(right, dy(thr.red_density || 6)); c.stroke();
    c.fillText(`${thr.red_density || 6}`, left - 6, dy(thr.red_density || 6)); c.fillText("0", left - 6, dy(0));
    c.textAlign = "left"; c.fillText("/m²", 4, dy(dMax * 0.5));
    // traces
    const i0 = lowerBoundW(hist, start - 2);
    const trace = (key, yf, color, width) => {
      c.strokeStyle = color; c.lineWidth = width; c.beginPath();
      let pen = false, prevW = null;
      for (let i = i0; i < hist.length; i++) {
        const h = hist[i]; if (h.w > end) break;
        const v = h[key];
        if (!isNum(v) || (prevW !== null && h.w - prevW > 3)) { pen = false; }
        if (isNum(v)) { const X = x(h.w), Y = yf(v); if (pen) c.lineTo(X, Y); else c.moveTo(X, Y); pen = true; }
        prevW = h.w;
      }
      c.stroke();
    };
    trace("pidx", py, ink, 1.6 * (parseFloat(cssVar("--line")) || 1));
    trace("dmax", dy, cssVar("--contour"), 1.4 * (parseFloat(cssVar("--line")) || 1));
    // alert ticks
    for (const a of this.store.alerts) {
      if (!isNum(a.w) || a.w < start || a.w > end) continue;
      const col = a.level === "RED" ? "--crit" : a.level === "AMBER" ? "--warn" : a.level === "CLEAR" ? "--ok" : "--ink-dim";
      c.strokeStyle = cssVar(col); c.lineWidth = 2;
      c.beginPath(); c.moveTo(x(a.w), H - 8); c.lineTo(x(a.w), H - 2); c.stroke();
    }
    // escalation event lines
    for (const e of this.events) {
      if (e.w < start) continue;
      c.strokeStyle = cssVar(e.level === "RED" ? "--crit" : "--warn"); c.lineWidth = 2; c.setLineDash([4, 4]);
      c.beginPath(); c.moveTo(x(e.w), top - 4); c.lineTo(x(e.w), H - 4); c.stroke(); c.setLineDash([]);
    }
    // hover readout
    if (this.hover !== null && this.hover !== undefined && this.hover > left) {
      const w = start + (this.hover - left) / (right - left) * this.windowS;
      const k = Math.min(hist.length - 1, lowerBoundW(hist, w));
      const h = hist[k];
      if (h && Math.abs(h.w - w) < 3) {
        c.strokeStyle = dim; c.lineWidth = 1; c.beginPath(); c.moveTo(this.hover, top); c.lineTo(this.hover, H - 6); c.stroke();
        const txt = `${new Date(h.w * 1000).toLocaleTimeString()}   pressure ${isNum(h.pidx) ? h.pidx.toFixed(2) + "×" : "—"}   density ${isNum(h.dmax) ? h.dmax.toFixed(2) : "—"} /m²`;
        c.font = `14px ${cssVar("--font-text")}`; c.fillStyle = ink; c.textAlign = this.hover > W / 2 ? "right" : "left";
        c.fillText(txt, this.hover + (this.hover > W / 2 ? -8 : 8), top + 4);
      }
    }
  }
}
