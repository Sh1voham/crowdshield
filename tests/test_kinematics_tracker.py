import numpy as np

from crowdshield.geometry import Calibration
from crowdshield.io_pipegen import Adapter, jsonl_source
from crowdshield.kinematics import Kinematics
from crowdshield.tracker import NNTracker

from cs_paths import VARIANTS


def test_velocity_constant_motion():
    k = Kinematics(min_dt_s=1.0, ema_tau_s=1.0, min_age_s=0.5)
    v = None
    for i in range(40):
        t = i * 0.1
        (v,) = k.update(t, [7], [(1.2 * t, 0.5 - 0.3 * t)])
        if t < 0.5 - 1e-9:
            assert v is None
    assert abs(v[0] - 1.2) < 1e-9 and abs(v[1] + 0.3) < 1e-9


def test_track_forgotten_after_gap():
    k = Kinematics(max_age_s=1.0)
    k.update(0.0, [1], [(0, 0)])
    k.update(2.0, [2], [(5, 5)])
    assert 1 not in k.tracks


def test_tracker_keeps_ids_for_walkers():
    tr = NNTracker(gate_m=1.0, max_age_s=1.0)
    first = None
    for i in range(50):
        t = i * 0.1
        pts = np.array([[1.3 * t, 1.0], [10 - 1.3 * t, 3.0], [5.0, 5.0 + 0.5 * t]])
        ids = tr.update(t, pts)
        if first is None:
            first = ids.copy()
        assert (ids == first).all()


def test_tracker_continuity_on_synthetic_without_ids():
    """Upstream IDs removed: fallback tracker keeps >= 99% of frame-to-frame continuations
    on the building variant (reference: 99.9% on building, VALIDATION.md section 4)."""
    d = VARIANTS / "building__pole_720_body"
    calib = Calibration.load(d / "calibration.json")
    tr = NNTracker(1.0, 1.0)
    prev = {}
    checked = correct = 0
    for fr in jsonl_source(d / "detections_ds_minimal.jsonl.gz", Adapter()):
        if fr.t > 30.0:
            break
        boxes = np.array([[x.x1, x.y1, x.x2, x.y2] for x in fr.dets])
        true_ids = [x.track_id for x in fr.dets]
        xy, keep = calib.to_floor(boxes, fr.width, fr.height)
        xy = xy[keep]
        true_ids = [i for i, kk in zip(true_ids, keep) if kk]
        ids = tr.update(fr.t, xy)
        cur = dict(zip(true_ids, ids))
        for tid, mine in cur.items():
            if tid in prev:
                checked += 1
                correct += prev[tid] == mine
        prev = cur
    assert checked > 5000
    assert correct / checked >= 0.99
