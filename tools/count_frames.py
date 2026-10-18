"""Hand counts for detector recall, and the recall curve over measured density.

Step 1 (prepare):  python tools/count_frames.py prepare recordings/NAME.jsonl VIDEO.mp4 --calibration config/calibration.json
    Picks ~30 frames spread across measured-density bands, saves them to eval/frames/ with the
    detections and the calibrated area drawn, and writes eval/counts.csv with an empty true_count.
Step 2 (human):    open each image and type the true number of people inside the area in true_count.
Step 3 (fit):      python tools/count_frames.py fit [--write --yes]
    Count MAE, overall recall, and the recall curve over measured density (fit_recall_curve);
    with --write --yes stores recall_curve / recall_estimate in config/default.json (backup kept).
    Results go to eval/recall.json for docs/RESULTS.md.
"""
from __future__ import annotations

import argparse
import csv
import json
import shutil

import numpy as np

from evalcommon import ROOT, Calibration, frames
from crowdshield.fields import fit_recall_curve

EVAL = ROOT / "eval"


def prepare(args):
    import cv2
    cal = Calibration.load(args.calibration)
    area = cal.width_m * cal.height_m
    rows = []
    for fr in frames(args.recording):
        if fr.frame is None or not fr.dets:
            continue
        boxes = np.array([[d.x1, d.y1, d.x2, d.y2] for d in fr.dets])
        _, keep = cal.to_floor(boxes, fr.width, fr.height, margin_m=0.0)
        rows.append((fr.frame, int(keep.sum()), fr.width, fr.height, fr.dets, keep))
    if not rows:
        raise SystemExit("no frames with detections and frame numbers")
    dens = np.array([r[1] / area for r in rows])
    edges = np.quantile(dens, np.linspace(0, 1, 7))
    pick = []
    for lo, hi in zip(edges[:-1], edges[1:]):
        idx = [i for i, d in enumerate(dens) if lo <= d <= hi]
        if idx:
            pick += list(np.random.default_rng(1).choice(idx, size=min(5, len(idx)), replace=False))
    pick = sorted(set(int(i) for i in pick))
    (EVAL / "frames").mkdir(parents=True, exist_ok=True)
    cap = cv2.VideoCapture(args.video)
    quad = cal.floor_to_image(np.array([[0, 0], [cal.width_m, 0], [cal.width_m, cal.height_m], [0, cal.height_m]]))
    with open(EVAL / "counts.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["frame", "region", "measured_count", "measured_density", "true_count", "image"])
        for i in pick:
            frame_no, n, W, H, dets, keep = rows[i]
            cap.set(cv2.CAP_PROP_POS_FRAMES, frame_no)
            ok, img = cap.read()
            if not ok:
                continue
            sx, sy = img.shape[1] / (W or img.shape[1]), img.shape[0] / (H or img.shape[0])
            cx, cy = img.shape[1] / (cal.frame_size[0] if cal.frame_size else img.shape[1]), img.shape[0] / (cal.frame_size[1] if cal.frame_size else img.shape[0])
            cv2.polylines(img, [np.int32(quad * [cx, cy])], True, (0, 220, 255), 2)
            for d, k in zip(dets, keep):
                cv2.rectangle(img, (int(d.x1 * sx), int(d.y1 * sy)), (int(d.x2 * sx), int(d.y2 * sy)), (60, 220, 60) if k else (120, 120, 120), 1)
            name = f"frame_{frame_no:06d}.jpg"
            cv2.imwrite(str(EVAL / "frames" / name), img)
            w.writerow([frame_no, "area", n, round(n / area, 3), "", f"eval/frames/{name}"])
    print(f"wrote {len(pick)} frames to eval/frames/ and eval/counts.csv: fill in true_count, then run `fit`")


def fit(args):
    rows = [r for r in csv.DictReader(open(EVAL / "counts.csv", encoding="utf-8")) if r["true_count"].strip()]
    if not rows:
        raise SystemExit("eval/counts.csv has no true_count values yet")
    m = np.array([float(r["measured_count"]) for r in rows])
    t = np.array([float(r["true_count"]) for r in rows])
    dm = np.array([float(r["measured_density"]) for r in rows])
    ok = t > 0
    recall = m[ok] / t[ok]
    curve = fit_recall_curve(dm[ok], recall, bin_width=0.5, min_per_bin=2)
    res = {"frames": len(rows), "count_mae": round(float(np.abs(m - t).mean()), 2),
           "overall_recall": round(float(m.sum() / max(t.sum(), 1)), 3), "recall_curve": curve,
           "recall_estimate": round(float(np.clip(m.sum() / max(t.sum(), 1), 0.05, 1.5)), 3), "written": False}
    print(json.dumps(res, indent=2))
    if args.write:
        if not args.yes:
            print("not written: add --yes to confirm")
        else:
            path = ROOT / "config" / "default.json"
            shutil.copy2(path, path.with_suffix(".json.bak"))
            raw = json.loads(path.read_text(encoding="utf-8"))
            raw["recall"]["recall_curve"] = curve if len(curve) >= 2 else None
            raw["recall"]["recall_estimate"] = res["recall_estimate"]
            path.write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8")
            res["written"] = True
            print(f"wrote the recall settings to {path} (backup .bak)")
    (EVAL / "recall.json").write_text(json.dumps(res, indent=2) + "\n", encoding="utf-8")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("prepare")
    p.add_argument("recording"); p.add_argument("video"); p.add_argument("--calibration", default="config/calibration.json")
    q = sub.add_parser("fit")
    q.add_argument("--write", action="store_true"); q.add_argument("--yes", action="store_true")
    args = ap.parse_args()
    prepare(args) if args.cmd == "prepare" else fit(args)


if __name__ == "__main__":
    main()
