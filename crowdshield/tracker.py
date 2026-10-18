"""Fallback nearest-neighbour tracker in floor coordinates.

Used ONLY when PipeGen's output carries no track IDs. PipeGen's nvtracker IDs are
always preferred. Greedy association with constant-velocity prediction.
"""
from __future__ import annotations

import numpy as np


class NNTracker:
    def __init__(self, gate_m: float = 1.0, max_age_s: float = 1.0, first_id: int = 1):
        self.gate = gate_m
        self.max_age = max_age_s
        self.next_id = first_id
        self.ids = np.zeros(0, dtype=np.int64)
        self.pos = np.zeros((0, 2))
        self.vel = np.zeros((0, 2))
        self.t_last = np.zeros(0)

    def update(self, t: float, xy: np.ndarray) -> np.ndarray:
        """Associate floor points (N, 2) at time t; returns an ID per point."""
        xy = np.asarray(xy, dtype=float).reshape(-1, 2)
        alive = (t - self.t_last) <= self.max_age
        self.ids, self.pos, self.vel, self.t_last = (self.ids[alive], self.pos[alive],
                                                     self.vel[alive], self.t_last[alive])
        n_det, n_trk = len(xy), len(self.ids)
        out = np.full(n_det, -1, dtype=np.int64)
        if n_trk and n_det:
            dt = (t - self.t_last)[:, None]
            pred = self.pos + self.vel * dt[:, 0:1]
            d = np.sqrt(((pred[:, None, :] - xy[None, :, :]) ** 2).sum(axis=2))  # (trk, det)
            cand = np.argwhere(d <= self.gate)
            if len(cand):
                order = np.argsort(d[cand[:, 0], cand[:, 1]], kind="stable")
                used_t = np.zeros(n_trk, dtype=bool)
                used_d = np.zeros(n_det, dtype=bool)
                for k in order:
                    ti, di = cand[k]
                    if used_t[ti] or used_d[di]:
                        continue
                    used_t[ti] = used_d[di] = True
                    out[di] = self.ids[ti]
                    step = t - self.t_last[ti]
                    if step > 1e-6:
                        v_new = (xy[di] - self.pos[ti]) / step
                        self.vel[ti] = 0.5 * self.vel[ti] + 0.5 * v_new
                    self.pos[ti] = xy[di]
                    self.t_last[ti] = t
        new = out < 0
        n_new = int(new.sum())
        if n_new:
            new_ids = np.arange(self.next_id, self.next_id + n_new, dtype=np.int64)
            self.next_id += n_new
            out[new] = new_ids
            self.ids = np.r_[self.ids, new_ids]
            self.pos = np.r_[self.pos, xy[new]]
            self.vel = np.r_[self.vel, np.zeros((n_new, 2))]
            self.t_last = np.r_[self.t_last, np.full(n_new, t)]
        return out
