"""Session history: full-rate states for the last 120 s, 2 Hz summaries for 60 min.

Entries are keyed by wall-clock receive time `w` (stream time `t` restarts with every
engine run). Summaries and alerts are persisted to gateway/data/session_<date>.jsonl
(appended, fsync every 5 s) so a gateway restart recovers the session.
"""
from __future__ import annotations

import json
import logging
import os
import time
from collections import deque
from pathlib import Path
from typing import List, Optional

log = logging.getLogger("gateway.history")


def summarize_state(s: dict, w: float) -> dict:
    """The fields the time river, seismograph and judge mode need (no grids, no tracks)."""
    st = s.get("status") or {}
    fc = s.get("forecast") or {}
    pr = s.get("pressure") or {}
    den = s.get("density") or {}
    return {
        "w": round(w, 3), "t": s.get("t"), "ts": s.get("ts"), "zone": s.get("zone"),
        "level": st.get("level"), "since_t": st.get("since_t"), "action": s.get("action"),
        "count": s.get("count"), "dmax": den.get("max"), "dmean": den.get("mean"), "peak_cell": den.get("peak_cell"),
        "pidx": pr.get("index"), "pmax": pr.get("max"), "speed": (s.get("speed") or {}).get("mean"),
        "turbulence": (s.get("turbulence") or {}).get("on"), "risk": s.get("risk"),
        "trend": fc.get("trend_per_min"), "t_red_s": fc.get("t_red_s"), "t_amber_s": fc.get("t_amber_s"),
        "source": (s.get("pipeline") or {}).get("source"), "fps": (s.get("pipeline") or {}).get("fps"),
    }


class History:
    def __init__(self, full_s: float = 120.0, keep_s: float = 3600.0, summary_hz: float = 2.0,
                 data_dir: Optional[Path] = None, clock=time.time):
        self.full_s = full_s
        self.keep_s = keep_s
        self.summary_period = 1.0 / summary_hz
        self.clock = clock
        self.full: deque = deque()        # (w, state)
        self.summary: deque = deque()     # summary dicts
        self.alerts: deque = deque()      # (w, alert)
        self.grids: deque = deque()       # (w, state without tracks) at 1 Hz, for rewinding the field
        self._last_grid_w: Optional[float] = None
        self._last_summary_w: Optional[float] = None
        self.data_dir = data_dir
        self._fh = None
        self._last_sync = 0.0
        if data_dir is not None:
            data_dir.mkdir(parents=True, exist_ok=True)

    # -- persistence ---------------------------------------------------------------------
    def _session_path(self) -> Optional[Path]:
        if self.data_dir is None:
            return None
        return self.data_dir / f"session_{time.strftime('%Y%m%d', time.localtime(self.clock()))}.jsonl"

    def _persist(self, rec: dict) -> None:
        p = self._session_path()
        if p is None:
            return
        if self._fh is None or self._fh.name != str(p):
            if self._fh:
                self._fh.close()
            self._fh = open(p, "a", encoding="utf-8")
        self._fh.write(json.dumps(rec, separators=(",", ":"), ensure_ascii=False) + "\n")
        now = self.clock()
        if now - self._last_sync >= 5.0:
            self.sync()

    def sync(self) -> None:
        if self._fh:
            self._fh.flush()
            try:
                os.fsync(self._fh.fileno())
            except OSError:
                pass
            self._last_sync = self.clock()

    def restore(self) -> int:
        """Load the last keep_s seconds of today's session file. Returns entries loaded."""
        p = self._session_path()
        if p is None or not p.exists():
            return 0
        cutoff = self.clock() - self.keep_s
        n = 0
        with open(p, encoding="utf-8") as f:
            for line in f:
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                w = rec.get("w", 0)
                if w < cutoff:
                    continue
                if rec.get("kind") == "summary":
                    self.summary.append(rec["data"])
                    n += 1
                elif rec.get("kind") == "alert":
                    self.alerts.append((w, rec["data"]))
                    n += 1
        if self.summary:
            self._last_summary_w = self.summary[-1]["w"]
        log.info("history: restored %d entries from %s", n, p)
        return n

    def close(self) -> None:
        self.sync()
        if self._fh:
            self._fh.close()
            self._fh = None

    # -- ingest ---------------------------------------------------------------------------
    def add_state(self, state: dict, w: Optional[float] = None) -> Optional[dict]:
        w = self.clock() if w is None else w
        self.full.append((w, state))
        while self.full and w - self.full[0][0] > self.full_s:
            self.full.popleft()
        if self._last_grid_w is None or w - self._last_grid_w >= 1.0 - 1e-6:
            self._last_grid_w = w
            self.grids.append((w, {k: v for k, v in state.items() if k != "tracks"}))
            while self.grids and w - self.grids[0][0] > self.keep_s:
                self.grids.popleft()
        summ = None
        # fixed schedule: one summary per period on average, whatever the input rate
        if self._last_summary_w is None or w >= self._last_summary_w + self.summary_period - 1e-6:
            if self._last_summary_w is None or w - self._last_summary_w > 2 * self.summary_period:
                self._last_summary_w = w
            else:
                self._last_summary_w += self.summary_period
            summ = summarize_state(state, w)
            self.summary.append(summ)
            self._persist({"kind": "summary", "w": w, "data": summ})
        while self.summary and w - self.summary[0]["w"] > self.keep_s:
            self.summary.popleft()
        return summ

    def add_alert(self, alert: dict, w: Optional[float] = None) -> None:
        w = self.clock() if w is None else w
        self.alerts.append((w, alert))
        while self.alerts and w - self.alerts[0][0] > self.keep_s:
            self.alerts.popleft()
        self._persist({"kind": "alert", "w": w, "data": alert})
        self.sync()

    # -- queries -------------------------------------------------------------------------
    def summaries(self, since: float = 0.0) -> List[dict]:
        return [s for s in self.summary if s["w"] >= since]

    def fulls(self, since: float = 0.0) -> List[dict]:
        return [dict(s, w=round(w, 3)) for w, s in self.full if w >= since]

    def alert_list(self, since: float = 0.0) -> List[dict]:
        return [dict(a, w=round(w, 3)) for w, a in self.alerts if w >= since]

    def at(self, w: float) -> Optional[dict]:
        """The state closest to wall time w: full (with tracks) inside the last full_s seconds,
        otherwise the 1 Hz grid snapshot (no tracks)."""
        best = None
        for series in (self.full, self.grids):
            if not series or w < series[0][0] - 2.0:
                continue
            lo, hi = 0, len(series)
            while lo < hi:
                mid = (lo + hi) // 2
                if series[mid][0] < w:
                    lo = mid + 1
                else:
                    hi = mid
            cands = [series[i] for i in (lo - 1, lo) if 0 <= i < len(series)]
            ww, st = min(cands, key=lambda e: abs(e[0] - w))
            if abs(ww - w) <= 2.0:
                return dict(st, w=round(ww, 3))
            if best is None or abs(ww - w) < abs(best[0] - w):
                best = (ww, st)
        return dict(best[1], w=round(best[0], 3)) if best else None

    def latest(self) -> Optional[dict]:
        return self.full[-1][1] if self.full else None
