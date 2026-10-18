// Time river: the whole session as a flowing band (height = people, colour = status).
// Drag to replay; incident markers jump to that incident. Arrow keys move the cursor.

import { cssVar, isNum, lowerBoundW } from "./seis_util.js";

export class River {
  constructor(canvas, store, { onScrub, onRelease, onIncident } = {}) {
    this.canvas = canvas; this.store = store; this.ctx = canvas.getContext("2d");
    this.onScrub = onScrub; this.onRelease = onRelease; this.onIncident = onIncident;
    this.ro = new ResizeObserver(() => this.resize()); this.ro.observe(canvas); this.resize();
    let dragging = false;
    const at = (e) => { const r = canvas.getBoundingClientRect(); return this.wAt(e.clientX - r.left); };
    canvas.addEventListener("pointerdown", (e) => {
      const r = canvas.getBoundingClientRect(), px = e.clientX - r.left;
      const inc = this.incidentAt(px);
      if (inc && this.onIncident) { this.onIncident(inc); return; }
      dragging = true; canvas.setPointerCapture(e.pointerId); this.onScrub?.(at(e));
    });
    canvas.addEventListener("pointermove", (e) => { if (dragging) this.onScrub?.(at(e)); });
    canvas.addEventListener("pointerup", (e) => { if (dragging) { dragging = false; this.onRelease?.(at(e)); } });
    canvas.addEventListener("keydown", (e) => {
      if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
      e.preventDefault();
      const cur = this.store.replayAt ?? Date.now() / 1000;
      const w = cur + (e.key === "ArrowLeft" ? -5 : 5);
      if (w >= Date.now() / 1000) this.onRelease?.(null); else this.onScrub?.(w);
    });
  }
  resize() {
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    this.w = this.canvas.clientWidth; this.h = this.canvas.clientHeight;
    this.canvas.width = Math.max(2, this.w * dpr); this.canvas.height = Math.max(2, this.h * dpr);
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }
  span() {
    const hist = this.store.history, now = Date.now() / 1000;
    const start = hist.length ? Math.min(hist[0].w, now - 120) : now - 120;
    return [start, now];
  }
  wAt(px) { const [a, b] = this.span(), L = 16, R = this.w - 8; return a + Math.max(0, Math.min(1, (px - L) / (R - L))) * (b - a); }
  xAt(w) { const [a, b] = this.span(), L = 16, R = this.w - 8; return L + (w - a) / (b - a) * (R - L); }
  incidentAt(px) {
    for (const inc of this.store.incidents || []) if (Math.abs(this.xAt(inc.opened_w) - px) < 8) return inc;
    return null;
  }

  draw() {
    const c = this.ctx, W = this.w, H = this.h, hist = this.store.history;
    c.clearRect(0, 0, W, H);
    const [a, b] = this.span(), mid = H / 2;
    const maxN = Math.max(20, ...hist.map((h) => h.count || 0));
    const col = { GREEN: cssVar("--ok"), AMBER: cssVar("--warn"), RED: cssVar("--crit") };
    // band: one column per pixel, coloured by the worst status in it
    const L = 16, R = W - 8;
    let k = 0;
    for (let px = L; px < R; px += 2) {
      const w0 = a + (px - L) / (R - L) * (b - a), w1 = a + (px + 2 - L) / (R - L) * (b - a);
      k = lowerBoundW(hist, w0);
      let n = 0, lvl = null, any = false, notLive = false;
      for (let i = k; i < hist.length && hist[i].w < w1; i++) {
        any = true; n = Math.max(n, hist[i].count || 0);
        if (hist[i].source && hist[i].source !== "live" && hist[i].source !== "video") notLive = true;
        const l = hist[i].level; if (l === "RED" || (l === "AMBER" && lvl !== "RED") || !lvl) lvl = l;
      }
      if (!any) continue;
      const hh = 4 + (n / maxN) * (H / 2 - 10);
      // simulated, synthetic and replayed stretches are drawn faded so they never pass for live data
      c.fillStyle = col[lvl] || cssVar("--ink-faint"); c.globalAlpha = notLive ? 0.3 : 0.85;
      c.fillRect(px, mid - hh, 2, hh * 2);
      if (notLive && px % 8 === 0) { c.globalAlpha = 0.7; c.fillRect(px, mid - hh, 1, hh * 2); }
    }
    c.globalAlpha = 1;
    // incident markers
    for (const inc of this.store.incidents || []) {
      const x = this.xAt(inc.opened_w);
      if (x < L || x > R) continue;
      c.fillStyle = cssVar(inc.peak_level === "RED" ? "--crit" : "--warn");
      c.beginPath(); c.moveTo(x, 3); c.lineTo(x + 6, 13); c.lineTo(x - 6, 13); c.closePath(); c.fill();
      c.strokeStyle = cssVar("--stage"); c.lineWidth = 1.5; c.stroke();
    }
    // cursor
    const cw = this.store.mode === "replay" && this.store.replayAt ? this.store.replayAt : b;
    const cx = this.xAt(cw);
    c.strokeStyle = cssVar("--ink"); c.lineWidth = 2;
    c.beginPath(); c.moveTo(cx, 2); c.lineTo(cx, H - 2); c.stroke();
    c.fillStyle = cssVar("--ink-dim"); c.font = `13px ${cssVar("--font-text")}`; c.textBaseline = "bottom"; c.textAlign = "left";
    if (hist.length) c.fillText(new Date(a * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }), L, H - 2);
    c.textAlign = "right";
    c.fillText(this.store.mode === "replay" ? `replay ${new Date(cw * 1000).toLocaleTimeString()}` : "now", R, H - 2);
  }
}
