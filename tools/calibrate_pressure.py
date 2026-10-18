"""Calibrate the crowd-pressure threshold on a CALM recording from this camera.

    python tools/calibrate_pressure.py recordings/calm_01.jsonl --calibration config/calibration.json [--write --yes]

Prints the 99th percentile of P over cells that pass the density gate, proposes
P_thr = max(0.02, 3 x P99_calm), and (with --write --yes) stores it in config/default.json
(backup kept), then replays the calm recording and checks it raises zero AMBER/RED alerts.
Results go to eval/pressure_calibration.json for docs/RESULTS.md.
"""
from __future__ import annotations

import argparse
import json
import shutil
from dataclasses import replace

import numpy as np

from evalcommon import ROOT, load_config, run_engine


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("recording")
    ap.add_argument("--calibration", default="config/calibration.json")
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--yes", action="store_true", help="confirm writing config/default.json")
    args = ap.parse_args()
    cfg = load_config()
    vals, dur = [], 0.0
    for s in run_engine(args.recording, args.calibration, cfg=cfg):
        gated = s.pressure[s.rho >= cfg.pressure.min_density]
        vals.extend(gated[gated > 0].tolist())
        dur = s.t
    p99 = float(np.percentile(vals, 99)) if vals else 0.0
    proposed = max(0.02, 3.0 * p99)
    print(f"calm recording: {dur:.0f} s, {len(vals)} gated cell samples with P > 0, P99 = {p99:.5f} s^-2")
    print(f"proposed P_thr = max(0.02, 3 x P99) = {proposed:.5f} s^-2 (current {cfg.pressure.p_thr})")
    if dur < 300:
        print("WARNING: shorter than 5 minutes; record a longer calm period before trusting this.")
    cfg2 = replace(cfg, pressure=replace(cfg.pressure, p_thr=proposed))
    alerts = sum(1 for s in run_engine(args.recording, args.calibration, cfg=cfg2)
                 for e in s.events if e["level"] in ("AMBER", "RED"))
    print(f"replay of the calm recording with the proposed threshold: {alerts} AMBER/RED alerts")
    res = {"recording": args.recording, "duration_s": round(dur, 1), "samples": len(vals), "p99": p99,
           "proposed_p_thr": proposed, "previous_p_thr": cfg.pressure.p_thr, "calm_replay_alerts": alerts,
           "written": False}
    if args.write:
        if not args.yes:
            print("not written: add --yes to confirm")
        else:
            path = ROOT / "config" / "default.json"
            shutil.copy2(path, path.with_suffix(".json.bak"))
            raw = json.loads(path.read_text(encoding="utf-8"))
            raw["pressure"]["p_thr"] = round(proposed, 5)
            path.write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8")
            res["written"] = True
            print(f"wrote pressure.p_thr = {proposed:.5f} to {path} (backup .bak)")
    (ROOT / "eval").mkdir(exist_ok=True)
    (ROOT / "eval" / "pressure_calibration.json").write_text(json.dumps(res, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
