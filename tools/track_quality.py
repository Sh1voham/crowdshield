"""Tracking quality on a recording: track lengths, short tracks, ID births/deaths, speed sanity.

    python tools/track_quality.py recordings/NAME.jsonl --calibration config/calibration.json [--json eval/track_quality_NAME.json]

Sanity check: the 99th percentile of floor speed must stay below 3 m/s after the homography
(people do not run at 10 m/s; if they seem to, the calibration or the tracker is wrong).
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict

import numpy as np

from evalcommon import Calibration, frames


def analyse(recording, calibration):
    cal = Calibration.load(calibration)
    first, last, pos = {}, {}, defaultdict(list)
    t0 = t1 = None
    n_frames = 0
    untracked = total = 0
    for fr in frames(recording):
        n_frames += 1
        t0 = fr.t if t0 is None else t0
        t1 = fr.t
        if not fr.dets:
            continue
        boxes = np.array([[d.x1, d.y1, d.x2, d.y2] for d in fr.dets])
        xy, keep = cal.to_floor(boxes, fr.width, fr.height)
        for d, p, k in zip(fr.dets, xy, keep):
            total += 1
            if d.track_id is None:
                untracked += 1
                continue
            first.setdefault(d.track_id, fr.t)
            last[d.track_id] = fr.t
            if k:
                pos[d.track_id].append((fr.t, p[0], p[1]))
    dur = (t1 - t0) if t0 is not None else 0.0
    lengths = np.array([last[i] - first[i] for i in first]) if first else np.zeros(0)
    speeds = []
    for tr in pos.values():
        for (ta, xa, ya), (tb, xb, yb) in zip(tr, tr[5:]):  # >= 5 frames apart smooths box jitter
            if tb - ta > 0.15:
                speeds.append(np.hypot(xb - xa, yb - ya) / (tb - ta))
    sp = np.array(speeds)
    minutes = max(dur / 60.0, 1e-9)
    res = {
        "recording": str(recording), "calibration": str(calibration), "frames": n_frames, "duration_s": round(dur, 1),
        "detections": total, "untracked_fraction": round(untracked / total, 4) if total else None,
        "tracks": len(first), "median_track_s": round(float(np.median(lengths)), 2) if len(lengths) else None,
        "p90_track_s": round(float(np.percentile(lengths, 90)), 2) if len(lengths) else None,
        "short_track_fraction": round(float((lengths < 1.0).mean()), 4) if len(lengths) else None,
        "births_per_min": round(len(first) / minutes, 1),
        "deaths_per_min": round(sum(1 for i in last if last[i] < t1 - 1.0) / minutes, 1) if t1 is not None else None,
        "speed_p50_mps": round(float(np.percentile(sp, 50)), 2) if len(sp) else None,
        "speed_p99_mps": round(float(np.percentile(sp, 99)), 2) if len(sp) else None,
    }
    res["speed_sanity_pass"] = bool(res["speed_p99_mps"] is not None and res["speed_p99_mps"] < 3.0)
    return res


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("recording")
    ap.add_argument("--calibration", default="config/calibration.json")
    ap.add_argument("--json")
    args = ap.parse_args()
    res = analyse(args.recording, args.calibration)
    for k, v in res.items():
        print(f"{k:22s} {v}")
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(res, f, indent=2)
        print(f"wrote {args.json}")


if __name__ == "__main__":
    main()
