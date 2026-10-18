// Small shared helpers: formatting, status vocabulary, colour tokens.

export const clamp = (x, a, b) => (x < a ? a : x > b ? b : x);
export const lerp = (a, b, t) => a + (b - a) * t;
export const easeInOut = (t) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2);
export const isNum = (v) => typeof v === "number" && Number.isFinite(v);

export const STATUS = {
  GREEN: { word: "Normal", action: "Normal flow. No action needed.", cls: "ok", sign: "ENTRY OPEN" },
  AMBER: { word: "Building", action: "Slow entry. Send a steward to the peak area.", cls: "warn", sign: "SLOW ENTRY" },
  RED: { word: "Critical", action: "Hold entry. Open all exits. Alert supervisors.", cls: "crit", sign: "HOLD ENTRY" },
};
export const SIGN_TEXT = { NONE: "ENTRY OPEN", SLOW_ENTRY: "SLOW ENTRY", HOLD_ENTRY: "HOLD ENTRY" };
export const SOURCE_LABEL = { live: "LIVE", video: "VIDEO via PipeGen", replay: "REPLAY", synthetic: "SYNTHETIC", sim: "SIMULATED" };

// Status icons: circle with a tick, triangle, octagon. Shape always accompanies colour.
export function statusIconSVG(level, color = "currentColor", ink = "#000") {
  if (level === "RED") {
    const pts = [];
    for (let i = 0; i < 8; i++) { const a = (Math.PI / 8) + i * Math.PI / 4; pts.push(`${32 + 29 * Math.cos(a)},${32 + 29 * Math.sin(a)}`); }
    return `<polygon points="${pts.join(" ")}" fill="${color}"/><rect x="18" y="28.5" width="28" height="7" rx="1.5" fill="${ink}"/>`;
  }
  if (level === "AMBER") {
    return `<path d="M32 5 L60 56 H4 Z" fill="${color}" stroke="${color}" stroke-width="3" stroke-linejoin="round"/><rect x="29" y="22" width="6" height="18" rx="2" fill="${ink}"/><circle cx="32" cy="47" r="3.4" fill="${ink}"/>`;
  }
  if (level === "GREEN") {
    return `<circle cx="32" cy="32" r="28" fill="${color}"/><path d="M19 33 l9 9 l18 -19" fill="none" stroke="${ink}" stroke-width="6" stroke-linecap="round" stroke-linejoin="round"/>`;
  }
  return `<circle cx="32" cy="32" r="27" fill="none" stroke="${color}" stroke-width="3" stroke-dasharray="6 6"/>`;
}

export function fmt(v, nd = 1, dash = "—") {
  return isNum(v) ? v.toFixed(nd) : dash;
}

export function mmss(sec) {
  if (!isNum(sec) || sec < 0) return "—";
  sec = Math.floor(sec);
  const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
  return h ? `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}` : `${m}:${String(s).padStart(2, "0")}`;
}

export function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

export function cssVar(name, el = document.documentElement) {
  return getComputedStyle(el).getPropertyValue(name).trim();
}

export function hexToRgb01(hex) {
  const m = /^#?([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})/i.exec(hex || "");
  if (!m) return [1, 1, 1];
  return [parseInt(m[1], 16) / 255, parseInt(m[2], 16) / 255, parseInt(m[3], 16) / 255];
}

export function tokens() {
  const g = (n) => hexToRgb01(cssVar(n));
  return {
    stage: g("--stage"), stageLift: g("--stage-lift"), stageDeep: g("--stage-deep"), ink: g("--ink"),
    contour: g("--contour"), terrainLo: g("--terrain-lo"), terrainHi: g("--terrain-hi"), particle: g("--particle"),
    person: g("--person"), personWarm: g("--person-warm"), ok: g("--ok"), warn: g("--warn"), crit: g("--crit"),
    glow: parseFloat(cssVar("--glow") || "1"), night: document.documentElement.dataset.theme !== "day",
    line: parseFloat(cssVar("--line")) || 1,
  };
}

export function statusColorVar(level) {
  return level === "RED" ? "var(--crit)" : level === "AMBER" ? "var(--warn)" : level === "GREEN" ? "var(--ok)" : "var(--ink-faint)";
}

export function reducedMotion() {
  return document.documentElement.dataset.motion === "reduced" ||
    (document.documentElement.dataset.motion !== "full" && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches);
}

export function sourceKind(state) {
  const src = state?.pipeline?.source;
  return SOURCE_LABEL[src] ? src : src ? "replay" : "none";
}

// "Critical in 1:40 if the trend continues"
export function forecastSentence(state) {
  const f = state?.forecast || {}, lvl = state?.status?.level;
  if (lvl === "RED") return "Critical now.";
  if (isNum(f.t_red_s)) return `Critical in <strong>${mmss(f.t_red_s)}</strong> if the trend continues.`;
  if (isNum(f.t_amber_s) && lvl !== "AMBER") return `Building in <strong>${mmss(f.t_amber_s)}</strong> if the trend continues.`;
  if (isNum(f.trend_per_min) && Math.abs(f.trend_per_min) >= 0.05)
    return `Peak density ${f.trend_per_min > 0 ? "rising" : "falling"} <strong>${Math.abs(f.trend_per_min).toFixed(2)}</strong> /m² per minute.`;
  return isNum(f.trend_per_min) ? "Steady: no trend toward Critical." : "";
}
