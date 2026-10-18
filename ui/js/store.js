// The store: every view renders from here. Live vs replay is a store mode.

export class Store {
  constructor() {
    this.live = null;            // latest live state message
    this.view = null;            // state being displayed (live, or the replay cursor)
    this.mode = "live";          // live | replay
    this.replayAt = null;        // wall time of the replay cursor
    this.lastLiveWall = null;    // performance-independent wall clock (Date.now()/1000) of the last state
    this.history = [];           // summaries {w, t, level, dmax, pidx, count, ...}
    this.alerts = [];            // alert messages (with w)
    this.incidents = [];
    this.health = null;
    this.status = null;
    this.conn = "connecting";
    this.sourceName = "gateway";
    this.ack = new Set();        // acknowledged alert ids (local only; never changes the backend)
    this.fullBuf = [];           // last 120 s of full states {w, s} for local rewinds
    this._ls = {};
  }

  on(evt, fn) { (this._ls[evt] ||= []).push(fn); return () => { this._ls[evt] = this._ls[evt].filter((f) => f !== fn); }; }
  emit(evt, data) { for (const fn of this._ls[evt] || []) { try { fn(data); } catch (e) { console.error(evt, e); } } }

  setConn(state, detail = "") { this.conn = state; this.emit("conn", { state, detail }); }

  pushState(s) {
    const w = Date.now() / 1000;
    this.live = s;
    this.lastLiveWall = w;
    this.fullBuf.push({ w, s });
    while (this.fullBuf.length && w - this.fullBuf[0].w > 120) this.fullBuf.shift();
    const prev = this.history.length ? this.history[this.history.length - 1] : null;
    if (!prev || w - prev.w >= 0.5) {
      this.history.push(summarize(s, w));
      if (this.history.length > 7200) this.history.splice(0, this.history.length - 7200);
    }
    if (this.mode === "live") { this.view = s; this.emit("view", s); }
    this.emit("live", s);
  }

  pushAlert(a) {
    const rec = { ...a, w: a.w ?? Date.now() / 1000 };
    this.alerts.push(rec);
    if (this.alerts.length > 500) this.alerts.shift();
    this.emit("alert", rec);
  }

  setHistory(items, alerts) {
    if (Array.isArray(items) && items.length) {
      const known = this.history.length ? this.history[0].w : Infinity;
      const older = items.filter((x) => x.w < known);
      this.history = older.concat(this.history);
    }
    if (Array.isArray(alerts)) {
      const ids = new Set(this.alerts.map((a) => a.id));
      for (const a of alerts) if (!ids.has(a.id)) this.alerts.push(a);
      this.alerts.sort((a, b) => (a.w ?? 0) - (b.w ?? 0));
    }
    this.emit("history", this.history);
  }

  setHealth(h) { this.health = h; this.emit("health", h); }
  setIncidents(list) { this.incidents = list; this.emit("incidents", list); }

  enterReplay(w, state) {
    this.mode = "replay"; this.replayAt = w;
    if (state) { this.view = state; this.emit("view", state); }
    this.emit("mode", this.mode);
  }
  replayFrame(w, state) { this.replayAt = w; this.view = state; this.emit("view", state); }
  goLive() {
    this.mode = "live"; this.replayAt = null;
    if (this.live) { this.view = this.live; this.emit("view", this.live); }
    this.emit("mode", this.mode);
  }

  dataAge() { return this.lastLiveWall == null ? null : Date.now() / 1000 - this.lastLiveWall; }
}

export function summarize(s, w) {
  return {
    w, t: s.t, zone: s.zone, level: s.status?.level, dmax: s.density?.max, pidx: s.pressure?.index,
    count: s.count, risk: s.risk, speed: s.speed?.mean, turbulence: s.turbulence?.on, trend: s.forecast?.trend_per_min,
    t_red_s: s.forecast?.t_red_s, source: s.pipeline?.source, peak_cell: s.density?.peak_cell,
  };
}
