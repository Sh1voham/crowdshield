"""Assessor: status, turbulence, forecast, risk and alert events.

This is the Python twin of `ui/js/sim/assessor.js`. Both are written with plain
float64 arithmetic in the same operation order so they agree bit for bit;
tests/test_parity.py checks that on tools/assessor_parity.jsonl. If they ever
differ, fix the port, never the test.

  pidx = pressure.max / P_thr
  raw  = RED   if dmax >= red_density   or (pidx >= red_pidx   and dmax >= red_pidx_density)
         AMBER if dmax >= amber_density or (pidx >= amber_pidx and dmax >= amber_pidx_density)
         GREEN otherwise
  A rise needs raw above the status for rise_hold_s (it rises to the lowest level seen
  in that run); a fall needs raw below it for fall_hold_s (to the highest level seen).
"""
from __future__ import annotations

from typing import List, Optional

from .config import Config, ForecastCfg, RiskCfg, StatusCfg, TurbulenceCfg

LEVELS = ("GREEN", "AMBER", "RED")
RANK = {"GREEN": 0, "AMBER": 1, "RED": 2}
LABEL = {"GREEN": "Normal", "AMBER": "Building", "RED": "Critical"}
ACTION = {"GREEN": "NONE", "AMBER": "SLOW_ENTRY", "RED": "HOLD_ENTRY"}
EPS = 1e-6


class Assessor:
    def __init__(self, status: StatusCfg, turbulence: TurbulenceCfg, forecast: ForecastCfg, risk: RiskCfg):
        self.s = status
        self.tb = turbulence
        self.fc = forecast
        self.rk = risk
        self.reset()

    @classmethod
    def from_config(cls, cfg: Config) -> "Assessor":
        return cls(cfg.status, cfg.turbulence, cfg.forecast, cfg.risk)

    def reset(self) -> None:
        self.status = "GREEN"
        self.status_since: Optional[float] = None
        self.up_since: Optional[float] = None
        self.up_level = 0
        self.down_since: Optional[float] = None
        self.down_level = 0
        self.turb_on = False
        self.turb_since: Optional[float] = None
        self.turb_cand: Optional[float] = None
        self.turb_off_since: Optional[float] = None
        self.hist_t: List[float] = []
        self.hist_d: List[float] = []
        self.forecast_fired = False

    # ------------------------------------------------------------------------------
    def classify(self, dmax: float, pidx: float) -> str:
        s = self.s
        if dmax >= s.red_density or (pidx >= s.red_pidx and dmax >= s.red_pidx_density):
            return "RED"
        if dmax >= s.amber_density or (pidx >= s.amber_pidx and dmax >= s.amber_pidx_density):
            return "AMBER"
        return "GREEN"

    def cause(self, level: str, dmax: float) -> Optional[str]:
        if level == "RED":
            return "DENSITY" if dmax >= self.s.red_density else "PRESSURE"
        if level == "AMBER":
            return "DENSITY" if dmax >= self.s.amber_density else "PRESSURE"
        return None

    def step(self, t: float, dmax: float, pidx: float) -> dict:
        events: List[dict] = []
        if self.status_since is None:
            self.status_since = t
        raw = self.classify(dmax, pidx)
        r, cur = RANK[raw], RANK[self.status]
        new_level: Optional[int] = None
        if r > cur:
            self.down_since = None
            if self.up_since is None:
                self.up_since = t
                self.up_level = r
            elif r < self.up_level:
                self.up_level = r
            if t - self.up_since >= self.s.rise_hold_s - EPS:
                new_level = self.up_level
        elif r < cur:
            self.up_since = None
            if self.down_since is None:
                self.down_since = t
                self.down_level = r
            elif r > self.down_level:
                self.down_level = r
            if t - self.down_since >= self.s.fall_hold_s - EPS:
                new_level = self.down_level
        else:
            self.up_since = None
            self.down_since = None
        if new_level is not None:
            self.status = LEVELS[new_level]
            self.status_since = t
            self.up_since = None
            self.down_since = None
            if self.status == "GREEN":
                events.append({"level": "CLEAR", "kind": "RECOVERED"})
            else:
                events.append({"level": self.status, "kind": self.cause(self.status, dmax)})

        # turbulence
        tb = self.tb
        if not self.turb_on:
            if pidx >= tb.on_pidx and dmax >= tb.on_density:
                if self.turb_cand is None:
                    self.turb_cand = t
                if t - self.turb_cand >= tb.on_hold_s - EPS:
                    self.turb_on = True
                    self.turb_since = self.turb_cand
                    self.turb_off_since = None
                    events.append({"level": "RED", "kind": "TURBULENCE"})
            else:
                self.turb_cand = None
        else:
            if pidx < tb.off_pidx:
                if self.turb_off_since is None:
                    self.turb_off_since = t
                if t - self.turb_off_since >= tb.off_hold_s - EPS:
                    self.turb_on = False
                    self.turb_since = None
                    self.turb_cand = None
                    self.turb_off_since = None
            else:
                self.turb_off_since = None

        # forecast: least squares on (t, dmax) over the window
        fc = self.fc
        self.hist_t.append(t)
        self.hist_d.append(dmax)
        while self.hist_t and t - self.hist_t[0] > fc.window_s + EPS:
            self.hist_t.pop(0)
            self.hist_d.pop(0)
        trend = None
        t_amber = None
        t_red = None
        r2 = None
        n = len(self.hist_t)
        if n >= fc.min_samples and self.hist_t[n - 1] - self.hist_t[0] >= fc.min_span_s - EPS:
            sx = 0.0
            sy = 0.0
            sxx = 0.0
            sxy = 0.0
            for i in range(n):
                x = self.hist_t[i] - t
                y = self.hist_d[i]
                sx += x
                sy += y
                sxx += x * x
                sxy += x * y
            den = n * sxx - sx * sx
            if den > 1e-12:
                slope = (n * sxy - sx * sy) / den
                icpt = (sy - slope * sx) / n
                mean_y = sy / n
                ss_res = 0.0
                ss_tot = 0.0
                for i in range(n):
                    x = self.hist_t[i] - t
                    e = self.hist_d[i] - (icpt + slope * x)
                    ss_res += e * e
                    dy = self.hist_d[i] - mean_y
                    ss_tot += dy * dy
                r2 = 1.0 - ss_res / ss_tot if ss_tot > 1e-12 else 0.0
                trend = slope * 60.0
                if slope > fc.min_slope_per_s and icpt >= fc.min_level and r2 >= fc.min_r2:
                    if icpt < self.s.amber_density:
                        t_amber = (self.s.amber_density - icpt) / slope
                        if t_amber > fc.max_horizon_s:
                            t_amber = None
                    if icpt < self.s.red_density:
                        t_red = (self.s.red_density - icpt) / slope
                        if t_red > fc.max_horizon_s:
                            t_red = None
        if t_red is None:
            self.forecast_fired = False
        elif t_red < fc.alert_horizon_s and self.status != "RED" and not self.forecast_fired:
            self.forecast_fired = True
            events.append({"level": "INFO", "kind": "FORECAST"})

        # risk
        rk = self.rk
        dterm = dmax / self.s.red_density
        if dterm > 1.0:
            dterm = 1.0
        pterm = pidx
        if pterm > 1.0:
            pterm = 1.0
        tterm = 0.0
        if trend is not None:
            tterm = trend / rk.trend_ref_per_min
            if tterm < 0.0:
                tterm = 0.0
            if tterm > 1.0:
                tterm = 1.0
        risk = rk.w_density * dterm + rk.w_pressure * pterm + rk.w_trend * tterm
        if risk < 0.0:
            risk = 0.0
        if risk > 1.0:
            risk = 1.0

        return {
            "status": self.status,
            "label": LABEL[self.status],
            "raw": raw,
            "status_since": self.status_since,
            "cause": self.cause(self.status, dmax),
            "turb_on": self.turb_on,
            "turb_since": self.turb_since,
            "trend_per_min": trend,
            "t_amber_s": t_amber,
            "t_red_s": t_red,
            "r2": r2,
            "risk": risk,
            "action": ACTION[self.status],
            "events": events,
        }
