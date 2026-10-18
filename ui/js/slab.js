// Status slab: status word + icon + time in status, advisory action, four readings,
// forecast sentence, gate sign. Greys out and says so when data is stale.

import { STATUS, SIGN_TEXT, statusIconSVG, fmt, mmss, forecastSentence, cssVar, isNum, reducedMotion } from "./util.js";
import { LedSign } from "./ledsign.js";

export class Slab {
  constructor(store) {
    this.store = store;
    this.el = document.getElementById("slab");
    this.icon = document.getElementById("status-icon");
    this.word = document.getElementById("status-word");
    this.time = document.getElementById("status-time");
    this.advice = document.getElementById("advice");
    this.forecast = document.getElementById("forecast");
    this.stale = document.getElementById("stale-band");
    this.ack = document.getElementById("sign-ack");
    this.r = { density: document.getElementById("r-density"), pressure: document.getElementById("r-pressure"),
      people: document.getElementById("r-people"), speed: document.getElementById("r-speed") };
    this.sign = new LedSign(document.getElementById("led"));
    this.level = null;
  }

  render(s, { stale = false, age = null } = {}) {
    if (!s) return;
    const lvl = s.status?.level || "GREEN", meta = STATUS[lvl];
    this.el.dataset.level = lvl;
    this.el.dataset.stale = String(stale);
    if (lvl !== this.level) {
      this.icon.innerHTML = statusIconSVG(lvl, cssVar(`--${meta.cls}`), cssVar(`--${meta.cls}-ink`) || "#000");
      this.word.textContent = meta.word;
      if (this.level !== null && !reducedMotion()) {
        this.icon.classList.remove("stamp"); void this.icon.getBBox?.(); this.icon.classList.add("stamp");
        if (lvl === "RED") { this.el.classList.remove("sweep"); void this.el.offsetWidth; this.el.classList.add("sweep"); }
      }
      this.level = lvl;
    }
    const since = isNum(s.status?.since_t) && isNum(s.t) ? s.t - s.status.since_t : null;
    this.time.textContent = stale ? "last known" : since !== null ? `for ${mmss(since)}` : "";
    this.advice.textContent = stale ? "Last known status. Live data has stopped." : meta.action;
    this.r.density.textContent = fmt(s.density?.max, 1);
    this.r.pressure.textContent = fmt(s.pressure?.index, 2);
    this.r.people.textContent = isNum(s.count) ? String(s.count) : "—";
    this.r.speed.textContent = fmt(s.speed?.mean, 2);
    this.forecast.innerHTML = stale ? "" : forecastSentence(s);
    if (stale) {
      this.stale.textContent = `No live data for ${Math.round(age ?? 0)} s. Check that PipeGen is running: System → PipeGen.`;
    }
    const action = s.action || "NONE";
    const act = s.actuator;
    this.sign.show(stale ? "NO DATA" : SIGN_TEXT[action] || "ENTRY OPEN", stale ? "--ink-faint" : `--${meta.cls}`);
    if (stale) {
      this.ack.dataset.ok = "false"; this.ack.textContent = "Sign state unknown (no live data)";
    } else if (act && isNum(act.ack_seq)) {
      const fresh = !isNum(act.age_s) || act.age_s < 15;
      this.ack.dataset.ok = String(fresh);
      this.ack.textContent = fresh ? `✓ ${act.simulated ? "Simulated sign" : `Sign ${act.device || ""}`} shows ${String(act.shown || "").replace("_", " ").toLowerCase()}` : `Sign offline for ${Math.round(act.age_s)} s`;
    } else {
      this.ack.dataset.ok = "false"; this.ack.textContent = "No sign connected";
    }
  }

  tick(now) { this.sign.tick(now); }
}
