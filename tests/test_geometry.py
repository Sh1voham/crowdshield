import json

import numpy as np
import pytest

from crowdshield.geometry import Calibration, apply_h, homography, save_calibration, validate_calibration

from cs_paths import VARIANTS

IMG = [(412, 618), (1508, 604), (1290, 322), (640, 330)]
WLD = [(0, 4), (6, 4), (6, 0), (0, 0)]


def calib_dict(**kw):
    d = {"image_points": [list(p) for p in IMG], "world_points": [list(p) for p in WLD],
         "frame_size": [1920, 1080], "area": {"width_m": 6, "height_m": 4}, "foot_mode": "bbox_bottom"}
    d.update(kw)
    return d


def test_corners_reproject_exactly():
    H = homography(IMG, WLD)
    assert np.abs(apply_h(H, np.array(IMG)) - np.array(WLD)).max() < 1e-6


def test_dossier_interior_point():
    H = homography(IMG, WLD)
    x, y = apply_h(H, np.array([[960, 470]]))[0]
    assert x == pytest.approx(2.98, abs=0.01) and y == pytest.approx(2.53, abs=0.01)


def test_validation_rejects_bad_quads():
    assert validate_calibration(calib_dict()) == []
    dup = calib_dict(image_points=[list(IMG[0]), list(IMG[0]), list(IMG[2]), list(IMG[3])])
    assert any("distinct" in e for e in validate_calibration(dup))
    bowtie = calib_dict(image_points=[list(IMG[0]), list(IMG[2]), list(IMG[1]), list(IMG[3])])
    assert any("convex" in e for e in validate_calibration(bowtie))
    assert validate_calibration(calib_dict(area={"width_m": 0, "height_m": 4}))
    assert validate_calibration(calib_dict(foot_mode="shoes"))


def test_foot_point_and_rescale():
    c = Calibration.from_dict(calib_dict())
    # a box whose bottom-centre is the dossier point, given at 1280x720 (scaled by 2/3)
    box = np.array([[950, 380, 970, 470]]) * (2 / 3)
    xy, keep = c.to_floor(box, 1280, 720)
    assert keep[0]
    assert xy[0, 0] == pytest.approx(2.98, abs=0.02) and xy[0, 1] == pytest.approx(2.53, abs=0.02)


def test_points_outside_area_are_dropped():
    c = Calibration.from_dict(calib_dict())
    far = c.floor_to_image(np.array([[10.0, 2.0]]))[0]  # 4 m right of the 6 m area
    box = np.array([[far[0] - 5, far[1] - 40, far[0] + 5, far[1]]])
    _, keep = c.to_floor(box, 1920, 1080)
    assert not keep[0]


def test_head_offset_moves_foot_down():
    c = Calibration.from_dict(calib_dict(foot_mode="head_offset", head_k=6.5))
    box = np.array([[950, 380, 970, 400]])
    foot = c.foot_pixels(box)[0]
    assert foot[1] == pytest.approx(400 + 6.5 * 20)


def test_variant_calibrations_load():
    for d in VARIANTS.iterdir():
        Calibration.load(d / "calibration.json")
        Calibration.load(d / "calibration_clicked.json")


def test_save_keeps_backup(tmp_path):
    p = tmp_path / "calibration.json"
    save_calibration(p, calib_dict())
    save_calibration(p, calib_dict(zone="Gate B"))
    assert json.loads(p.read_text())["zone"] == "Gate B"
    assert (tmp_path / "calibration.json.bak").exists()
    with pytest.raises(ValueError):
        save_calibration(p, calib_dict(area={"width_m": -1, "height_m": 4}))
