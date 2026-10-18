"""Group alerts into incidents for the incident list and the incident replay.

An incident opens when the status leaves GREEN (or turbulence starts) and closes
after 60 s of GREEN. It keeps the summary series from 120 s before opening until
closing, and grid snapshots (every 2 s) of the 60 s before each escalation.
"""
from __future__ import annotations

import time
from collections import deque
from typing import Dict, List, Optional

RANK = {"GREEN": 0, "AMBER": 1, "RED": 2}


def grid_snapshot(state: dict, w: float) -> dict:
    g = state.get("grid") or {}
    return {"w": round(w, 3), "t": state.get("t"), "level": (state.get("status") or {}).get("level"),
            "dmax": (state.get("density") or {}).get("max"), "pidx": (state.get("pressure") or {}).get("index"),
            "count": state.get("count"),
            "grid": {k: g.get(k) for k in ("cell_m", "rows", "cols", "density", "pressure", "vx", "vy")},
            "tracks": (state.get("tracks") or [])[:400]}


class Incidents:
    def __init__(self, close_after_s: float = 60.0, pre_s: float = 120.0, snap_every_s: float = 2.0,
                 snap_window_s: float = 60.0, clock=time.time):
        self.close_after = close_after_s
        self.pre_s = pre_s
        self.snap_every = snap_every_s
        self.snap_window = snap_window_s
        self.clock = clock
        self.items: List[dict] = []
        self.open: Optional[dict] = None
        self.recent_summaries: deque = deque()
        self.recent_snaps: deque = deque()
        self._last_snap_w: Optional[float] = None
        self._green_since: Optional[float] = None
        self._last_level = "GREEN"
        self._seq = 0
        self.listeners = []

    def _emit(self, kind: str, inc: dict) -> None:
        for fn in self.listeners:
            fn(kind, inc)

    def on_state(self, state: dict, summary: Optional[dict], w: Optional[float] = None) -> None:
        w = self.clock() if w is None else w
        level = (state.get("status") or {}).get("level") or "GREEN"
        turb = bool((state.get("turbulence") or {}).get("on"))
        if summary is not None:
            self.recent_summaries.append(summary)
            while self.recent_summaries and w - self.recent_summaries[0]["w"] > self.pre_s:
                self.recent_summaries.popleft()
            if self.open is not None:
                self.open["series"].append(summary)
        if self._last_snap_w is None or w - self._last_snap_w >= self.snap_every - 1e-6:
            self._last_snap_w = w
            self.recent_snaps.append(grid_snapshot(state, w))
            while self.recent_snaps and w - self.recent_snaps[0]["w"] > self.snap_window:
                self.recent_snaps.popleft()

        if self.open is None and (level != "GREEN" or turb):
            self._open(w, state, level)
        elif self.open is not None:
            inc = self.open
            if RANK.get(level, 0) > RANK.get(self._last_level, 0):
                self._escalation(w, level)
            if RANK.get(level, 0) > RANK.get(inc["peak_level"], 0):
                inc["peak_level"] = level
            den = (state.get("density") or {}).get("max") or 0
            pidx = (state.get("pressure") or {}).get("index") or 0
            inc["peak_dmax"] = max(inc["peak_dmax"], den)
            inc["peak_pidx"] = max(inc["peak_pidx"], pidx)
            if turb:
                inc["turbulence"] = True
            if level == "GREEN" and not turb:
                if self._green_since is None:
                    self._green_since = w
                elif w - self._green_since >= self.close_after:
                    self._close(w)
            else:
                self._green_since = None
        self._last_level = level

    def _open(self, w: float, state: dict, level: str) -> None:
        self._seq += 1
        den = (state.get("density") or {}).get("max") or 0
        pidx = (state.get("pressure") or {}).get("index") or 0
        self.open = {
            "id": f"inc-{int(w)}-{self._seq}", "opened_w": round(w, 3), "closed_w": None,
            "zone": state.get("zone"), "source": (state.get("pipeline") or {}).get("source"),
            "peak_level": level, "peak_dmax": den, "peak_pidx": pidx,
            "turbulence": bool((state.get("turbulence") or {}).get("on")),
            "alerts": [], "series": list(self.recent_summaries), "escalations": [],
        }
        self._green_since = None
        self.items.append(self.open)
        if level != "GREEN":
            self._escalation(w, level)
        self._emit("opened", self.summary_of(self.open))

    def _escalation(self, w: float, level: str) -> None:
        if self.open is None:
            return
        self.open["escalations"].append({"w": round(w, 3), "level": level, "snapshots": list(self.recent_snaps)})
        self._emit("escalated", self.summary_of(self.open))

    def _close(self, w: float) -> None:
        inc = self.open
        inc["closed_w"] = round(w, 3)
        self.open = None
        self._green_since = None
        self._emit("closed", self.summary_of(inc))

    def on_alert(self, alert: dict, w: Optional[float] = None) -> None:
        w = self.clock() if w is None else w
        if self.open is None and alert.get("level") in ("AMBER", "RED") and alert.get("kind") != "PIPELINE":
            # the alert arrived before the state that carries the new level
            self._open(w, {"zone": alert.get("zone"), "status": {"level": alert.get("status") or alert["level"]},
                           "density": {"max": (alert.get("metrics") or {}).get("dmax")},
                           "pressure": {"index": (alert.get("metrics") or {}).get("pidx")}},
                       alert.get("status") or alert["level"])
        if self.open is not None:
            self.open["alerts"].append(dict(alert, w=round(w, 3)))

    @staticmethod
    def summary_of(inc: dict) -> dict:
        return {k: inc[k] for k in ("id", "opened_w", "closed_w", "zone", "source", "peak_level", "peak_dmax",
                                    "peak_pidx", "turbulence")} | {
            "alerts": len(inc["alerts"]), "escalations": [{"w": e["w"], "level": e["level"]} for e in inc["escalations"]]}

    def list(self) -> List[dict]:
        return [self.summary_of(i) for i in reversed(self.items)]

    def get(self, inc_id: str) -> Optional[Dict]:
        for i in self.items:
            if i["id"] == inc_id:
                return i
        return None
