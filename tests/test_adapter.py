import json
import threading

import pytest

from crowdshield.io_pipegen import (Adapter, AdapterOptions, auto_parse, decode_line, folder_source,
                                    jsonl_source, tail_jsonl)

from cs_paths import ROOT, VARIANTS

SAMPLE_DIR = VARIANTS / "building__pole_720_body" / "samples"


def frames_of(path, **kw):
    return list(jsonl_source(path, Adapter(AdapterOptions(**kw))))


# --- PipeGen 0.5.1 meta_json layout (see docs/DATA_CONTRACT.md "Input") -------------------
PIPEGEN_FRAME = {"frame": 7, "source_id": 0, "pts_ns": 233333333, "width": 1280, "height": 720,
                 "objects": [{"id": 12, "class_id": 0, "label": "person", "confidence": 0.81,
                              "bbox": {"left": 100.0, "top": 50.0, "width": 40.0, "height": 120.0},
                              "tracker_confidence": 0.9},
                             {"id": None, "class_id": 0, "label": "person", "confidence": 0.5,
                              "bbox": {"left": 300.0, "top": 60.0, "width": 30.0, "height": 90.0}},
                             {"id": 13, "class_id": 2, "label": "car", "confidence": 0.9,
                              "bbox": {"left": 500.0, "top": 60.0, "width": 80.0, "height": 40.0}}]}


def test_pipegen_frame_layout():
    fr = auto_parse(PIPEGEN_FRAME)
    assert fr.layout == "pipegen_frame"
    assert len(fr.dets) == 2  # the car is filtered by the label allow-list
    d = fr.dets[0]
    assert (d.track_id, d.x1, d.y1, d.x2, d.y2, d.conf) == (12, 100.0, 50.0, 140.0, 170.0, 0.81)
    assert fr.dets[1].track_id is None
    assert (fr.width, fr.height, fr.frame) == (1280, 720, 7)


def test_pipegen_metadata_json_document_lines(tmp_path):
    """meta_json writes '{', header lines, then one frame per line with a trailing comma."""
    frames = []
    for k in range(5):
        f = dict(PIPEGEN_FRAME, frame=k, pts_ns=k * 33_333_333)
        frames.append(json.dumps(f))
    text = '{\n  "pipeline": "video_json_export",\n  "source": "",\n  "min_confidence": 0.000,\n  "frames": ['
    text += ",".join("\n    " + f for f in frames) + "\n  ]\n}\n"
    p = tmp_path / "metadata.json"
    p.write_text(text)
    out = frames_of(p)
    assert len(out) == 5
    assert out[0].t == 0.0 and out[4].t == pytest.approx(4 * 0.033333333)


def test_pipegen_whole_document_single_line(tmp_path):
    doc = {"pipeline": "video_json_export", "frames": [dict(PIPEGEN_FRAME, frame=k, pts_ns=k * 40_000_000)
                                                       for k in range(3)]}
    p = tmp_path / "m.json"
    p.write_text(json.dumps(doc))
    out = frames_of(p)
    assert [round(f.t, 3) for f in out] == [0.0, 0.04, 0.08]


def test_pipegen_shapes_and_vms_layouts():
    shapes = {"frame": 0, "pts_ns": 0, "width": 1280, "height": 720, "objects": [],
              "shapes": [{"kind": "skeleton", "points": [[1, 2]], "id": 1},
                         {"kind": "box", "x": 340.5, "y": 4.8, "w": 244.0, "h": 708.8, "label": "person",
                          "id": 1, "score": 0.93}]}
    fr = auto_parse(shapes)
    assert fr.layout == "pipegen_frame" and len(fr.dets) == 1
    assert fr.dets[0].x2 == pytest.approx(584.5)
    a = Adapter()
    vms = [{"t": 1792310400.0 + k * 0.2, "cam": 0, "frame": k * 5,
            "shapes": [{"kind": "box", "x": 10, "y": 20, "w": 30, "h": 90, "id": 4, "score": 0.8, "label": "person"}]}
           for k in range(3)]
    out = [f for v in vms for f in a.feed(v)]
    assert out[0].layout == "pipegen_vms"
    assert [round(f.t, 2) for f in out] == [0.0, 0.2, 0.4]
    assert out[0].abs_ts == pytest.approx(1792310400.0)


def test_ds_minimal_full_file():
    out = frames_of(VARIANTS / "building__pole_720_body" / "detections_ds_minimal.jsonl.gz")
    assert len(out) == 1800
    assert out[0].layout == "ds_minimal"
    assert out[0].t == 0.0 and out[-1].t == pytest.approx(179.9)
    assert all(d.label == "person" for d in out[0].dets)
    assert out[0].dets[0].track_id == 1000


@pytest.mark.parametrize("name,layout", [("ds_full", "ds_full"), ("generic", "generic"),
                                         ("normalized", "generic")])
def test_sample_layouts(name, layout):
    out = frames_of(SAMPLE_DIR / f"{name}.jsonl", frame_size=(1280, 720))
    assert len(out) == 30, name
    assert out[0].layout == layout
    assert out[1].t == pytest.approx(0.1, abs=1e-6)
    ref = frames_of(SAMPLE_DIR / "generic.jsonl")
    # every layout yields the same boxes as the generic pixel layout (normalised: 5 decimals)
    a = sorted((d.track_id, d.x1, d.y2) for d in out[5].dets)
    b = sorted((d.track_id, d.x1, d.y2) for d in ref[5].dets)
    assert [x[0] for x in a] == [x[0] for x in b]
    assert max(max(abs(p[1] - q[1]), abs(p[2] - q[2])) for p, q in zip(a, b)) < 0.1


def test_every_sample_in_every_variant_parses():
    for vd in VARIANTS.iterdir():
        size = tuple(json.loads((vd / "calibration.json").read_text())["frame_size"])
        for s in (vd / "samples").glob("*.jsonl"):
            out = frames_of(s, frame_size=size)
            assert len(out) == 30, s
            assert sum(len(f.dets) for f in out) > 0, s


@pytest.mark.parametrize("unit,factor", [("s", 1.0), ("ms", 1e3), ("us", 1e6), ("ns", 1e9)])
def test_timestamp_unit_inference(unit, factor):
    a = Adapter()
    out = []
    for k in range(5):
        msg = {"timestamp": 1000 * factor + k * 0.04 * factor,
               "objects": [{"id": 1, "bbox": [10, 10, 20, 50]}]}
        out += a.feed(msg)
    assert [round(f.t, 4) for f in out] == [0.0, 0.04, 0.08, 0.12, 0.16]


def test_frame_number_fallback_and_iso():
    a = Adapter(AdapterOptions(default_fps=25))
    out = [f for k in range(3) for f in a.feed({"frame": 10 + k, "objects": [{"id": 1, "bbox": [1, 2, 3, 4]}]})]
    assert [round(f.t, 3) for f in out] == [0.0, 0.04, 0.08]
    b = Adapter()
    out = b.feed({"@timestamp": "2026-10-18T10:00:00.500Z", "objects": ["1|1|2|3|4|person"]})
    assert out[0].abs_ts == pytest.approx(1792317600.5)


def test_backwards_frames_dropped_and_jitter_clamped():
    a = Adapter()
    def m(ts):
        return {"ts": ts, "objects": [{"id": 1, "bbox": [1, 2, 3, 4]}]}
    seq = [0.0, 0.1, 0.2, 0.15, 5.0, 2.0, 5.1]
    out = [f for ts in seq for f in a.feed(m(ts))]
    ts = [round(f.t, 2) for f in out]
    assert ts == [0.0, 0.1, 0.2, 0.2, 5.0, 5.1]  # 0.15 clamped, 2.0 dropped (back 3 s)
    assert a.stats["dropped_backwards"] == 1


def test_normalised_coords_need_size():
    msg = {"frame": 1, "objects": [{"id": 1, "bbox": [0.1, 0.2, 0.05, 0.2]}]}
    fr = Adapter(AdapterOptions(frame_size=(1000, 500))).feed(msg)[0]
    assert (fr.dets[0].x1, fr.dets[0].y1, fr.dets[0].x2, fr.dets[0].y2) == pytest.approx((100, 100, 150, 200))


def test_decode_line_variants():
    assert decode_line("") is None
    assert decode_line("  ]") is None
    assert decode_line('    {"frame": 1},') == {"frame": 1}
    assert decode_line('{"topic": "crowdshield/detections", "payload": {"a": 1}}') == {"a": 1}


def test_folder_source(tmp_path):
    for k in range(3):
        (tmp_path / f"frame_{k:05d}.json").write_text(json.dumps(dict(PIPEGEN_FRAME, frame=k, pts_ns=k * 10**8)))
    out = [f for f in folder_source(tmp_path, follow=False) if f is not None]
    assert [f.frame for f in out] == [0, 1, 2]


def test_tail_follows_and_detects_truncation(tmp_path):
    p = tmp_path / "metadata.json"
    stop = threading.Event()
    lines = [json.dumps(dict(PIPEGEN_FRAME, frame=k, pts_ns=k * 10**8)) for k in range(4)]
    p.write_text("{\n" + "\n".join(l + "," for l in lines[:2]) + "\n")
    it = tail_jsonl(p, stop=stop)
    got = []
    for n, item in enumerate(it):
        if item is not None:
            got.append(item)
        if len(got) == 2 or n > 500:
            break
    # rewritten from scratch with LONGER content (as when PipeGen restarts and reopens the file)
    p.write_text("{\n" + "\n".join(l + "," for l in lines[2:]) + "\n")
    for n, item in enumerate(it):
        if item is not None:
            got.append(item)
        if len(got) == 4 or n > 500:
            break
    stop.set()
    assert [f.frame for f in got] == [0, 1, 2, 3]
    assert got[2].t == 0.0  # time base reset after the producer restarted


def test_real_pipegen_sample_if_present():
    """Parses every real PipeGen capture in samples/ (added once PipeGen runs)."""
    for p in sorted((ROOT / "samples").glob("pipegen_*")):
        out = [f for f in jsonl_source(p, Adapter()) if f is not None]
        assert out, f"no frames parsed from {p}"
