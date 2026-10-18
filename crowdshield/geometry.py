"""Calibration and homography: camera pixels -> floor metres.

Foot modes:
  bbox_bottom  body detector: the bottom-centre of the box stands on the floor.
  head_offset  head detector with a floor calibration: foot = (cx, y2 + head_k * box_h).
  head_plane   head detector with a head-plane calibration (corners clicked on the heads
               of people standing on the taped corners): map the head centre directly.
"""
from __future__ import annotations

import json
import math
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

import numpy as np

FOOT_MODES = ("bbox_bottom", "head_offset", "head_plane")


def homography(image_points: Sequence[Sequence[float]], world_points: Sequence[Sequence[float]]) -> np.ndarray:
    """Normalised DLT (Hartley). Maps image (u, v) to world (x, y). Needs >= 4 points."""
    src = np.asarray(image_points, dtype=float)
    dst = np.asarray(world_points, dtype=float)
    if src.shape != dst.shape or src.shape[0] < 4 or src.shape[1] != 2:
        raise ValueError("homography needs >= 4 matching 2D points")

    def norm(p):
        c = p.mean(axis=0)
        d = np.sqrt(((p - c) ** 2).sum(axis=1)).mean()
        s = math.sqrt(2) / d if d > 0 else 1.0
        T = np.array([[s, 0, -s * c[0]], [0, s, -s * c[1]], [0, 0, 1]])
        ph = np.c_[p, np.ones(len(p))] @ T.T
        return ph[:, :2], T

    s_n, Ts = norm(src)
    d_n, Td = norm(dst)
    rows = []
    for (u, v), (x, y) in zip(s_n, d_n):
        rows.append([-u, -v, -1, 0, 0, 0, u * x, v * x, x])
        rows.append([0, 0, 0, -u, -v, -1, u * y, v * y, y])
    _, _, vt = np.linalg.svd(np.asarray(rows))
    Hn = vt[-1].reshape(3, 3)
    H = np.linalg.inv(Td) @ Hn @ Ts
    return H / H[2, 2]


def apply_h(H: np.ndarray, pts: np.ndarray) -> np.ndarray:
    pts = np.asarray(pts, dtype=float).reshape(-1, 2)
    ph = np.c_[pts, np.ones(len(pts))] @ H.T
    w = ph[:, 2:3]
    with np.errstate(divide="ignore", invalid="ignore"):
        out = ph[:, :2] / w
    return out


def _convex(quad: np.ndarray) -> bool:
    signs = []
    n = len(quad)
    for i in range(n):
        a, b, c = quad[i], quad[(i + 1) % n], quad[(i + 2) % n]
        cross = (b[0] - a[0]) * (c[1] - b[1]) - (b[1] - a[1]) * (c[0] - b[0])
        if abs(cross) < 1e-9:
            return False
        signs.append(cross > 0)
    return all(signs) or not any(signs)


def validate_calibration(d: dict) -> List[str]:
    """Return a list of problems (empty when the calibration is usable)."""
    errs: List[str] = []
    try:
        img = np.asarray(d["image_points"], dtype=float)
        wld = np.asarray(d["world_points"], dtype=float)
    except (KeyError, TypeError, ValueError):
        return ["image_points and world_points must be lists of [x, y] pairs"]
    if img.shape != (4, 2) or wld.shape != (4, 2):
        return ["exactly 4 image_points and 4 world_points are required"]
    if not (np.isfinite(img).all() and np.isfinite(wld).all()):
        errs.append("points must be finite numbers")
    for name, pts in (("image_points", img), ("world_points", wld)):
        dmin = min(np.linalg.norm(pts[i] - pts[j]) for i in range(4) for j in range(i + 1, 4))
        if dmin < 1e-6:
            errs.append(f"{name} must be 4 distinct points")
        elif not _convex(pts):
            errs.append(f"{name} must form a convex quadrilateral (click the corners in order)")
    area = d.get("area") or {}
    try:
        if float(area.get("width_m", 0)) <= 0 or float(area.get("height_m", 0)) <= 0:
            errs.append("area.width_m and area.height_m must be positive")
    except (TypeError, ValueError):
        errs.append("area sizes must be numbers")
    fs = d.get("frame_size")
    if fs is not None:
        try:
            if len(fs) != 2 or float(fs[0]) <= 0 or float(fs[1]) <= 0:
                errs.append("frame_size must be [W, H] with positive values")
        except (TypeError, ValueError):
            errs.append("frame_size must be [W, H]")
    if d.get("foot_mode", "bbox_bottom") not in FOOT_MODES:
        errs.append(f"foot_mode must be one of {FOOT_MODES}")
    gate = d.get("gate")
    if gate is not None and not isinstance(gate, dict):
        errs.append("gate must be an object {x, y0, y1, label}")
    if not errs:
        try:
            H = homography(img, wld)
            back = apply_h(H, img)
            if not np.allclose(back, wld, atol=1e-6):
                errs.append("homography does not reproduce the corners")
        except (ValueError, np.linalg.LinAlgError):
            errs.append("homography could not be computed from these points")
    return errs


@dataclass
class Calibration:
    image_points: List[List[float]]
    world_points: List[List[float]]
    width_m: float
    height_m: float
    frame_size: Optional[Tuple[int, int]] = None
    foot_mode: str = "bbox_bottom"
    head_k: float = 6.5
    plane_height_m: float = 0.0
    zone: str = "Gate A"
    gate: Optional[dict] = None
    raw: dict = field(default_factory=dict)

    def __post_init__(self):
        self.H = homography(self.image_points, self.world_points)
        self.H_inv = np.linalg.inv(self.H)

    @classmethod
    def from_dict(cls, d: dict) -> "Calibration":
        errs = validate_calibration(d)
        if errs:
            raise ValueError("invalid calibration: " + "; ".join(errs))
        area = d["area"]
        fs = d.get("frame_size")
        return cls(
            image_points=[list(map(float, p)) for p in d["image_points"]],
            world_points=[list(map(float, p)) for p in d["world_points"]],
            width_m=float(area["width_m"]),
            height_m=float(area["height_m"]),
            frame_size=(int(fs[0]), int(fs[1])) if fs else None,
            foot_mode=d.get("foot_mode", "bbox_bottom"),
            head_k=float(d.get("head_k", 6.5)),
            plane_height_m=float(d.get("plane_height_m", 0.0)),
            zone=d.get("zone", "Gate A"),
            gate=d.get("gate"),
            raw=dict(d),
        )

    @classmethod
    def load(cls, path: str | Path) -> "Calibration":
        with open(path, encoding="utf-8") as f:
            return cls.from_dict(json.load(f))

    def to_dict(self) -> dict:
        out = dict(self.raw)
        out.update({
            "image_points": self.image_points, "world_points": self.world_points,
            "area": {"width_m": self.width_m, "height_m": self.height_m},
            "frame_size": list(self.frame_size) if self.frame_size else None,
            "foot_mode": self.foot_mode, "head_k": self.head_k, "zone": self.zone, "gate": self.gate,
        })
        return out

    # ---------------------------------------------------------------------------
    def image_to_floor(self, uv: np.ndarray) -> np.ndarray:
        return apply_h(self.H, uv)

    def floor_to_image(self, xy: np.ndarray) -> np.ndarray:
        return apply_h(self.H_inv, xy)

    def foot_pixels(self, boxes: np.ndarray) -> np.ndarray:
        """boxes: (N, 4) x1, y1, x2, y2 in calibration pixels -> (N, 2) pixels to map."""
        cx = 0.5 * (boxes[:, 0] + boxes[:, 2])
        if self.foot_mode == "bbox_bottom":
            return np.c_[cx, boxes[:, 3]]
        if self.foot_mode == "head_offset":
            h = boxes[:, 3] - boxes[:, 1]
            return np.c_[cx, boxes[:, 3] + self.head_k * h]
        # head_plane: the head centre lies on the calibrated head plane
        return np.c_[cx, 0.5 * (boxes[:, 1] + boxes[:, 3])]

    def to_floor(self, boxes: np.ndarray, width: Optional[int] = None, height: Optional[int] = None,
                 margin_m: float = 0.5) -> Tuple[np.ndarray, np.ndarray]:
        """Map detection boxes to floor points. Returns (xy (N,2), keep mask (N,)).

        If the detections come at a different resolution than the calibration frame
        (e.g. nvstreammux at 1280x720, calibration clicked on a 1920x1080 still), the
        pixels are rescaled first.
        """
        boxes = np.asarray(boxes, dtype=float).reshape(-1, 4)
        if len(boxes) == 0:
            return np.zeros((0, 2)), np.zeros(0, dtype=bool)
        if self.frame_size and width and height and (width, height) != tuple(self.frame_size):
            sx, sy = self.frame_size[0] / width, self.frame_size[1] / height
            boxes = boxes * np.array([sx, sy, sx, sy])
        xy = self.image_to_floor(self.foot_pixels(boxes))
        keep = (np.isfinite(xy).all(axis=1)
                & (xy[:, 0] >= -margin_m) & (xy[:, 0] <= self.width_m + margin_m)
                & (xy[:, 1] >= -margin_m) & (xy[:, 1] <= self.height_m + margin_m))
        return xy, keep


def save_calibration(path: str | Path, d: dict) -> None:
    """Validate, back up the old file as .bak, and write atomically."""
    errs = validate_calibration(d)
    if errs:
        raise ValueError("; ".join(errs))
    path = Path(path)
    if path.exists():
        shutil.copy2(path, path.with_suffix(path.suffix + ".bak"))
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=2)
        f.write("\n")
    tmp.replace(path)
