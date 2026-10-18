"""FP16 vs INT8: the same clip recorded through both PipeGen engines.

    python tools/compare_precision.py recordings/dense_fp16.jsonl recordings/dense_int8.jsonl \
        --calibration config/calibration.json [--speed evidence/speed_reports.json]

Frames are aligned by PipeGen frame number. Reports, per precision: mean detections per frame,
mean people inside the area, agreement with the other precision, alerts the engine raises on the
clip, and (if eval/counts.csv has hand counts for these frames) count MAE. Latency, FPS and power
come ONLY from PipeGen's speed reports, typed into evidence/speed_reports.json by a person:
    {"fp16": {"latency_ms": ..., "fps": ..., "power_w": ..., "source": "evidence/speed_report_fp16.png"}, "int8": {...}}
Writes eval/precision.json for docs/RESULTS.md.
"""
from __future__ import annotations

import argparse
import csv
import json

import numpy as np

from evalcommon import ROOT, Calibration, frames, run_engine


def per_frame(path, cal):
    out = {}
    for fr in frames(path):
        if fr.frame is None:
            continue
        boxes = np.array([[d.x1, d.y1, d.x2, d.y2] for d in fr.dets]) if fr.dets else np.zeros((0, 4))
        _, keep = cal.to_floor(boxes, fr.width, fr.height, margin_m=0.0)
        out[fr.frame] = (len(fr.dets), int(keep.sum()))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("fp16"); ap.add_argument("int8")
    ap.add_argument("--calibration", default="config/calibration.json")
    ap.add_argument("--speed", default=str(ROOT / "evidence" / "speed_reports.json"))
    args = ap.parse_args()
    cal = Calibration.load(args.calibration)
    a, b = per_frame(args.fp16, cal), per_frame(args.int8, cal)
    common = sorted(set(a) & set(b))
    counts = {}
    try:
        for r in csv.DictReader(open(ROOT / "eval" / "counts.csv", encoding="utf-8")):
            if r["true_count"].strip():
                counts[int(r["frame"])] = float(r["true_count"])
    except FileNotFoundError:
        pass
    speed = {}
    try:
        speed = json.loads(open(args.speed, encoding="utf-8").read())
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    res = {"frames_aligned": len(common), "calibration": args.calibration}
    for name, path, d in (("fp16", args.fp16, a), ("int8", args.int8, b)):
        dets = np.array([d[f][0] for f in common]) if common else np.zeros(0)
        inside = np.array([d[f][1] for f in common]) if common else np.zeros(0)
        alerts = [f"{e['level']}:{e['kind']}" for s in run_engine(path, args.calibration) for e in s.events]
        mae = None
        hand = [f for f in common if f in counts]
        if hand:
            mae = round(float(np.mean([abs(d[f][1] - counts[f]) for f in hand])), 2)
        res[name] = {"recording": path, "mean_detections": round(float(dets.mean()), 2) if len(dets) else None,
                     "mean_in_area": round(float(inside.mean()), 2) if len(inside) else None,
                     "alerts": alerts, "count_mae": mae, "hand_counted_frames": len(hand),
                     "pipegen_speed_report": speed.get(name) or "[FILL: from PipeGen's speed report]"}
    if common:
        diff = np.array([a[f][1] - b[f][1] for f in common])
        res["mean_in_area_difference_fp16_minus_int8"] = round(float(diff.mean()), 3)
    print(json.dumps(res, indent=2))
    (ROOT / "eval").mkdir(exist_ok=True)
    (ROOT / "eval" / "precision.json").write_text(json.dumps(res, indent=2) + "\n", encoding="utf-8")
    print("wrote eval/precision.json")


if __name__ == "__main__":
    main()
