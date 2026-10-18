"""Per-track velocities from frame timestamps only.

v = (p(t) - p(t - D)) / D with D >= min_dt_s taken from the track's own history, then
an exponential moving average with time constant ema_tau_s. Tracks younger than
min_age_s have no velocity (they still count for density). Between min_age_s and
min_dt_s the oldest available sample is used.
"""
from __future__ import annotations

import math
from collections import deque
from typing import Dict, Iterable, Optional, Tuple


class _Track:
    __slots__ = ("hist", "v", "t_v", "t_first", "t_last")

    def __init__(self, t: float):
        self.hist: deque = deque()
        self.v: Optional[Tuple[float, float]] = None
        self.t_v: Optional[float] = None
        self.t_first = t
        self.t_last = t


class Kinematics:
    def __init__(self, min_dt_s: float = 1.0, ema_tau_s: float = 1.0, min_age_s: float = 0.5,
                 history_s: float = 3.0, max_age_s: float = 1.0):
        self.min_dt = min_dt_s
        self.tau = ema_tau_s
        self.min_age = min_age_s
        self.history = max(history_s, min_dt_s + 0.5)
        self.max_age = max_age_s
        self.tracks: Dict[int, _Track] = {}

    def update(self, t: float, ids: Iterable[int], xy) -> list:
        """Feed this frame's tracked points. Returns a velocity (vx, vy) or None per point."""
        out = []
        for tid, (x, y) in zip(ids, xy):
            tid = int(tid)
            tr = self.tracks.get(tid)
            if tr is None:
                tr = self.tracks[tid] = _Track(t)
            tr.t_last = t
            h = tr.hist
            h.append((t, float(x), float(y)))
            while h and t - h[0][0] > self.history:
                h.popleft()
            out.append(self._velocity(tr, t, float(x), float(y)))
        # forget tracks not seen for a while (their IDs may be reused upstream)
        stale = [k for k, tr in self.tracks.items() if t - tr.t_last > self.max_age]
        for k in stale:
            del self.tracks[k]
        return out

    def _velocity(self, tr: _Track, t: float, x: float, y: float) -> Optional[Tuple[float, float]]:
        age = t - tr.t_first
        if age < self.min_age:
            return None
        ref = None
        for (tr_t, rx, ry) in reversed(tr.hist):  # newest sample at least min_dt old
            if t - tr_t >= self.min_dt:
                ref = (tr_t, rx, ry)
                break
        if ref is None:
            ref = tr.hist[0]
        dt = t - ref[0]
        if dt <= 1e-9:
            return tr.v
        raw = ((x - ref[1]) / dt, (y - ref[2]) / dt)
        if tr.v is None or tr.t_v is None:
            tr.v = raw
        else:
            step = t - tr.t_v
            a = 1.0 - math.exp(-step / self.tau) if self.tau > 0 else 1.0
            tr.v = (tr.v[0] + a * (raw[0] - tr.v[0]), tr.v[1] + a * (raw[1] - tr.v[1]))
        tr.t_v = t
        return tr.v
