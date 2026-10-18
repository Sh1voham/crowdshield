"""CrowdShield runner: PipeGen detections in, MQTT state/status/alerts/actuator out.

    python -m crowdshield.run --source mqtt://localhost:1883/crowdshield/detections \
        --calibration config/calibration.json
    python -m crowdshield.run --source jsonl:data/synthetic/variants/building__pole_720_body/detections_ds_minimal.jsonl.gz \
        --calibration data/synthetic/variants/building__pole_720_body/calibration.json --realtime --source-label synthetic

Sources: mqtt://HOST:PORT/TOPIC | tcp://HOST:PORT (PipeGen VMS) | tail:PATH (PipeGen metadata.json)
         | jsonl:PATH | folder:PATH | stdin
"""
from __future__ import annotations

import argparse
import json
import logging
import signal
import sys
import threading
import time
from pathlib import Path
from typing import Optional

from .config import load_config
from .engine import Engine
from .geometry import Calibration
from .io_pipegen import Adapter, AdapterOptions, DetFrame, open_source
from .publish import JsonlSink, MqttSink, Publisher

log = logging.getLogger("crowdshield.run")


def record_line(fr: DetFrame) -> str:
    return json.dumps({
        "t": round(fr.t, 4), "frame": fr.frame, "width": fr.width, "height": fr.height,
        "abs_ts": fr.abs_ts,
        "objects": [{"track_id": d.track_id, "bbox": [round(d.x1, 1), round(d.y1, 1), round(d.x2, 1), round(d.y2, 1)],
                     "conf": d.conf, "label": d.label} for d in fr.dets],
    }, separators=(",", ":"))


def parse_args(argv=None):
    ap = argparse.ArgumentParser(prog="python -m crowdshield.run", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", required=True)
    ap.add_argument("--calibration", default="config/calibration.json")
    ap.add_argument("--config", default=None)
    ap.add_argument("--profile", default="default", choices=["default", "demo"])
    ap.add_argument("--broker", default="localhost:1883", help="HOST:PORT, or 'none' to disable MQTT")
    ap.add_argument("--topic-prefix", default="crowdshield")
    ap.add_argument("--zone", default=None)
    ap.add_argument("--source-label", default=None, choices=["live", "video", "replay", "synthetic"],
                    help="how displays must label this data (default: live, or replay for file sources)")
    ap.add_argument("--emit-jsonl", default=None, help="mirror every published message to PATH or '-'")
    ap.add_argument("--realtime", action="store_true", help="pace file replays by frame time")
    ap.add_argument("--speed", type=float, default=1.0, help="replay speed factor with --realtime")
    ap.add_argument("--ts-unit", default=None, choices=["auto", "s", "ms", "us", "ns"])
    ap.add_argument("--coords", default=None, choices=["auto", "px", "norm"])
    ap.add_argument("--frame-size", default=None, help="WxH for normalised coordinates")
    ap.add_argument("--record", default=None, help="save the raw DetFrames as JSONL")
    ap.add_argument("--incidents", default="incidents.jsonl")
    ap.add_argument("--start-wallclock", type=float, default=None,
                    help="epoch seconds for t=0 (replays); makes the output deterministic")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--log-level", default="INFO")
    return ap.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    logging.basicConfig(level=getattr(logging, args.log_level.upper(), logging.INFO),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s", stream=sys.stderr)
    cfg = load_config(args.config, args.profile)
    calib_path = Path(args.calibration)
    calib = Calibration.load(calib_path)
    calib_mtime = calib_path.stat().st_mtime

    opts = AdapterOptions(labels=tuple(cfg.adapter.labels), ts_unit=args.ts_unit or cfg.adapter.ts_unit,
                          coords=args.coords or cfg.adapter.coords, default_fps=cfg.adapter.default_fps,
                          max_backstep_s=cfg.adapter.max_backstep_s)
    if args.frame_size:
        w, _, h = args.frame_size.lower().partition("x")
        opts.frame_size = (int(w), int(h))
    elif calib.frame_size:
        opts.frame_size = tuple(calib.frame_size)
    adapter = Adapter(opts)

    file_source = args.source.startswith(("jsonl:", "folder:", "stdin"))
    source_label = args.source_label or ("replay" if file_source else "live")
    deterministic = args.start_wallclock is not None

    sinks = []
    if args.broker and args.broker.lower() != "none":
        host, _, port = args.broker.partition(":")
        sinks.append(MqttSink(host or "localhost", int(port or 1883), args.topic_prefix))
    if args.emit_jsonl:
        sinks.append(JsonlSink(args.emit_jsonl))
    engine = Engine(cfg, calib)
    pub = Publisher(cfg, calib, sinks, prefix=args.topic_prefix, zone=args.zone, source=source_label,
                    incidents_path=args.incidents or None, start_wallclock=args.start_wallclock,
                    deterministic=deterministic)
    recorder = open(args.record, "a", encoding="utf-8") if args.record else None

    stop = threading.Event()

    def _stop(*_):
        stop.set()

    signal.signal(signal.SIGINT, _stop)
    try:
        signal.signal(signal.SIGTERM, _stop)
    except (AttributeError, ValueError):
        pass

    log.info("CrowdShield engine: source=%s label=%s zone=%r profile=%s area=%.1fx%.1f m",
             args.source, source_label, pub.zone, cfg.profile, calib.width_m, calib.height_m)
    src = open_source(args.source, adapter, realtime=args.realtime, speed=args.speed, stop=stop)
    last_print_t: Optional[float] = None
    last_calib_check = time.monotonic()
    last_snap = None
    try:
        for item in src:
            if stop.is_set():
                break
            now = time.time()
            if time.monotonic() - last_calib_check > 2.0:
                last_calib_check = time.monotonic()
                try:
                    m = calib_path.stat().st_mtime
                    if m != calib_mtime:
                        calib_mtime = m
                        calib = Calibration.load(calib_path)
                        engine.set_calibration(calib)
                        pub.set_calibration(calib)
                        log.info("calibration reloaded from %s", calib_path)
                except (OSError, ValueError) as e:
                    log.warning("calibration reload failed: %s", e)
            if item is None:
                pub.tick(now)
                continue
            fr = item
            if recorder is not None:
                recorder.write(record_line(fr) + "\n")
            try:
                snap = engine.process(fr)
                pub.tracker_kind = "fallback" if engine.use_fallback_tracker else "pipegen"
                pub.on_frame(now, fr.abs_ts, fr.layout)
                if snap is not None:
                    pub.on_snapshot(snap, now)
                    last_snap = snap
            except Exception:  # noqa: BLE001 - one bad frame must not stop the engine
                log.exception("error processing frame at t=%.3f", fr.t)
                continue
            pub.tick(now)
            if not args.quiet and last_snap is not None and (last_print_t is None or fr.t - last_print_t >= 1.0):
                last_print_t = fr.t
                a = last_snap.assessment
                fc = a["t_red_s"]
                print(f"t={last_snap.t:7.1f}s fps={last_snap.fps or 0:5.1f} people={last_snap.count:4d} "
                      f"dmax={last_snap.summary['density']['max']:5.2f} pidx={last_snap.pidx:5.2f} "
                      f"status={a['status']:5s} forecast={'—' if fc is None else f'red in {fc:.0f}s'}",
                      file=sys.stderr, flush=True)
    finally:
        if recorder is not None:
            recorder.close()
        pub.close()
    log.info("done: %d frames, adapter stats %s", engine.frames_processed, adapter.stats)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
