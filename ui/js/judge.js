// Judge mode: six steps; every number shown comes from a file (named under it) or the live feed.
import { escapeHtml } from "./util.js";

const $ = (id) => document.getElementById(id);
const slides = [...document.querySelectorAll(".slide")];
let cur = 0;

function show(i) {
  cur = Math.max(0, Math.min(slides.length - 1, i));
  slides.forEach((s, k) => s.classList.toggle("on", k === cur));
  [...$("dots").children].forEach((b, k) => b.setAttribute("aria-current", k === cur ? "step" : "false"));
  if (slides[cur].dataset.title === "Live" && !$("live").src) $("live").src = "/?projector=1";
  history.replaceState(null, "", `#${cur + 1}`);
}
slides.forEach((s, k) => {
  const b = document.createElement("button");
  b.textContent = String(k + 1); b.title = s.dataset.title; b.setAttribute("aria-label", `Step ${k + 1}: ${s.dataset.title}`);
  b.addEventListener("click", () => show(k));
  $("dots").appendChild(b);
});
document.addEventListener("keydown", (e) => {
  if (e.key === "ArrowRight" || e.key === "PageDown" || e.key === " ") show(cur + 1);
  else if (e.key === "ArrowLeft" || e.key === "PageUp") show(cur - 1);
  else if (e.key.toLowerCase() === "f") document.documentElement.requestFullscreen?.();
  else if (/^[1-6]$/.test(e.key)) show(+e.key - 1);
});

function table(t, maxRows = 14) {
  return `<table class="t"><tr>${t.headers.map((h) => `<th>${escapeHtml(h)}</th>`).join("")}</tr>${t.rows.slice(0, maxRows).map((r) =>
    `<tr>${r.map((c) => `<td class="${/^[\d.\-—/ %]+$/.test(c) ? "num" : ""}">${escapeHtml(c)}</td>`).join("")}</tr>`).join("")}</table>`;
}

async function load() {
  let ev = {}, cfg = {};
  try { ev = await (await fetch("/api/evidence")).json(); } catch (e) { ev = {}; }
  try { cfg = await (await fetch("/api/config")).json(); } catch (e) { cfg = {}; }
  // 1. incidents
  const inc = ev.incidents?.tables?.[0];
  $("incidents").innerHTML = inc ? table(inc) : "<p>docs/INCIDENTS.md not found.</p>";
  $("incidents-src").textContent = inc ? `Source: ${ev.incidents.file} (figures from the team's research dossier; verify before quoting).` : "";
  // 2. pipeline
  const pl = cfg.pipeline || {}, fill = (v, what) => v ? escapeHtml(v) : `<span class="fill">[FILL: ${what} from PipeGen]</span>`;
  $("pipeline-table").innerHTML = `<tr><th>Model</th><td>${fill(pl.model, "model name")}</td></tr>
    <tr><th>Precision</th><td>${fill(pl.precision, "precision")}</td></tr>
    <tr><th>Tracker</th><td>DeepStream nvtracker IDs, used as is</td></tr>
    <tr><th>Output</th><td>Per-frame JSON (meta_json): boxes, IDs, pts_ns</td></tr>
    <tr><th>Source of these values</th><td>${escapeHtml(cfg.file || "config/default.json")} · pipeline</td></tr>`;
  // the key PipeGen evidence first: IR graph, speed report, the pipeline running, the ONNX check
  const keys = ["pipeline_ir_graph_inference_tracking", "speed_report_fp16", "pipeline_running_1", "onnx_graph_visualized"];
  const all = (ev.evidence_files || []).filter((f) => f.image);
  const imgs = keys.map((k) => all.find((f) => f.name.includes(k))).filter(Boolean).concat(all.filter((f) => !keys.some((k) => f.name.includes(k))));
  $("shots").innerHTML = imgs.length ? imgs.slice(0, 4).map((f) => `<figure><img src="${f.url}" alt="${escapeHtml(f.name)}"><figcaption>evidence/${escapeHtml(f.name)}</figcaption></figure>`).join("")
    : `<p class="fill">[FILL: add evidence/pipeline_ir.png and evidence/speed_report_fp16.png from PipeGen]</p>`;
  // 3. thresholds
  const st = cfg.status || {}, pr = cfg.pressure || {};
  $("thresholds").innerHTML = `<tr><th>Building</th><td class="num">${st.amber_density ?? "—"} /m², or pressure ≥ ${st.amber_pidx ?? "—"}× at ${st.amber_pidx_density ?? "—"} /m²</td></tr>
    <tr><th>Critical</th><td class="num">${st.red_density ?? "—"} /m², or pressure ≥ ${st.red_pidx ?? "—"}× at ${st.red_pidx_density ?? "—"} /m²</td></tr>
    <tr><th>Pressure threshold</th><td class="num">${pr.p_thr ?? "—"} s⁻²</td></tr>`;
  $("thr-src").textContent = `Source: ${cfg.file || "config/default.json"}. The pressure threshold must be calibrated on a calm recording from this camera.`;
  // 5. proof
  const parts = [];
  if (ev.engine_validation?.rows) {
    const rows = ev.engine_validation.rows;
    parts.push(`<h3>Engine vs reference engine on synthetic data (no recall correction)</h3>` + table({
      headers: ["Dataset (SYNTHETIC)", "Peak density ref", "ours", "First Critical ref (s)", "ours"],
      rows: rows.map((r) => [r.name, String(r.ref_peak_density), String(r.peak_density), String(r.ref_first_red ?? "—"), String(r.first_red ?? "—")]),
    }) + `<p class="src">Source: ${escapeHtml(ev.engine_validation.file)} (written by tools/validate_variants.py)</p>`);
  }
  for (const t of (ev.results_tables || []).slice(0, 3)) parts.push(`<h3>${escapeHtml(t.section)}</h3>${table(t)}<p class="src">Source: ${escapeHtml(ev.results_file)}</p>`);
  if (ev.validation?.tables?.length) {
    const t = ev.validation.tables[0];
    parts.push(`<h3>${escapeHtml(t.section)}</h3>${table(t, 11)}<p class="src">Source: ${escapeHtml(ev.validation.file)} (reference engine, SYNTHETIC data)</p>`);
  }
  $("proof").innerHTML = parts.join("") || "<p>No result files yet.</p>";
}

show(Math.max(0, (parseInt(location.hash.slice(1), 10) || 1) - 1));
load();
