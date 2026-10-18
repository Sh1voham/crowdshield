"""Regenerate docs/RESULTS.md from files and runs. No hand-typed numbers: every section names
its input file, and a missing input leaves a [FILL: ...] line instead of a number.

    python -m crowdshield.make_results
"""
from __future__ import annotations

import json
import math
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np

from .assess import Assessor
from .config import REPO_ROOT, load_config
from .engine import Engine
from .geometry import Calibration
from .io_pipegen import Adapter, Det, DetFrame, jsonl_source

ROOT = REPO_ROOT
OUT = ROOT / "docs" / "RESULTS.md"


def _load(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def section_validation(lines):
    rows = _load(ROOT / "docs" / "validation_variants.json")
    lines += ["## 1. Engine against the reference engine (SYNTHETIC data)", "",
              "Input: `docs/validation_variants.json`, written by `python tools/validate_variants.py --json docs/validation_variants.json`. "
              "Reference values: `VALIDATION.md` section 3, `raw` rows (no recall correction). The data are synthetic: they test the formulas and the code, not real crowds.", ""]
    if not rows:
        lines += ["[FILL: run `make validate`]", ""]
        return
    lines += ["| Dataset | Peak density ref | Peak density ours | First Critical ref (s) | First Critical ours (s) | Frames/s |", "|---|---|---|---|---|---|"]
    for r in rows:
        lines.append(f"| {r['name']} | {r['ref_peak_density']} | {r['peak_density']} | {r['ref_first_red'] if r['ref_first_red'] is not None else '—'} "
                     f"| {r['first_red'] if r['first_red'] is not None else '—'} | {r['frames_per_s']} |")
    worst = max(abs(r["peak_err_pct"]) for r in rows)
    dt = [abs(r["first_red"] - r["ref_first_red"]) for r in rows if r["first_red"] is not None and r["ref_first_red"] is not None]
    same_none = all((r["first_red"] is None) == (r["ref_first_red"] is None) for r in rows)
    lines += ["", f"Largest peak-density difference: {worst:.1f} %. Largest first-Critical difference: {max(dt) if dt else 0:.1f} s. "
              f"Datasets that never reach Critical agree: {'yes' if same_none else 'no'}.", ""]


def section_parity(lines):
    path = ROOT / "tools" / "assessor_parity.jsonl"
    lines += ["## 2. Browser and engine assessors agree", "",
              f"Input: `{path.relative_to(ROOT).as_posix()}` (recorded from the browser simulator's Assessor at 5 Hz by `node tools/make_parity.mjs`).", ""]
    if not path.exists():
        lines += ["[FILL: run `make parity`]", ""]
        return
    rows = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    cfg = load_config()
    a, run, bad = None, None, 0
    for r in rows:
        if r["run"] != run:
            run, a = r["run"], Assessor.from_config(cfg)
        o = a.step(r["t"], r["dmax"], r["pidx"])
        ev = [f"{e['level']}:{e['kind']}" for e in o["events"]]
        num_ok = all((o[k] is None and r[k] is None) or (o[k] is not None and r[k] is not None and math.isclose(o[k], r[k], rel_tol=1e-6, abs_tol=1e-9))
                     for k in ("trend_per_min", "t_amber_s", "t_red_s", "risk"))
        if o["status"] != r["status"] or o["turb_on"] != r["turb_on"] or ev != r["events"] or not num_ok:
            bad += 1
    lines += [f"{len(rows)} rows, {bad} mismatches.", ""]


def section_throughput(lines):
    cfg = load_config()
    cal = Calibration.from_dict({"image_points": [[0, 0], [1920, 0], [1920, 1080], [0, 1080]],
                                 "world_points": [[0, 0], [30, 0], [30, 20], [0, 20]],
                                 "frame_size": [1920, 1080], "area": {"width_m": 30, "height_m": 20}})
    eng = Engine(cfg, cal)
    rng = np.random.default_rng(1)
    pos = rng.random((600, 2)) * [1900, 1060]
    n_upd, t0 = 0, time.perf_counter()
    for k in range(200):
        pos += rng.normal(0, 2, pos.shape)
        dets = [Det(i, x - 8, y - 40, x + 8, y, 0.9, "person") for i, (x, y) in enumerate(pos)]
        if eng.process(DetFrame(t=k * 0.1, frame=k, width=1920, height=1080, dets=dets)) is not None:
            n_upd += 1
    el = time.perf_counter() - t0
    lines += ["## 3. Engine throughput", "",
              f"Measured now by `crowdshield/make_results.py`: 600 tracked people, 200 frames at 10 fps, field updates at 5 Hz, one CPU core: "
              f"{n_upd} field updates in {el:.2f} s = **{n_upd / el:.1f} updates/s** ({200 / el:.0f} frames/s). Requirement: at least 10 updates/s.", ""]


def section_pipegen(lines):
    lines += ["## 4. Real PipeGen output", ""]
    samples = sorted((ROOT / "samples").glob("pipegen_*"))
    if samples:
        lines += ["| Sample | Layout detected | Frames parsed | Detections | With tracker ID |", "|---|---|---|---|---|"]
        for p in samples:
            fr = [f for f in jsonl_source(p, Adapter()) if f is not None]
            dets = sum(len(f.dets) for f in fr)
            ids = sum(1 for f in fr for d in f.dets if d.track_id is not None)
            lines.append(f"| `{p.relative_to(ROOT).as_posix()}` | {fr[0].layout if fr else '—'} | {len(fr)} | {dets} | {ids} |")
        lines.append("")
    recs = sorted((ROOT / "recordings").glob("*.jsonl"))
    tq = sorted((ROOT / "eval").glob("track_quality_*.json"))
    if tq:
        lines += ["Tracking quality (`tools/track_quality.py`):", "",
                  "| Input | Duration (s) | Tracks | Median track (s) | Tracks < 1 s | p99 speed (m/s) | Speed sanity |", "|---|---|---|---|---|---|---|"]
        for p in tq:
            r = _load(p) or {}
            lines.append(f"| `{p.relative_to(ROOT).as_posix()}` ({r.get('recording')}) | {r.get('duration_s')} | {r.get('tracks')} | {r.get('median_track_s')} "
                         f"| {r.get('short_track_fraction')} | {r.get('speed_p99_mps')} | {'pass' if r.get('speed_sanity_pass') else 'fail'} |")
        lines.append("")
    if not samples and not recs:
        lines += ["[FILL: run PipeGen's pipeline (`scripts/pipegen_ctl.sh run-video`) and record it (`python tools/record.py NAME`)]", ""]


def section_pipegen_stages(lines):
    """Facts from PipeGen's own model-stage log in the exported project (never typed by hand)."""
    log = ROOT / "pipegen_export" / "model_stage" / "ModelStage.log"
    lines += ["## 0. The PipeGen pipeline", ""]
    if not log.exists():
        lines += ["[FILL: run `scripts/pipegen_ctl.sh export` after compiling in PipeGen]", ""]
        return
    run_sh = (ROOT / "pipegen_export" / "pipeline" / "deepstream" / "run.sh").read_text(encoding="utf-8")
    elems = [e for e in ("nvinfer_backport", "nvtracker", "MetaJson", "MqttPublish", "nvdsosd") if e in run_sh]
    lines += [f"Input: `{log.relative_to(ROOT).as_posix()}` and `pipegen_export/pipeline/deepstream/run.sh`, both copied from PipeGen's project by "
              "`scripts/pipegen_ctl.sh export`.", "",
              f"DeepStream elements in the generated app: {', '.join(elems)}.", "",
              "| UTC | Step | Outcome | PipeGen's record |", "|---|---|---|---|"]
    for line in log.read_text(encoding="utf-8").splitlines():
        if line.startswith("#") or not line.strip():
            continue
        parts = line.split(None, 3)
        if len(parts) == 4 and parts[1] in ("visualize", "compile", "accuracy", "engine", "benchmark") and parts[2] != "start":
            lines.append(f"| {parts[0]} | {parts[1]} | {parts[2]} | {parts[3]} |")
    lines.append("")


def section_eval(lines):
    lines += ["## 5. Calibration on the real camera", ""]
    rec = _load(ROOT / "eval" / "recall.json")
    if rec:
        lines += [f"Detector recall from hand counts (`eval/recall.json`, from `eval/counts.csv`): {rec['frames']} frames, count MAE {rec['count_mae']}, "
                  f"overall recall {rec['overall_recall']}, recall curve {rec['recall_curve']}.", ""]
    else:
        lines += ["Recall: not measured yet. It needs about 30 hand-counted frames from the target camera (`python tools/count_frames.py prepare`, then `fit`); until then no recall correction is applied.", ""]
    pc = _load(ROOT / "eval" / "pressure_calibration.json")
    if pc:
        lines += [f"Pressure threshold (`eval/pressure_calibration.json`): calm recording `{pc['recording']}` ({pc['duration_s']} s), "
                  f"P99 = {pc['p99']:.5f} s⁻², proposed P_thr = {pc['proposed_p_thr']:.5f} s⁻², alerts on the calm replay: {pc['calm_replay_alerts']}.", ""]
    else:
        lines += ["Pressure threshold: not calibrated yet. The literature default (0.02 s⁻²) is used; calibrating needs 5 minutes of calm movement from the target camera (`python tools/calibrate_pressure.py`).", ""]
    pr = _load(ROOT / "eval" / "precision.json")
    lines += ["## 6. FP16 and INT8", ""]
    if pr:
        lines += ["Input: `eval/precision.json` (`tools/compare_precision.py`). Speed, latency and power come only from PipeGen's speed reports.", "",
                  "| Precision | Mean detections/frame | Mean people in area | Count MAE | Alerts on the clip | PipeGen speed report |", "|---|---|---|---|---|---|"]
        for k in ("fp16", "int8"):
            r = pr.get(k, {})
            lines.append(f"| {k.upper()} | {r.get('mean_detections')} | {r.get('mean_in_area')} | {r.get('count_mae') if r.get('count_mae') is not None else '—'} "
                         f"| {', '.join(r.get('alerts') or []) or 'none'} | {json.dumps(r.get('pipegen_speed_report'))} |")
        lines.append("")
    else:
        lines += ["Not measured: PipeGen's INT8 build was not completed (the free credits ran out during quantization), so only the FP16 engine exists. With an INT8 engine, record the same clip with both and run `tools/compare_precision.py`.", ""]


def main() -> int:
    lines = ["# CrowdShield results", "",
             f"Generated by `python -m crowdshield.make_results` on {datetime.now().strftime('%Y-%m-%d %H:%M')}. "
             "Every number below comes from the file or run named in its section. Synthetic and simulated data are labelled as such; "
             "they test the code and the formulas, not real crowds.", ""]
    for fn in (section_pipegen_stages, section_validation, section_parity, section_throughput, section_pipegen, section_eval):
        try:
            fn(lines)
        except Exception as e:  # noqa: BLE001 - one missing input must not hide the others
            lines += [f"(section failed: {e})", ""]
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
