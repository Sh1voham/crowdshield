// CrowdShield Command: wires the data store to the field, slab, seismograph and river,
// and choreographs the signature moments (escalation, forecast horizon, reality lock, rewind).

import { Store } from "./store.js";
import { startSource, sourceFromURL, refreshIncidents } from "./source.js";
import { FieldGL } from "./field/gl.js";
import { Field2D } from "./field/canvas2d.js";
import { Slab } from "./slab.js";
import { Seismograph } from "./seismograph.js";
import { River } from "./river.js";
import { RealityLock } from "./reality.js";
import { STATUS, SOURCE_LABEL, fmt, mmss, escapeHtml, isNum, reducedMotion, sourceKind, statusIconSVG, cssVar } from "./util.js";

const $ = (id) => document.getElementById(id);
const root = document.documentElement;
const q = new URLSearchParams(location.search);
const store = new Store();
const pref = (k, d) => { try { return localStorage.getItem("cs." + k) ?? d; } catch (e) { return d; } };
const setPref = (k, v) => { try { localStorage.setItem("cs." + k, v); } catch (e) { /* private mode */ } };

// ------------------------------------------------------------------ settings
const themeQ = q.get("theme");
root.dataset.theme = themeQ === "light" || themeQ === "day" ? "day" : themeQ === "dark" || themeQ === "night" ? "night" : pref("theme", "night");
root.dataset.projector = q.get("projector") === "1" ? "on" : pref("projector", "off");
if (pref("motion", "") === "reduced") root.dataset.motion = "reduced";
let lowPower = q.get("lowpower") === "1" || pref("lowpower", "0") === "1";

// ------------------------------------------------------------------ views
const fieldEl = $("field"), canvas = $("field-canvas"), labelsEl = $("labels");
let field;
const want2d = q.get("renderer") === "2d";
if (!want2d && FieldGL.supported()) {
  try { field = new FieldGL(canvas, labelsEl); } catch (e) { console.warn("WebGL2 field failed; using Canvas 2D", e); }
}
if (!field) {
  const c2 = document.createElement("canvas"); c2.className = "c2d"; c2.setAttribute("aria-hidden", "true");
  canvas.replaceWith(c2);
  field = new Field2D(c2, labelsEl);
}
field.setLowPower?.(lowPower);
const slab = new Slab(store);
const seismo = new Seismograph($("seismo-canvas"), store);
const reality = new RealityLock($("reality"));
const river = new River($("river-canvas"), store, {
  onScrub: (w) => replay.scrub(w),
  onRelease: (w) => (w === null ? replay.live() : replay.release(w)),
  onIncident: (inc) => replay.incident(inc.id),
});
const announcer = $("announcer");

window.__cs = { store, field, startLive: () => replay.live(), route: (h) => (location.hash = h) };

// ------------------------------------------------------------------ data -> views
let lastLevel = null, lastAnnounced = null, escalationTimer = null;
store.on("view", (s) => render(s));
store.on("live", (s) => onLive(s));
store.on("alert", (a) => { renderAlerts(); if (store.mode === "live" && (a.level === "RED" || a.level === "AMBER")) seismo.mark(a.w ?? Date.now() / 1000, a.level); });
store.on("conn", ({ state }) => { const el = $("conn"); el.dataset.state = state; el.textContent = state === "up" ? connName() : state === "down" ? "Reconnecting" : "Connecting"; });
store.on("health", () => { if ($("sheet-system").classList.contains("on")) renderSystem(); });
store.on("incidents", () => { if ($("sheet-incidents").classList.contains("on")) renderIncidents(); });

function connName() { return store.sourceName === "sim" ? "Built-in simulator" : store.sourceName === "mqtt" ? "MQTT" : "Gateway"; }

function render(s) {
  if (!s) return;
  field.setState(s);
  reality.setState(s);
  const stale = store.mode === "live" && isStale();
  slab.render(s, { stale, age: store.dataAge() });
  header(s);
  updateLegend();
  if ($("sheet-readout").classList.contains("on")) renderReadout();
}

function onLive(s) {
  if (s.camera) cameraViewHint(s.camera);
  const lvl = s.status?.level;
  if (lvl && lvl !== lastAnnounced) {
    announcer.textContent = `Status ${STATUS[lvl].word} at ${s.zone || "the zone"}. ${STATUS[lvl].action}`;
    lastAnnounced = lvl;
  }
  if (store.mode === "live") {
    // moment 1, escalation: rising to Critical frames the epicentre once
    if (lastLevel && lastLevel !== "RED" && lvl === "RED") escalate(s);
    // moment 2, forecast horizon: a ghost of the projected surface while Critical is < 5 min away
    const f = s.forecast || {};
    if (lvl !== "RED" && isNum(f.t_red_s) && f.t_red_s < 300 && isNum(f.trend_per_min) && f.trend_per_min > 0) {
      field.setFuture({ add: (f.trend_per_min / 60) * f.t_red_s, dmax: s.density?.max || 1, label: `Critical in ${mmss(f.t_red_s)} if the trend continues` });
    } else field.setFuture(null);
  }
  lastLevel = lvl;
}

function escalate(s) {
  const pk = s.density?.peak_cell, cell = s.grid?.cell_m || 1;
  const x = Array.isArray(pk) ? (pk[1] + 0.5) * cell : s.area.width_m / 2, y = Array.isArray(pk) ? (pk[0] + 0.5) * cell : s.area.height_m / 2;
  seismo.mark(Date.now() / 1000, "RED");
  if (reducedMotion()) return;
  field.focusOn(x, y, 1200);
  field.setDim(true, x, y);
  clearTimeout(escalationTimer);
  escalationTimer = setTimeout(() => { field.setDim(false); if (!field.camUser) field.resetView("smooth"); }, 5200);
}

function isStale() { const age = store.dataAge(); return age !== null && age > 3; }

function header(s) {
  $("zone").textContent = s.zone || "Zone";
  const kind = store.sourceName === "sim" ? "sim" : sourceKind(s);
  const lbl = $("source-label");
  const demo = s.thresholds?.profile === "demo";
  const text = (SOURCE_LABEL[kind] || "REPLAY") + (demo ? " · DEMO PROFILE" : "");
  if (store.mode === "replay") { lbl.dataset.kind = "replay"; lbl.textContent = "REPLAY"; }
  else { lbl.dataset.kind = kind; lbl.textContent = text; }
  lbl.hidden = false;
}

function vitals() {
  const s = store.live, age = store.dataAge(), v = $("vitals");
  if (!s) { v.innerHTML = ""; return; }
  const fps = s.pipeline?.fps, lat = s.pipeline?.latency_ms;
  const parts = [`data <b>${age === null ? "—" : age.toFixed(1)}</b> s`, `pipeline <b>${fmt(fps, 0)}</b> fps`];
  if (isNum(lat)) parts.push(`latency <b>${fmt(lat, 0)}</b> ms`);
  if (isNum(fps) && fps < 10 && store.sourceName !== "sim") parts.push(`<span class="warn-chip">Pipeline below 10 fps</span>`);
  v.innerHTML = parts.map((p) => `<span>${p}</span>`).join("");
}

function updateLegend() {
  const mpp = field.metresPerPixel?.();
  if (!mpp) return;
  const px = 5 / mpp;
  const target = px > 160 ? 2 : px < 40 ? 10 : 5;
  $("scale-bar").style.width = `${Math.round(target / mpp)}px`;
  $("scale-text").textContent = `${target} m`;
  $("legend-height").textContent = field.kind === "webgl2" ? "(height × 0.22 m per /m²)" : "";
}

// ------------------------------------------------------------------ replay / rewind
const banner = $("banner");
const replay = {
  timer: null, seq: 0,
  async stateAt(w) {
    const buf = store.fullBuf;
    if (buf.length && w >= buf[0].w - 1) {
      let best = buf[0];
      for (const e of buf) if (Math.abs(e.w - w) < Math.abs(best.w - w)) best = e;
      return best.s;
    }
    if (store.sourceName === "gateway") {
      try { const r = await fetch(`/api/history?at=${w}`); if (r.ok) return (await r.json()).state; } catch (e) { /* offline */ }
    }
    return null;
  },
  async scrub(w) {
    this.stop();
    const my = ++this.seq;
    const st = await this.stateAt(w);
    if (my !== this.seq) return;
    store.enterReplay(w, st || undefined);
    if (!st) store.replayFrame(w, store.view);
    banner.textContent = `REPLAY ${new Date(w * 1000).toLocaleTimeString()}${st ? "" : "  (no field data stored for this moment)"}`;
    banner.classList.add("on");
    $("btn-live").setAttribute("aria-pressed", "false");
  },
  release(w) {
    const inc = (store.incidents || []).find((i) => Math.abs(i.opened_w - w) < 15);
    if (inc) this.incident(inc.id);
  },
  play(from, rate = 1) {
    this.stop();
    let w = from;
    const step = async () => {
      if (w >= Date.now() / 1000 - 0.5) { this.live(); return; }
      await this.scrub(w);
      w += 0.25 * rate;
      this.timer = setTimeout(step, 250);
    };
    step();
  },
  async incident(id) {
    this.stop();
    let inc;
    try { inc = await (await fetch(`/api/incidents/${encodeURIComponent(id)}`)).json(); } catch (e) { return; }
    const esc = (inc.escalations || []).slice(-1)[0];
    const snaps = esc?.snapshots || [];
    if (!snaps.length) { this.scrub(inc.opened_w); return; }
    const tmpl = store.live || store.view || {};
    let i = 0;
    store.enterReplay(snaps[0].w);
    const tick = () => {
      const sn = snaps[i];
      const st = { ...tmpl, t: sn.t, grid: sn.grid, tracks: sn.tracks || [], count: sn.count,
        status: { ...(tmpl.status || {}), level: sn.level, since_t: null }, density: { ...(tmpl.density || {}), max: sn.dmax },
        pressure: { ...(tmpl.pressure || {}), index: sn.pidx }, forecast: {}, pipeline: { ...(tmpl.pipeline || {}), source: "replay" } };
      store.replayFrame(sn.w, st);
      banner.textContent = `REPLAY incident ${inc.peak_level === "RED" ? "Critical" : "Building"} · 60 s before escalation · 4×  ${new Date(sn.w * 1000).toLocaleTimeString()}`;
      banner.classList.add("on");
      i++;
      if (i < snaps.length) this.timer = setTimeout(tick, 500);
    };
    tick();
    $("btn-live").setAttribute("aria-pressed", "false");
  },
  stop() { clearTimeout(this.timer); this.timer = null; },
  live() { this.stop(); this.seq++; banner.classList.remove("on"); store.goLive(); $("btn-live").setAttribute("aria-pressed", "true"); },
};
$("btn-live").addEventListener("click", () => replay.live());

// ------------------------------------------------------------------ sheets
function openSheet(id) {
  for (const el of document.querySelectorAll(".sheet")) if (el.id !== id) el.classList.remove("on");
  const el = $(id); el.classList.toggle("on");
  if (el.classList.contains("on")) {
    if (id === "sheet-readout") renderReadout();
    if (id === "sheet-incidents") { refreshIncidents(store); renderIncidents(); }
    if (id === "sheet-system") renderSystem();
    el.querySelector("[data-close]")?.focus();
  }
}
for (const b of document.querySelectorAll("[data-close]")) b.addEventListener("click", () => b.closest(".sheet").classList.remove("on"));
$("btn-readout").addEventListener("click", () => openSheet("sheet-readout"));
$("btn-incidents").addEventListener("click", () => openSheet("sheet-incidents"));
$("btn-system").addEventListener("click", () => openSheet("sheet-system"));
$("btn-help").addEventListener("click", () => openSheet("sheet-help"));

function row(k, v) { return `<tr><th>${escapeHtml(k)}</th><td class="n">${v}</td></tr>`; }
function renderReadout() {
  const s = store.view;
  if (!s) { $("readout-body").textContent = "No data yet."; return; }
  const f = s.forecast || {};
  $("readout-body").innerHTML = `<table>
    ${row("Status", `${escapeHtml(STATUS[s.status?.level]?.word || "—")}`)}
    ${row("Advisory", escapeHtml(STATUS[s.status?.level]?.action || "—"))}
    ${row("Peak density", `${fmt(s.density?.max, 2)} people/m² (measured ${fmt(s.density?.measured_max, 2)})`)}
    ${row("Mean density", `${fmt(s.density?.mean, 2)} people/m²`)}
    ${row("Crowd pressure", `${fmt(s.pressure?.index, 2)} × threshold (${fmt(s.pressure?.max, 4)} s⁻², threshold ${fmt(s.pressure?.threshold, 3)})`)}
    ${row("Turbulence", s.turbulence?.on ? "Yes" : "No")}
    ${row("People in view", isNum(s.count) ? s.count : "—")}
    ${row("Mean speed", `${fmt(s.speed?.mean, 2)} m/s`)}
    ${row("Trend", `${fmt(f.trend_per_min, 2)} /m² per min`)}
    ${row("Forecast Critical in", isNum(f.t_red_s) ? mmss(f.t_red_s) : "—")}
    ${row("Risk", fmt(s.risk, 2))}
    ${row("Source", escapeHtml(s.pipeline?.source || "—"))}
    ${row("Pipeline", `${fmt(s.pipeline?.fps, 1)} fps · model ${escapeHtml(s.pipeline?.model || "—")} · ${escapeHtml(s.pipeline?.precision || "—")}`)}
    ${row("Stream time", `${fmt(s.t, 1)} s`)}
  </table>`;
}

function renderIncidents() {
  const list = store.incidents || [];
  $("incidents-body").innerHTML = list.length ? list.map((i) => `<div class="inc-item">
      <svg width="28" height="28" viewBox="0 0 64 64" aria-hidden="true">${statusIconSVG(i.peak_level, cssVar(i.peak_level === "RED" ? "--crit" : "--warn"), "#000")}</svg>
      <div><b>${escapeHtml(STATUS[i.peak_level]?.word || i.peak_level)}</b> · peak ${fmt(i.peak_dmax, 1)} /m² · pressure ${fmt(i.peak_pidx, 1)}×${i.turbulence ? " · turbulence" : ""}<br>
      <span style="color:var(--ink-dim)">${new Date(i.opened_w * 1000).toLocaleTimeString()}${i.closed_w ? "–" + new Date(i.closed_w * 1000).toLocaleTimeString() : " (open)"} · ${escapeHtml(i.zone || "")}</span></div>
      <button class="btn" data-replay="${escapeHtml(i.id)}">Replay</button></div>`).join("")
    : `<p style="color:var(--ink-dim)">No incidents in this session. An incident opens when the status leaves Normal.</p>`;
  for (const b of document.querySelectorAll("[data-replay]")) b.addEventListener("click", () => { replay.incident(b.dataset.replay); $("sheet-incidents").classList.remove("on"); });
  renderAlerts();
}

function renderAlerts() {
  const el = $("alerts-body");
  if (!el || !$("sheet-incidents").classList.contains("on")) return;
  const list = store.alerts.slice(-40).reverse();
  el.innerHTML = list.length ? list.map((a) => `<div class="alert-row" data-ack="${store.ack.has(a.id)}">
      <svg width="20" height="20" viewBox="0 0 64 64" aria-hidden="true">${statusIconSVG(a.level === "CLEAR" ? "GREEN" : a.level === "INFO" ? "" : a.level, cssVar(a.level === "RED" ? "--crit" : a.level === "AMBER" ? "--warn" : a.level === "CLEAR" ? "--ok" : "--ink-dim"), "#000")}</svg>
      <div>${escapeHtml(a.message || `${a.level} ${a.kind}`)}<br><span style="color:var(--ink-dim)">${a.w ? new Date(a.w * 1000).toLocaleTimeString() : ""} · ${escapeHtml(a.kind || "")}</span></div>
      <button class="btn" data-ack-id="${escapeHtml(a.id)}" ${store.ack.has(a.id) ? "disabled" : ""}>${store.ack.has(a.id) ? "Acknowledged" : "Acknowledge"}</button></div>`).join("")
    : `<p style="color:var(--ink-dim)">No alerts yet.</p>`;
  for (const b of el.querySelectorAll("[data-ack-id]")) b.addEventListener("click", () => { store.ack.add(b.dataset.ackId); renderAlerts(); });
}

async function renderSystem() {
  const h = store.health || {};
  const gpu = h.gpu;
  let ctl = null;
  if (store.sourceName === "gateway") { try { ctl = await (await fetch("/api/control")).json(); } catch (e) { ctl = null; } }
  let recs = [];
  if (store.sourceName === "gateway") { try { recs = (await (await fetch("/api/recordings")).json()).items || []; } catch (e) { recs = []; } }
  const ages = Object.entries(h.data_age_s || {}).map(([k, v]) => `${k} ${fmt(v, 1)} s`).join(", ") || "—";
  const eng = h.engine || {}, pg = h.pipegen || {};
  $("system-body").innerHTML = `<table>
      ${row("Connection", escapeHtml(connName() + " · " + store.conn))}
      ${row("MQTT", escapeHtml(h.mqtt || "—"))}
      ${row("Data age", escapeHtml(ages))}
      ${row("Engine", eng.running ? `running (pid ${eng.pid ?? "?"}${eng.profile ? ", " + escapeHtml(eng.profile) : ""}) · ${fmt(eng.fps, 1)} fps · tracker ${escapeHtml(eng.tracker || "—")} · input ${escapeHtml(eng.layout || "—")}` : "not running")}
      ${row("PipeGen pipeline", pg.running ? `running · model ${escapeHtml(pg.model || "—")} · ${escapeHtml(pg.precision || "—")}` : "not running")}
      ${row("GPU", gpu ? `${escapeHtml(gpu.name || "")} · ${fmt(gpu.util, 0)}% · ${fmt(gpu.mem_used, 0)}/${fmt(gpu.mem_total, 0)} MiB · ${fmt(gpu.temp, 0)} °C · ${fmt(gpu.power_w, 1)} W` : "not reported")}
      ${row("Invalid messages", h.invalid_messages ?? "—")}
      ${row("Field renderer", `${field.kind} · CPU ${fmt(field.cpuMs, 2)} ms/frame · GPU ${isNum(field.gpuMs) ? fmt(field.gpuMs, 2) + " ms" : "not measured"}${lowPower ? " · low power" : ""}`)}
    </table>
    <h3>Control</h3>
    ${ctl ? `<p style="color:var(--ink-dim)">Commands are fixed in gateway/config.json. Other devices need the PIN printed in the gateway terminal.</p>
      <div class="btnrow">
        <button class="btn" data-act="engine.start" data-profile="video">Start engine on PipeGen video</button>
        <button class="btn" data-act="engine.start" data-profile="vms">Start engine on PipeGen VMS stream</button>
        <button class="btn" data-act="engine.start" data-profile="demo">Start engine, demo profile</button>
        <button class="btn danger" data-act="engine.stop">Stop engine</button>
      </div>
      <div class="btnrow">
        <button class="btn" data-act="pipegen.run-video">Start PipeGen pipeline</button>
        <button class="btn" data-act="pipegen.run-vms">Start PipeGen (VMS mode)</button>
        <button class="btn danger" data-act="pipegen.stop">Stop PipeGen pipeline</button>
      </div>
      <h3>Replays</h3>
      <div class="btnrow">${recs.map((r) => `<button class="btn" data-act="engine.start" data-profile="${r.kind === "synthetic" ? "synthetic" : "replay"}" data-rec="${escapeHtml(r.name)}">${escapeHtml(r.label)} ${escapeHtml(r.name)}</button>`).join("") || "<span style='color:var(--ink-dim)'>No recordings yet.</span>"}</div>
      <h3>Exact commands</h3>
      <table>${Object.entries(ctl.actions || {}).map(([k, v]) => `<tr><th>${escapeHtml(k)}</th><td><code>${escapeHtml(v.join(" "))}</code></td></tr>`).join("")}</table>
      <p id="ctl-result" role="status"></p>`
    : `<p style="color:var(--ink-dim)">Process control needs the gateway (open this page from http://&lt;laptop&gt;:8080/).</p>`}`;
  for (const b of document.querySelectorAll("[data-act]")) b.addEventListener("click", async () => {
    const body = { action: b.dataset.act };
    if (b.dataset.profile) body.profile = b.dataset.profile;
    if (b.dataset.rec) body.recording = b.dataset.rec;
    const pin = sessionStorage.getItem?.("cs.pin");
    const r = await fetch("/api/control", { method: "POST", headers: { "Content-Type": "application/json", ...(pin ? { "X-CS-PIN": pin } : {}) }, body: JSON.stringify(body) });
    const j = await r.json().catch(() => ({}));
    if (r.status === 401) { const p = prompt("Control PIN (printed in the gateway terminal):"); if (p) { sessionStorage.setItem("cs.pin", p); } }
    $("ctl-result").textContent = r.ok ? `Done: ${body.action}${j.pid ? " (pid " + j.pid + ")" : ""}` : `Refused: ${j.error || r.status}`;
  });
}

// ------------------------------------------------------------------ toggles + keyboard
function setTheme(t) { root.dataset.theme = t; setPref("theme", t); field.refreshTheme?.(); slab.level = null; if (store.view) render(store.view); $("btn-theme").textContent = t === "day" ? "Night" : "Day"; }
function setProjector(on) { root.dataset.projector = on ? "on" : "off"; setPref("projector", on ? "on" : "off"); $("btn-projector").setAttribute("aria-pressed", String(on)); field.refreshTheme?.(); }
function toggleReality() {
  const on = !reality.on;
  const src = store.view?.pipeline?.source;
  if (on && store.sourceName !== "gateway" || on && src && !["live", "video"].includes(src)) {
    banner.textContent = "Reality lock needs live or PipeGen video data: the camera image would not match this data.";
    banner.classList.add("on"); setTimeout(() => { if (store.mode === "live") banner.classList.remove("on"); }, 3500);
    return;
  }
  if (on) reality.enable(); else reality.disable();
  $("btn-reality").setAttribute("aria-pressed", String(on));
  if (on && store.view) reality.setState(store.view);
}
$("btn-theme").addEventListener("click", () => setTheme(root.dataset.theme === "day" ? "night" : "day"));
$("btn-theme").textContent = root.dataset.theme === "day" ? "Night" : "Day";
$("btn-projector").addEventListener("click", () => setProjector(root.dataset.projector !== "on"));
$("btn-projector").setAttribute("aria-pressed", String(root.dataset.projector === "on"));
$("btn-reality").addEventListener("click", toggleReality);
$("btn-reset").addEventListener("click", () => field.resetView("smooth"));

document.addEventListener("keydown", (e) => {
  if (e.target.matches("input, textarea, select") || e.ctrlKey || e.metaKey || e.altKey) return;
  const k = e.key.toLowerCase();
  if (k === "escape") document.querySelectorAll(".sheet.on").forEach((s) => s.classList.remove("on"));
  else if (k === "1") document.querySelectorAll(".sheet.on").forEach((s) => s.classList.remove("on"));
  else if (k === "2" || k === "i") openSheet("sheet-incidents");
  else if (k === "3" || k === "s") openSheet("sheet-system");
  else if (k === "4") location.href = "/calibrate";
  else if (k === "5") location.href = "/judge";
  else if (k === "l") { if (store.mode === "replay") replay.live(); else replay.play(Date.now() / 1000 - 60, 1); }
  else if (k === "t") setTheme(root.dataset.theme === "day" ? "night" : "day");
  else if (k === "p") setProjector(root.dataset.projector !== "on");
  else if (k === "r") toggleReality();
  else if (k === "m") { const red = root.dataset.motion === "reduced"; root.dataset.motion = red ? "full" : "reduced"; setPref("motion", red ? "" : "reduced"); }
  else if (k === "e") { lowPower = !lowPower; setPref("lowpower", lowPower ? "1" : "0"); field.setLowPower?.(lowPower); }
  else if (e.key === "?") openSheet("sheet-help");
});
if (location.hash === "#system") openSheet("sheet-system");
if (location.hash.startsWith("#incident/")) setTimeout(() => replay.incident(decodeURIComponent(location.hash.slice(10))), 1500);
window.addEventListener("hashchange", () => { if (location.hash === "#system") openSheet("sheet-system"); else if (location.hash.startsWith("#incident/")) replay.incident(decodeURIComponent(location.hash.slice(10))); });

// tooltip on hover: track ID and speed
const tip = $("tip");
fieldEl.addEventListener("pointermove", (e) => {
  const r = fieldEl.getBoundingClientRect(), px = e.clientX - r.left, py = e.clientY - r.top;
  const t = field.pick?.(px, py);
  field.selected = t;
  if (t) {
    const sp = isNum(t.vx) ? Math.hypot(t.vx, t.vy) : null;
    tip.innerHTML = `Track <b class="num">${escapeHtml(t.id ?? "—")}</b> · ${sp === null ? "speed —" : `<span class="num">${sp.toFixed(2)}</span> m/s`} · local ${fmt(field.densityAt?.(t.x, t.y), 1)} /m²`;
    tip.style.display = "block"; tip.style.left = `${px + 14}px`; tip.style.top = `${py + 10}px`;
  } else tip.style.display = "none";
});
fieldEl.addEventListener("pointerleave", () => { tip.style.display = "none"; field.selected = null; });

// ------------------------------------------------------------------ main loop
let lastSlow = 0, wasStale = false;
function loop(now) {
  try { field.frame(now); } catch (e) { console.error(e); }
  slab.tick(now);
  if (now - lastSlow > 100) {
    lastSlow = now;
    seismo.draw(); river.draw(); vitals();
    const stale = store.mode === "live" && isStale();
    if (stale !== wasStale || stale) { if (store.view) slab.render(store.view, { stale, age: store.dataAge() }); wasStale = stale; }
    if (reality.on) reality.draw();
  }
  requestAnimationFrame(loop);
}
requestAnimationFrame(loop);

// align the 3D view with the real camera: the floor direction toward the bottom of the image
let hintKey = null;
async function cameraViewHint(cam) {
  try {
    const c = cam || (store.sourceName === "gateway" ? (await (await fetch("/api/config")).json()).calibration : null);
    const key = JSON.stringify(c?.image_points);
    if (key === hintKey) return;
    hintKey = key;
    if (!c?.image_points) return;
    const { homography, applyH } = await import("./field/math.js");
    const H = homography(c.image_points, c.world_points), [fw, fh] = c.frame_size || [1920, 1080];
    const a = applyH(H, fw / 2, fh * 0.5), b = applyH(H, fw / 2, fh * 0.95);
    const dx = b[0] - a[0], dy = b[1] - a[1];
    if (Number.isFinite(dx) && Number.isFinite(dy) && Math.hypot(dx, dy) > 1e-6) field.setViewHint?.(Math.atan2(dx, dy));
  } catch (e) { /* no calibration yet */ }
}

const src = startSource(store, sourceFromURL());
setTimeout(cameraViewHint, 300);
window.__cs.source = src;
window.__cs.startSim = (sc) => src.restart?.(sc);
