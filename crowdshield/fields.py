"""Density, velocity, speed and pressure fields on a metre grid (vectorised numpy).

  rho(r)   = 1/(pi R^2) * sum_j exp(-|r_j - r|^2 / R^2)          Johansson et al. 2008, Eq. 3
  V(r)     = sum_j w_j v_j / sum_j w_j     (people with a velocity; None if sum w < 1)
  speed(r) = sum_j w_j |v_j| / sum_j w_j   (None if sum w < 0.3)
  P(r)     = rho(r) * Var_t(V(r))          over the last 8 s, >= 15 valid samples, rho >= gate

Recall correction: rho_corr = rho_meas / recall(rho_meas), recall from a piecewise-linear
curve over MEASURED density (or a constant). Never iterated on corrected density.
"""
from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass
from typing import Optional, Sequence

import numpy as np


@dataclass
class GridSpec:
    width_m: float
    height_m: float
    cell_m: float = 1.0

    @property
    def cols(self) -> int:
        return max(1, int(math.ceil(self.width_m / self.cell_m - 1e-9)))

    @property
    def rows(self) -> int:
        return max(1, int(math.ceil(self.height_m / self.cell_m - 1e-9)))

    def centres(self):
        xs = (np.arange(self.cols) + 0.5) * self.cell_m
        ys = (np.arange(self.rows) + 0.5) * self.cell_m
        gx, gy = np.meshgrid(xs, ys)  # (rows, cols)
        return gx, gy


class RecallModel:
    def __init__(self, recall_estimate: float = 1.0, recall_curve: Optional[Sequence] = None):
        self.const = float(recall_estimate)
        if recall_curve:
            pts = sorted((float(d), float(r)) for d, r in recall_curve)
            self.xs = np.array([p[0] for p in pts])
            self.rs = np.array([p[1] for p in pts])
        else:
            self.xs = self.rs = None

    def recall(self, rho_meas: np.ndarray) -> np.ndarray:
        if self.xs is None:
            return np.full_like(rho_meas, self.const, dtype=float)
        return np.interp(rho_meas, self.xs, self.rs)

    def correct(self, rho_meas: np.ndarray) -> np.ndarray:
        r = np.clip(self.recall(rho_meas), 1e-3, None)
        return rho_meas / r


def fit_recall_curve(measured_density: Sequence[float], recall: Sequence[float],
                     bin_width: float = 0.5, min_per_bin: int = 3) -> list:
    """Fit a piecewise-linear recall curve over measured density from paired samples.

    Samples are binned by measured density; each bin with enough samples contributes
    (mean measured density, mean recall). Returns [[density, recall], ...] sorted.
    """
    d = np.asarray(measured_density, dtype=float)
    r = np.asarray(recall, dtype=float)
    ok = np.isfinite(d) & np.isfinite(r) & (r > 0)
    d, r = d[ok], r[ok]
    if len(d) == 0:
        return []
    bins = np.floor(d / bin_width).astype(int)
    curve = []
    for b in np.unique(bins):
        m = bins == b
        if m.sum() >= min_per_bin:
            curve.append([round(float(d[m].mean()), 3), round(float(np.clip(r[m].mean(), 0.05, 1.5)), 3)])
    return curve


class FieldComputer:
    def __init__(self, spec: GridSpec, kernel_R: float = 1.0, cutoff_R: float = 3.0,
                 velocity_min_weight: float = 1.0, speed_min_weight: float = 0.3,
                 recall: Optional[RecallModel] = None):
        self.spec = spec
        self.R = kernel_R
        self.cut2 = (cutoff_R * kernel_R) ** 2
        self.vmin_w = velocity_min_weight
        self.smin_w = speed_min_weight
        self.recall = recall or RecallModel()
        gx, gy = spec.centres()
        self.cx = gx.ravel()
        self.cy = gy.ravel()

    def compute(self, xy: np.ndarray, vel: np.ndarray, vmask: np.ndarray) -> dict:
        """xy (N,2) floor points; vel (N,2); vmask (N,) True where the velocity is valid."""
        rows, cols = self.spec.rows, self.spec.cols
        n_cells = rows * cols
        if len(xy) == 0:
            zeros = np.zeros((rows, cols))
            nan = np.full((rows, cols), np.nan)
            return {"rho_meas": zeros, "rho": zeros.copy(), "vx": nan, "vy": nan.copy(),
                    "speed": nan.copy(), "wsum_v": zeros.copy()}
        dx = xy[:, 0:1] - self.cx[None, :]
        dy = xy[:, 1:2] - self.cy[None, :]
        d2 = dx * dx + dy * dy                                     # (N, cells)
        w = np.where(d2 <= self.cut2, np.exp(-d2 / (self.R * self.R)), 0.0)
        rho_meas = w.sum(axis=0) / (math.pi * self.R * self.R)
        rho = self.recall.correct(rho_meas)

        wv = w[vmask]
        v = vel[vmask]
        wsum = wv.sum(axis=0) if len(v) else np.zeros(n_cells)
        with np.errstate(invalid="ignore", divide="ignore"):
            if len(v):
                vx = (wv * v[:, 0:1]).sum(axis=0) / wsum
                vy = (wv * v[:, 1:2]).sum(axis=0) / wsum
                sp = (wv * np.hypot(v[:, 0], v[:, 1])[:, None]).sum(axis=0) / wsum
            else:
                vx = vy = sp = np.full(n_cells, np.nan)
        vx = np.where(wsum >= self.vmin_w, vx, np.nan)
        vy = np.where(wsum >= self.vmin_w, vy, np.nan)
        sp = np.where(wsum >= self.smin_w, sp, np.nan)
        shape = (rows, cols)
        return {"rho_meas": rho_meas.reshape(shape), "rho": rho.reshape(shape),
                "vx": vx.reshape(shape), "vy": vy.reshape(shape), "speed": sp.reshape(shape),
                "wsum_v": wsum.reshape(shape)}


class PressureBuffer:
    """Per-cell ring buffer of V(r, t) samples over the last window_s seconds."""

    def __init__(self, shape, window_s: float = 8.0, min_samples: int = 15, min_density: float = 3.0):
        self.shape = shape
        self.window = window_s
        self.min_samples = min_samples
        self.min_density = min_density
        self.samples: deque = deque()

    def reset(self):
        self.samples.clear()

    def update(self, t: float, vx: np.ndarray, vy: np.ndarray, rho: np.ndarray) -> np.ndarray:
        self.samples.append((t, vx, vy))
        while self.samples and t - self.samples[0][0] > self.window + 1e-9:
            self.samples.popleft()
        VX = np.stack([s[1] for s in self.samples])  # (S, rows, cols)
        VY = np.stack([s[2] for s in self.samples])
        valid = np.isfinite(VX) & np.isfinite(VY)
        n = valid.sum(axis=0)
        with np.errstate(invalid="ignore", divide="ignore"):
            mx = np.where(valid, VX, 0).sum(axis=0) / n
            my = np.where(valid, VY, 0).sum(axis=0) / n
            dev = np.where(valid, (VX - mx) ** 2 + (VY - my) ** 2, 0).sum(axis=0) / n
        ok = (n >= self.min_samples) & (rho >= self.min_density)
        return np.where(ok, rho * dev, 0.0)


def summarize(count: int, rho: np.ndarray, rho_meas: np.ndarray, P: np.ndarray, speeds: Sequence[float],
              area_m2: float, p_thr: float) -> dict:
    flat = int(np.argmax(rho)) if rho.size else 0
    r, c = divmod(flat, rho.shape[1]) if rho.size else (0, 0)
    dense = rho >= 1.0
    sp = [s for s in speeds if s is not None]
    return {
        "count": int(count),
        "density": {
            "max": float(rho.max()) if rho.size else 0.0,
            "mean": float(count / area_m2) if area_m2 > 0 else 0.0,
            "p95": float(np.percentile(rho, 95)) if rho.size else 0.0,
            "peak_cell": [int(r), int(c)],
            "measured_max": float(rho_meas.max()) if rho_meas.size else 0.0,
        },
        "pressure": {
            "max": float(P.max()) if P.size else 0.0,
            "mean": float(P[dense].mean()) if dense.any() else 0.0,
            "threshold": float(p_thr),
        },
        "speed": {"mean": float(np.mean(sp)) if sp else None},
    }
