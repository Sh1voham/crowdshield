// Assessor: the JavaScript twin of crowdshield/assess.py.
// Plain float64 arithmetic in the same operation order as the Python version, so the
// two agree bit for bit (tests/test_parity.py). Fix the port, never the test.

export const LEVELS = ["GREEN", "AMBER", "RED"];
export const RANK = { GREEN: 0, AMBER: 1, RED: 2 };
export const LABEL = { GREEN: "Normal", AMBER: "Building", RED: "Critical" };
export const ACTION = { GREEN: "NONE", AMBER: "SLOW_ENTRY", RED: "HOLD_ENTRY" };
const EPS = 1e-6;

export const DEFAULT_CFG = {
  status: { amber_density: 4.0, red_density: 6.0, amber_pidx: 0.5, amber_pidx_density: 3.0,
            red_pidx: 1.0, red_pidx_density: 4.0, rise_hold_s: 2.0, fall_hold_s: 10.0 },
  turbulence: { on_pidx: 1.0, on_density: 3.0, on_hold_s: 3.0, off_pidx: 0.8, off_hold_s: 5.0 },
  forecast: { window_s: 90.0, min_samples: 20, min_span_s: 30.0, min_slope_per_s: 0.0008,
              min_level: 2.0, min_r2: 0.4, max_horizon_s: 900.0, alert_horizon_s: 300.0 },
  risk: { w_density: 0.55, w_pressure: 0.30, w_trend: 0.15, trend_ref_per_min: 1.0 },
};

export class Assessor {
  constructor(cfg = DEFAULT_CFG) {
    this.s = cfg.status; this.tb = cfg.turbulence; this.fc = cfg.forecast; this.rk = cfg.risk;
    this.reset();
  }

  reset() {
    this.status = "GREEN";
    this.statusSince = null;
    this.upSince = null; this.upLevel = 0;
    this.downSince = null; this.downLevel = 0;
    this.turbOn = false; this.turbSince = null; this.turbCand = null; this.turbOffSince = null;
    this.histT = []; this.histD = [];
    this.forecastFired = false;
  }

  classify(dmax, pidx) {
    const s = this.s;
    if (dmax >= s.red_density || (pidx >= s.red_pidx && dmax >= s.red_pidx_density)) return "RED";
    if (dmax >= s.amber_density || (pidx >= s.amber_pidx && dmax >= s.amber_pidx_density)) return "AMBER";
    return "GREEN";
  }

  cause(level, dmax) {
    if (level === "RED") return dmax >= this.s.red_density ? "DENSITY" : "PRESSURE";
    if (level === "AMBER") return dmax >= this.s.amber_density ? "DENSITY" : "PRESSURE";
    return null;
  }

  step(t, dmax, pidx) {
    const events = [];
    if (this.statusSince === null) this.statusSince = t;
    const raw = this.classify(dmax, pidx);
    const r = RANK[raw], cur = RANK[this.status];
    let newLevel = null;
    if (r > cur) {
      this.downSince = null;
      if (this.upSince === null) { this.upSince = t; this.upLevel = r; }
      else if (r < this.upLevel) this.upLevel = r;
      if (t - this.upSince >= this.s.rise_hold_s - EPS) newLevel = this.upLevel;
    } else if (r < cur) {
      this.upSince = null;
      if (this.downSince === null) { this.downSince = t; this.downLevel = r; }
      else if (r > this.downLevel) this.downLevel = r;
      if (t - this.downSince >= this.s.fall_hold_s - EPS) newLevel = this.downLevel;
    } else {
      this.upSince = null; this.downSince = null;
    }
    if (newLevel !== null) {
      this.status = LEVELS[newLevel];
      this.statusSince = t;
      this.upSince = null; this.downSince = null;
      if (this.status === "GREEN") events.push({ level: "CLEAR", kind: "RECOVERED" });
      else events.push({ level: this.status, kind: this.cause(this.status, dmax) });
    }

    const tb = this.tb;
    if (!this.turbOn) {
      if (pidx >= tb.on_pidx && dmax >= tb.on_density) {
        if (this.turbCand === null) this.turbCand = t;
        if (t - this.turbCand >= tb.on_hold_s - EPS) {
          this.turbOn = true; this.turbSince = this.turbCand; this.turbOffSince = null;
          events.push({ level: "RED", kind: "TURBULENCE" });
        }
      } else this.turbCand = null;
    } else {
      if (pidx < tb.off_pidx) {
        if (this.turbOffSince === null) this.turbOffSince = t;
        if (t - this.turbOffSince >= tb.off_hold_s - EPS) {
          this.turbOn = false; this.turbSince = null; this.turbCand = null; this.turbOffSince = null;
        }
      } else this.turbOffSince = null;
    }

    const fc = this.fc;
    this.histT.push(t); this.histD.push(dmax);
    while (this.histT.length && t - this.histT[0] > fc.window_s + EPS) { this.histT.shift(); this.histD.shift(); }
    let trend = null, tAmber = null, tRed = null, r2 = null;
    const n = this.histT.length;
    if (n >= fc.min_samples && this.histT[n - 1] - this.histT[0] >= fc.min_span_s - EPS) {
      let sx = 0.0, sy = 0.0, sxx = 0.0, sxy = 0.0;
      for (let i = 0; i < n; i++) {
        const x = this.histT[i] - t, y = this.histD[i];
        sx += x; sy += y; sxx += x * x; sxy += x * y;
      }
      const den = n * sxx - sx * sx;
      if (den > 1e-12) {
        const slope = (n * sxy - sx * sy) / den;
        const icpt = (sy - slope * sx) / n;
        const meanY = sy / n;
        let ssRes = 0.0, ssTot = 0.0;
        for (let i = 0; i < n; i++) {
          const x = this.histT[i] - t;
          const e = this.histD[i] - (icpt + slope * x);
          ssRes += e * e;
          const dy = this.histD[i] - meanY;
          ssTot += dy * dy;
        }
        r2 = ssTot > 1e-12 ? 1.0 - ssRes / ssTot : 0.0;
        trend = slope * 60.0;
        if (slope > fc.min_slope_per_s && icpt >= fc.min_level && r2 >= fc.min_r2) {
          if (icpt < this.s.amber_density) {
            tAmber = (this.s.amber_density - icpt) / slope;
            if (tAmber > fc.max_horizon_s) tAmber = null;
          }
          if (icpt < this.s.red_density) {
            tRed = (this.s.red_density - icpt) / slope;
            if (tRed > fc.max_horizon_s) tRed = null;
          }
        }
      }
    }
    if (tRed === null) this.forecastFired = false;
    else if (tRed < fc.alert_horizon_s && this.status !== "RED" && !this.forecastFired) {
      this.forecastFired = true;
      events.push({ level: "INFO", kind: "FORECAST" });
    }

    const rk = this.rk;
    let dterm = dmax / this.s.red_density; if (dterm > 1.0) dterm = 1.0;
    let pterm = pidx; if (pterm > 1.0) pterm = 1.0;
    let tterm = 0.0;
    if (trend !== null) {
      tterm = trend / rk.trend_ref_per_min;
      if (tterm < 0.0) tterm = 0.0;
      if (tterm > 1.0) tterm = 1.0;
    }
    let risk = rk.w_density * dterm + rk.w_pressure * pterm + rk.w_trend * tterm;
    if (risk < 0.0) risk = 0.0;
    if (risk > 1.0) risk = 1.0;

    return {
      status: this.status, label: LABEL[this.status], raw, status_since: this.statusSince,
      cause: this.cause(this.status, dmax), turb_on: this.turbOn, turb_since: this.turbSince,
      trend_per_min: trend, t_amber_s: tAmber, t_red_s: tRed, r2, risk,
      action: ACTION[this.status], events,
    };
  }
}
