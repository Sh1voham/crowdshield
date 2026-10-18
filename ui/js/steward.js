// Steward phone page: one huge status, the advisory, and an arrow toward the gate.
// Vibration and a short tone on escalation, after one tap to enable (browser rule).

import { Store } from "./store.js";
import { startSource, sourceFromURL } from "./source.js";
import { STATUS, SOURCE_LABEL, statusIconSVG, cssVar, mmss, fmt, isNum, sourceKind } from "./util.js";

const $ = (id) => document.getElementById(id);
const store = new Store();
let me = null, lastLevel = null, alertsOn = false, audio = null;
try { me = JSON.parse(localStorage.getItem("cs.steward.pos") || "null"); } catch (e) { me = null; }

$("alerts-on").addEventListener("click", () => {
  alertsOn = true;
  try { audio = new (window.AudioContext || window.webkitAudioContext)(); } catch (e) { audio = null; }
  navigator.vibrate?.(60);
  $("alerts-on").textContent = "Vibration and sound on";
  $("alerts-on").disabled = true;
});

function tone(freq, ms) {
  if (!audio) return;
  const o = audio.createOscillator(), g = audio.createGain();
  o.frequency.value = freq; o.type = "square"; g.gain.value = 0.08;
  o.connect(g); g.connect(audio.destination); o.start(); o.stop(audio.currentTime + ms / 1000);
}

function render() {
  const s = store.live;
  if (!s) return;
  const age = store.dataAge(), stale = age !== null && age > 3;
  const lvl = s.status?.level || "GREEN", meta = STATUS[lvl];
  const big = $("big");
  big.dataset.level = lvl; big.dataset.stale = String(stale);
  $("icon").innerHTML = statusIconSVG(lvl, cssVar(`--${meta.cls}`), "#000");
  $("word").textContent = meta.word;
  $("act").textContent = stale ? "Last known status — live data has stopped." : meta.action;
  $("since").textContent = stale ? "last known" : isNum(s.status?.since_t) ? `for ${mmss(s.t - s.status.since_t)}` : "";
  $("stale").textContent = stale ? `No live data for ${Math.round(age)} s. Tell the control room.` : "";
  $("zone").textContent = s.zone || "Zone";
  const kind = store.sourceName === "sim" ? "sim" : sourceKind(s);
  const src = $("src"); src.hidden = false; src.dataset.kind = kind; src.textContent = SOURCE_LABEL[kind] || "REPLAY";
  $("d").textContent = fmt(s.density?.max, 1); $("p").textContent = fmt(s.pressure?.index, 1); $("n").textContent = s.count ?? "—";
  if (lastLevel && lvl !== lastLevel && STATUS[lvl] && ["GREEN", "AMBER", "RED"].indexOf(lvl) > ["GREEN", "AMBER", "RED"].indexOf(lastLevel) && alertsOn && !stale) {
    navigator.vibrate?.(lvl === "RED" ? [300, 120, 300, 120, 600] : [250, 150, 250]);
    tone(lvl === "RED" ? 880 : 660, 350);
  }
  lastLevel = lvl;
  drawMap(s);
  arrow(s);
}

function fit(s, c) { const pad = 16, k = Math.min((c.width - pad * 2) / s.area.width_m, (c.height - pad * 2) / s.area.height_m); return { k, ox: (c.width - s.area.width_m * k) / 2, oy: (c.height - s.area.height_m * k) / 2 }; }

function drawMap(s) {
  const c = $("mini"), x = c.getContext("2d");
  x.clearRect(0, 0, c.width, c.height);
  if (!s.area) return;
  const { k, ox, oy } = fit(s, c), g = s.grid;
  if (g?.density) {
    const max = Math.max(6, s.thresholds?.red_density || 6);
    for (let r = 0; r < g.rows; r++) for (let q = 0; q < g.cols; q++) {
      const d = g.density[r][q] || 0; if (d < 0.3) continue;
      x.fillStyle = cssVar("--terrain-hi"); x.globalAlpha = Math.min(0.85, d / max);
      x.fillRect(ox + q * g.cell_m * k, oy + r * g.cell_m * k, g.cell_m * k + 0.5, g.cell_m * k + 0.5);
    }
    x.globalAlpha = 1;
  }
  x.strokeStyle = cssVar("--ink"); x.lineWidth = 2; x.strokeRect(ox, oy, s.area.width_m * k, s.area.height_m * k);
  const gt = s.gate;
  if (gt && isNum(gt.y0)) {
    const lvl = s.status?.level; x.strokeStyle = cssVar(lvl === "RED" ? "--crit" : lvl === "AMBER" ? "--warn" : "--ok"); x.lineWidth = 8;
    const gx = ox + (gt.x ?? s.area.width_m) * k; x.beginPath(); x.moveTo(gx, oy + gt.y0 * k); x.lineTo(gx, oy + gt.y1 * k); x.stroke();
  }
  if (me) { x.fillStyle = cssVar("--focus"); x.beginPath(); x.arc(ox + me[0] * k, oy + me[1] * k, 9, 0, Math.PI * 2); x.fill(); }
}

function arrow(s) {
  const gt = s.gate;
  if (!me || !gt || !isNum(gt.y0)) return;
  const gx = gt.x ?? s.area.width_m, gy = (gt.y0 + gt.y1) / 2;
  const dx = gx - me[0], dy = gy - me[1], dist = Math.hypot(dx, dy);
  // map "up" = toward y = 0 on the floor plan; the arrow shows the direction on the map
  const deg = Math.atan2(dx, -dy) * 180 / Math.PI;
  $("arrow-path").setAttribute("transform", `rotate(${deg.toFixed(0)} 32 32)`);
  $("exit-text").innerHTML = `${gt.label || "Gate"}: <b class="num">${dist.toFixed(0)} m</b> that way on the map.`;
}

$("mini").addEventListener("click", (e) => {
  const s = store.live; if (!s?.area) return;
  const c = $("mini"), r = c.getBoundingClientRect(), sx = (e.clientX - r.left) * (c.width / r.width), sy = (e.clientY - r.top) * (c.height / r.height);
  const { k, ox, oy } = fit(s, c);
  me = [Math.max(0, Math.min(s.area.width_m, (sx - ox) / k)), Math.max(0, Math.min(s.area.height_m, (sy - oy) / k))];
  try { localStorage.setItem("cs.steward.pos", JSON.stringify(me)); } catch (e) { /* private mode */ }
  render();
});

store.on("live", render);
store.on("conn", ({ state }) => { if (state !== "up" && !store.live) $("zone").textContent = state === "down" ? "Reconnecting…" : "Connecting…"; });
setInterval(render, 1000);
startSource(store, sourceFromURL());
