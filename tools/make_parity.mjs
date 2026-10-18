// Records the browser simulator's Assessor at 5 Hz into tools/assessor_parity.jsonl
// (exact inputs and outputs), and writes tools/sample_stream.jsonl: contract-shaped
// SIMULATED messages at 1 Hz for smoke tests and gateway replays.
//
//   node tools/make_parity.mjs            # all scenarios
//   node tools/make_parity.mjs --summary  # print the status timeline only

import { writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { Sim } from "../ui/js/sim/sim.js";

const here = dirname(fileURLToPath(import.meta.url));
const summaryOnly = process.argv.includes("--summary");
const parity = [];
const stream = [];
let runIdx = 0;

for (const [name, seed] of [["calm", 3], ["building", 7], ["surge", 11], ["relief", 5]]) {
  const sim = new Sim(name, { seed });
  runIdx++;
  let last = null, transitions = [], peak = 0, peakP = 0, nextStream = 0;
  while (!sim.done()) {
    const st = sim.step();
    if (!st) continue;
    const a = st._raw.assess;
    parity.push({
      run: runIdx, scenario: name, t: st.t, dmax: st._raw.dmax, pidx: st._raw.pidx, status: a.status,
      turb_on: a.turb_on, trend_per_min: a.trend_per_min, t_amber_s: a.t_amber_s, t_red_s: a.t_red_s,
      risk: a.risk, events: a.events.map((e) => `${e.level}:${e.kind}`),
    });
    peak = Math.max(peak, st._raw.dmax); peakP = Math.max(peakP, st._raw.pidx);
    if (a.status !== last) { transitions.push(`${st.t.toFixed(1)}s ${a.status}`); last = a.status; }
    if (st.t + 1e-9 >= nextStream && name !== "relief") {
      nextStream += 1.0;
      const { _events, _raw, ...msg } = st;
      msg.zone = `Gate A (simulated ${name})`;
      msg.ts = new Date(Date.UTC(2026, 9, 18, 10, 0, 0) + st.t * 1000).toISOString();
      stream.push({ topic: "crowdshield/state", payload: msg });
      for (const e of _events) {
        stream.push({ topic: "crowdshield/alerts", payload: { v: 1, type: "alert", id: `sim-${stream.length}`, t: st.t,
          ts: msg.ts, zone: msg.zone, level: e.level, kind: e.kind, status: a.status, action: a.action,
          message: `${e.level} ${e.kind} (simulated)`, metrics: { dmax: msg.density.max, pidx: msg.pressure.index,
          count: msg.count, risk: msg.risk }, cell: msg.density.peak_cell } });
      }
    }
  }
  console.log(`${name.padEnd(9)} people end ${sim.people.length.toString().padStart(3)}  peak density ${peak.toFixed(2)}  peak pidx ${peakP.toFixed(2)}  ${transitions.join(" -> ")}`);
}

if (!summaryOnly) {
  writeFileSync(join(here, "assessor_parity.jsonl"), parity.map((r) => JSON.stringify(r)).join("\n") + "\n");
  writeFileSync(join(here, "sample_stream.jsonl"), stream.map((r) => JSON.stringify(r)).join("\n") + "\n");
  console.log(`wrote ${parity.length} parity rows and ${stream.length} sample-stream messages (SIMULATED)`);
}
