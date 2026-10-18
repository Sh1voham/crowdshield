"""End-to-end engine checks on SYNTHETIC data: validation numbers, schema, determinism, throughput."""
import json
import subprocess
import sys
import time

import numpy as np
import pytest

from crowdshield.config import load_config
from crowdshield.engine import Engine
from crowdshield.geometry import Calibration
from crowdshield.io_pipegen import Adapter, DetFrame, Det, jsonl_source
from crowdshield.publish import Publisher, Sink
from crowdshield.schema import validate

from cs_paths import ROOT, VARIANTS


class ListSink(Sink):
    def __init__(self):
        self.msgs = []

    def publish(self, topic, payload, qos=0, retain=False):
        self.msgs.append((topic, payload))


def run_variant(name):
    d = VARIANTS / name
    cf = d / ("calibration_head.json" if (d / "calibration_head.json").exists() else "calibration.json")
    eng = Engine(load_config(), Calibration.load(cf))
    peak, first_red = 0.0, None
    for fr in jsonl_source(d / "detections_ds_minimal.jsonl.gz", Adapter()):
        s = eng.process(fr)
        if s is None:
            continue
        peak = max(peak, s.summary["density"]["max"])
        if first_red is None and s.assessment["status"] == "RED":
            first_red = s.t
    return peak, first_red


# Reference engine, VALIDATION.md section 3, `raw` rows (no recall correction)
@pytest.mark.parametrize("name,ref_peak,ref_red", [
    ("building__pole_720_body", 5.92, 153.6),
    ("surge__pole_1080_head", 7.24, 19.2),
    ("platform__low_wall_1080_body", 4.25, None),
])
def test_matches_reference_validation(name, ref_peak, ref_red):
    peak, red = run_variant(name)
    assert peak == pytest.approx(ref_peak, rel=0.05)
    if ref_red is None:
        assert red is None
    else:
        assert red == pytest.approx(ref_red, abs=1.0)


def test_published_messages_validate_against_contract():
    d = VARIANTS / "surge__pole_720_body"
    calib = Calibration.load(d / "calibration.json")
    cfg = load_config()
    sink = ListSink()
    pub = Publisher(cfg, calib, [sink], source="synthetic", start_wallclock=1792310400.0, deterministic=True)
    eng = Engine(cfg, calib)
    for fr in jsonl_source(d / "detections_ds_minimal.jsonl.gz", Adapter()):
        if fr.t > 40:
            break
        pub.on_frame(0.0, fr.abs_ts, fr.layout)
        s = eng.process(fr)
        if s is not None:
            pub.on_snapshot(s)
    topics = {t for t, _ in sink.msgs}
    assert {"crowdshield/state", "crowdshield/status", "crowdshield/alerts", "crowdshield/actuator/cmd"} <= topics
    for topic, payload in sink.msgs:
        errs = validate(topic.split("/", 1)[1], payload)
        assert not errs, (topic, errs[:5])
        json.dumps(payload, allow_nan=False)  # no NaN anywhere
    kinds = [p["kind"] for t, p in sink.msgs if t.endswith("alerts")]
    assert "TURBULENCE" in kinds and "PRESSURE" in kinds


def test_replay_is_byte_identical(tmp_path):
    d = VARIANTS / "building__pole_720_body"
    outs = []
    for k in range(2):
        out = tmp_path / f"run{k}.jsonl"
        subprocess.run([sys.executable, "-m", "crowdshield.run",
                        "--source", f"jsonl:{d / 'detections_ds_minimal.jsonl.gz'}",
                        "--calibration", str(d / "calibration.json"), "--broker", "none",
                        "--emit-jsonl", str(out), "--incidents", "", "--start-wallclock", "1792310400",
                        "--quiet", "--log-level", "WARNING"],
                       cwd=ROOT, check=True, timeout=300)
        outs.append(out.read_bytes())
    assert len(outs[0]) > 100_000
    assert outs[0] == outs[1]


def test_throughput_600_people():
    cfg = load_config()
    calib = Calibration.from_dict({
        "image_points": [[0, 0], [1920, 0], [1920, 1080], [0, 1080]],
        "world_points": [[0, 0], [30, 0], [30, 20], [0, 20]],
        "frame_size": [1920, 1080], "area": {"width_m": 30, "height_m": 20}})
    best = 0.0
    for _ in range(3):  # best of up to 3: other processes on a demo laptop add timing noise
        eng = Engine(cfg, calib)
        rng = np.random.default_rng(1)
        pos = rng.random((600, 2)) * [1900, 1060]
        n_upd = 0
        t0 = time.perf_counter()
        for k in range(100):  # 10 s of frames at 10 fps -> 50 field updates
            pos += rng.normal(0, 2, pos.shape)
            dets = [Det(i, x - 8, y - 40, x + 8, y, 0.9, "person") for i, (x, y) in enumerate(pos)]
            if eng.process(DetFrame(t=k * 0.1, frame=k, width=1920, height=1080, dets=dets)) is not None:
                n_upd += 1
        elapsed = time.perf_counter() - t0
        assert n_upd == 50
        best = max(best, n_upd / elapsed)
        print(f"\nthroughput: {n_upd} field updates with 600 people in {elapsed:.2f} s ({n_upd / elapsed:.1f} updates/s)")
        if best >= 10.0:
            break
    assert best >= 10.0


def test_engine_recovers_when_stream_time_restarts():
    """A looping video makes PipeGen rewrite metadata.json and start pts at 0 again."""
    cfg = load_config()
    calib = Calibration.from_dict({"image_points": [[0, 0], [1000, 0], [1000, 500], [0, 500]],
                                   "world_points": [[0, 0], [20, 0], [20, 10], [0, 10]],
                                   "frame_size": [1000, 500], "area": {"width_m": 20, "height_m": 10}})
    eng = Engine(cfg, calib)
    def frame(t, k):
        return DetFrame(t=t, frame=k, width=1000, height=500,
                        dets=[Det(1, 100 + 10 * t, 200, 110 + 10 * t, 240, 0.9, "person")])
    first = [eng.process(frame(k * 0.1, k)) for k in range(100)]
    second = [eng.process(frame(k * 0.1, k)) for k in range(50)]
    assert sum(s is not None for s in first) == 50
    assert sum(s is not None for s in second) == 25   # updates resume at 5 Hz from t = 0
    assert second[0] is not None and second[0].t == 0.0
