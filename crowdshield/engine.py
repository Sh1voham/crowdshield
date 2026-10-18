"""One-call engine: DetFrame in, Snapshot out (at the field update rate).

Uses frame timestamps only. Wall-clock concerns (staleness, latency, publishing
rates) live in publish.py and run.py.
"""
from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass, field
from typing import List, Optional

import numpy as np

from .assess import Assessor
from .config import Config
from .fields import FieldComputer, GridSpec, PressureBuffer, RecallModel, summarize
from .geometry import Calibration
from .io_pipegen import DetFrame
from .kinematics import Kinematics
from .tracker import NNTracker

log = logging.getLogger("crowdshield.engine")


@dataclass
class Snapshot:
    t: float
    frame: Optional[int]
    count: int
    tracks: list                     # [(id, x, y, vx|None, vy|None), ...]
    rho: np.ndarray
    rho_meas: np.ndarray
    pressure: np.ndarray
    speed: np.ndarray
    vx: np.ndarray
    vy: np.ndarray
    summary: dict
    assessment: dict
    pidx: float
    fps: Optional[float]
    abs_ts: Optional[float] = None
    events: List[dict] = field(default_factory=list)


class Engine:
    def __init__(self, cfg: Config, calib: Calibration):
        self.cfg = cfg
        self.assessor = Assessor.from_config(cfg)
        self.kin = Kinematics(cfg.kinematics.min_dt_s, cfg.kinematics.ema_tau_s, cfg.kinematics.min_age_s,
                              cfg.kinematics.history_s, max_age_s=cfg.tracker.max_age_s)
        self.tracker = NNTracker(cfg.tracker.gate_m, cfg.tracker.max_age_s, first_id=1)
        self.recall = RecallModel(cfg.recall.recall_estimate, cfg.recall.recall_curve)
        self.period = 1.0 / cfg.grid.update_hz
        self.use_fallback_tracker = False
        self._id_frames = 0
        self._det_frames = 0
        self._frame_times: deque = deque()
        self._last_t: Optional[float] = None
        self.frames_processed = 0
        self.set_calibration(calib)

    # ------------------------------------------------------------------------------
    def set_calibration(self, calib: Calibration) -> None:
        self.calib = calib
        self.spec = GridSpec(calib.width_m, calib.height_m, self.cfg.grid.cell_m)
        g, p = self.cfg.grid, self.cfg.pressure
        self.fields = FieldComputer(self.spec, g.kernel_R_m, g.cutoff_R, p.velocity_min_weight,
                                    p.speed_min_weight, self.recall)
        self.pbuf = PressureBuffer((self.spec.rows, self.spec.cols), p.window_s, p.min_samples, p.min_density)
        self.next_update: Optional[float] = None
        self.last_xy = np.zeros((0, 2))

    def reset_stream(self) -> None:
        """Producer restarted: drop motion history (the time base changes)."""
        self.kin = Kinematics(self.cfg.kinematics.min_dt_s, self.cfg.kinematics.ema_tau_s,
                              self.cfg.kinematics.min_age_s, self.cfg.kinematics.history_s,
                              max_age_s=self.cfg.tracker.max_age_s)
        self.tracker = NNTracker(self.cfg.tracker.gate_m, self.cfg.tracker.max_age_s, first_id=1)
        self.pbuf.reset()
        self.assessor.reset()
        self.next_update = None
        self._frame_times.clear()

    def fps(self) -> Optional[float]:
        ft = self._frame_times
        if len(ft) < 3 or ft[-1] - ft[0] <= 0:
            return None
        return (len(ft) - 1) / (ft[-1] - ft[0])

    # ------------------------------------------------------------------------------
    def process(self, fr: DetFrame) -> Optional[Snapshot]:
        t = fr.t
        if self._last_t is not None and t < self._last_t - 0.5:
            # the producer restarted (e.g. a looping video): stream time began again
            log.info("engine: stream time went back from %.1f to %.1f s; resetting motion history", self._last_t, t)
            self.reset_stream()
        self._last_t = t
        self.frames_processed += 1
        self._frame_times.append(t)
        while self._frame_times and t - self._frame_times[0] > 2.0:
            self._frame_times.popleft()

        if fr.dets:
            boxes = np.array([[d.x1, d.y1, d.x2, d.y2] for d in fr.dets], dtype=float)
            up_ids = [d.track_id for d in fr.dets]
        else:
            boxes = np.zeros((0, 4))
            up_ids = []
        xy, keep = self.calib.to_floor(boxes, fr.width, fr.height, self.cfg.grid.area_margin_m)
        xy = xy[keep]
        up_ids = [i for i, k in zip(up_ids, keep) if k]

        if len(up_ids):
            self._det_frames += 1
            if sum(i is not None for i in up_ids) >= 0.5 * len(up_ids):
                self._id_frames += 1
            if (not self.use_fallback_tracker and self._det_frames >= 20
                    and self._id_frames <= 0.05 * self._det_frames):
                self.use_fallback_tracker = True
                log.warning("engine: upstream detections carry no track IDs; using the fallback "
                            "NN tracker (prefer enabling nvtracker in PipeGen)")

        n = len(xy)
        vel = np.zeros((n, 2))
        vmask = np.zeros(n, dtype=bool)
        if self.use_fallback_tracker:
            ids = list(self.tracker.update(t, xy))
        else:
            ids = up_ids
        tracked_idx = [k for k, i in enumerate(ids) if i is not None]
        if tracked_idx:
            vs = self.kin.update(t, [ids[k] for k in tracked_idx], xy[tracked_idx])
            for k, v in zip(tracked_idx, vs):
                if v is not None:
                    vel[k] = v
                    vmask[k] = True
        else:
            self.kin.update(t, [], [])
        self.last_xy = xy

        if self.next_update is None:
            self.next_update = t
        if t + 1e-9 < self.next_update:
            return None
        self.next_update += self.period
        if self.next_update <= t:
            self.next_update = t + self.period

        f = self.fields.compute(xy, vel, vmask)
        P = self.pbuf.update(t, f["vx"], f["vy"], f["rho"])
        speeds = [float(np.hypot(*vel[k])) for k in range(n) if vmask[k]]
        area = self.calib.width_m * self.calib.height_m
        summ = summarize(n, f["rho"], f["rho_meas"], P, speeds, area, self.cfg.pressure.p_thr)
        pidx = summ["pressure"]["max"] / self.cfg.pressure.p_thr
        a = self.assessor.step(t, summ["density"]["max"], pidx)
        tracks = []
        for k in range(n):
            tid = ids[k]
            if vmask[k]:
                tracks.append((tid, float(xy[k, 0]), float(xy[k, 1]), float(vel[k, 0]), float(vel[k, 1])))
            else:
                tracks.append((tid, float(xy[k, 0]), float(xy[k, 1]), None, None))
        return Snapshot(t=t, frame=fr.frame, count=n, tracks=tracks, rho=f["rho"], rho_meas=f["rho_meas"],
                        pressure=P, speed=f["speed"], vx=f["vx"], vy=f["vy"], summary=summ, assessment=a,
                        pidx=pidx, fps=self.fps(), abs_ts=fr.abs_ts, events=a["events"])
