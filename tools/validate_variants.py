"""Run the engine on the synthetic camera/detector variants and compare with the
reference engine's `raw` rows in VALIDATION.md (no recall correction).

Usage: python tools/validate_variants.py [--only NAME] [--min-density 3.0] [--json OUT]
All data here are SYNTHETIC.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from crowdshield.config import load_config  # noqa: E402
from crowdshield.engine import Engine  # noqa: E402
from crowdshield.geometry import Calibration  # noqa: E402
from crowdshield.io_pipegen import Adapter, AdapterOptions, jsonl_source  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
VARIANTS = ROOT / "data" / "synthetic" / "variants"

# Reference `raw` rows from VALIDATION.md section 3: (peak density est, first RED est or None)
REFERENCE_RAW = {
    "building__low_wall_1080_body": (5.00, None),
    "surge__low_wall_1080_body": (5.10, None),
    "platform__low_wall_1080_body": (4.25, None),
    "building__overhead_1080_body": (6.29, 122.6),
    "surge__overhead_1080_body": (6.76, 18.2),
    "platform__overhead_1080_body": (4.53, None),
    "building__pole_720_body": (5.92, 153.6),
    "surge__pole_720_body": (5.90, 33.2),
    "platform__pole_720_body": (4.68, None),
    "building__pole_1080_head": (6.92, 122.2),
    "surge__pole_1080_head": (7.24, 19.2),
    "platform__pole_1080_head": (4.21, None),
}


def run_variant(name: str, cfg) -> dict:
    d = VARIANTS / name
    calib_file = d / ("calibration_head.json" if (d / "calibration_head.json").exists() else "calibration.json")
    calib = Calibration.load(calib_file)
    eng = Engine(cfg, calib)
    adapter = Adapter(AdapterOptions())
    peak = 0.0
    first = {"AMBER": None, "RED": None}
    first_turb = None
    n_snap = 0
    started = time.perf_counter()
    frames = 0
    for fr in jsonl_source(d / "detections_ds_minimal.jsonl.gz", adapter):
        frames += 1
        snap = eng.process(fr)
        if snap is None:
            continue
        n_snap += 1
        peak = max(peak, snap.summary["density"]["max"])
        st = snap.assessment["status"]
        t_scn = snap.t  # the reference reports normalised stream time (first frame t = 0)
        for lvl in ("AMBER", "RED"):
            if first[lvl] is None and (st == lvl or (lvl == "AMBER" and st == "RED")):
                first[lvl] = round(t_scn, 1)
        if first_turb is None and snap.assessment["turb_on"]:
            first_turb = round(t_scn, 1)
    elapsed = time.perf_counter() - started
    return {"name": name, "frames": frames, "snapshots": n_snap, "peak_density": round(peak, 2),
            "first_amber": first["AMBER"], "first_red": first["RED"], "first_turbulence": first_turb,
            "seconds": round(elapsed, 1), "frames_per_s": round(frames / elapsed, 0) if elapsed > 0 else None}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--only")
    ap.add_argument("--min-density", type=float, help="override pressure.min_density")
    ap.add_argument("--json", help="write results to this JSON file")
    args = ap.parse_args()
    cfg = load_config()
    if args.min_density is not None:
        cfg = replace(cfg, pressure=replace(cfg.pressure, min_density=args.min_density))
    names = [args.only] if args.only else list(REFERENCE_RAW)
    rows = []
    print(f"{'variant':32s} {'peak ref':>8s} {'peak':>6s} {'err%':>6s} {'RED ref':>8s} {'RED':>6s}  amber  turb  fps")
    for name in names:
        r = run_variant(name, cfg)
        ref_peak, ref_red = REFERENCE_RAW.get(name, (None, None))
        r["ref_peak_density"], r["ref_first_red"] = ref_peak, ref_red
        err = 100.0 * (r["peak_density"] - ref_peak) / ref_peak if ref_peak else float("nan")
        r["peak_err_pct"] = round(err, 1)
        rows.append(r)
        print(f"{name:32s} {ref_peak:8.2f} {r['peak_density']:6.2f} {err:6.1f} {str(ref_red):>8s} "
              f"{str(r['first_red']):>6s}  {str(r['first_amber']):>5s} {str(r['first_turbulence']):>5s} "
              f"{r['frames_per_s']}")
        sys.stdout.flush()
    if args.json:
        Path(args.json).write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
