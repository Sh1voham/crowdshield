"""The ONLY module that knows PipeGen's (and DeepStream's) output formats.

Everything else in CrowdShield consumes `DetFrame` / `Det`. The adapter detects the
layout, the timestamp unit and the coordinate mode, logs each choice once, and
normalises time so the first frame is t = 0 and t never runs backwards.

Layouts (see docs/DATA_CONTRACT.md, section "Input"):
  pipegen_frame  PipeGen 0.5.1 meta_json frame: {"frame","pts_ns","width","height",
                 "objects":[{"id","label","confidence","bbox":{left,top,width,height}}],
                 "shapes":[{"kind":"box","x","y","w","h","id","score","label"}]}
  pipegen_doc    {"pipeline": "...", "frames": [pipegen_frame, ...]}   (output/metadata.json)
  pipegen_vms    PipeGen VMS event line: {"t": <epoch s>, "frame", "shapes": [...]}
  ds_minimal     DeepStream nvmsgconv minimal: {"@timestamp","id","objects":["tid|l|t|r|b|label"]}
  ds_full        DeepStream full schema, one object per message (grouped into frames)
  generic        lists under objects/detections/boxes/persons with bbox lists or dicts
"""
from __future__ import annotations

import gzip
import io
import json
import logging
import math
import os
import queue
import socket
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from statistics import median
from typing import Iterable, Iterator, List, Optional

log = logging.getLogger("crowdshield.io")


@dataclass
class Det:
    track_id: Optional[int]
    x1: float
    y1: float
    x2: float
    y2: float
    conf: Optional[float] = None
    label: Optional[str] = None


@dataclass
class DetFrame:
    t: float                      # seconds since the first frame (normalised, monotonic)
    frame: Optional[int]
    width: Optional[int]
    height: Optional[int]
    dets: List[Det] = field(default_factory=list)
    abs_ts: Optional[float] = None  # absolute epoch seconds when the source provides one
    layout: Optional[str] = None


@dataclass
class AdapterOptions:
    ts_unit: str = "auto"          # auto | s | ms | us | ns
    coords: str = "auto"           # auto | px | norm
    frame_size: Optional[tuple] = None  # (W, H) for normalised coordinates
    labels: tuple = ("person", "head", "pedestrian", "0")
    default_fps: float = 10.0
    bbox_format: str = "auto"      # auto | xyxy | xywh  (for generic list bboxes)
    max_backstep_s: float = 1.0


_UNIT_SCALE = {"s": 1.0, "ms": 1e-3, "us": 1e-6, "ns": 1e-9}
_LIST_KEYS = ("objects", "detections", "boxes", "persons", "people", "dets")
_ID_KEYS = ("track_id", "trackingId", "tracking_id", "object_id", "id", "tid")
_CONF_KEYS = ("conf", "confidence", "score", "prob")
_LABEL_KEYS = ("label", "class", "class_name", "obj_label", "type", "category")


def _parse_iso(s: str) -> Optional[float]:
    try:
        s2 = s.strip()
        if s2.endswith("Z"):
            s2 = s2[:-1] + "+00:00"
        return datetime.fromisoformat(s2).timestamp()
    except (ValueError, AttributeError):
        return None


def _to_int(v) -> Optional[int]:
    if v is None or v == "" or v is False:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(f) or f < 0 or f > 2 ** 62:
        return None
    return int(f)


def _to_float(v) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _first(d: dict, keys) -> object:
    for k in keys:
        if k in d and d[k] is not None:
            return d[k]
    return None


class Adapter:
    """Stateful parser: feed decoded JSON objects, get normalised DetFrames out."""

    def __init__(self, opts: Optional[AdapterOptions] = None):
        self.opts = opts or AdapterOptions()
        self._logged: set = set()
        self.reset_stream()
        self.stats = {"frames": 0, "dropped_backwards": 0, "unparsed": 0, "filtered_labels": 0}

    # -- stream / time state -------------------------------------------------------
    def reset_stream(self) -> None:
        """Forget the time base (call when the producer restarts, e.g. truncated file)."""
        self._raw0: Optional[float] = None
        self._raw_kind: Optional[str] = None
        self._scale: Optional[float] = None
        self._pending_deltas: list = []
        self._last_t: Optional[float] = None
        self._last_raw: Optional[float] = None
        self._frame0: Optional[int] = None
        self._coord_mode: Optional[str] = None
        self._bbox_fmt: Optional[str] = None
        self._group_key = None
        self._group: list = []
        self._group_meta: Optional[tuple] = None
        self._layout: Optional[str] = None

    def _log_once(self, key: str, msg: str, *args, level=logging.INFO) -> None:
        if key not in self._logged:
            self._logged.add(key)
            log.log(level, msg, *args)

    # -- public API ---------------------------------------------------------------
    def feed(self, obj) -> List[DetFrame]:
        """Parse one decoded JSON value. Returns zero or more complete frames."""
        if isinstance(obj, list):
            out: List[DetFrame] = []
            for o in obj:
                out.extend(self.feed(o))
            return out
        if not isinstance(obj, dict):
            self.stats["unparsed"] += 1
            return []
        if isinstance(obj.get("frames"), list):
            self._set_layout("pipegen_doc")
            out = []
            for fr in obj["frames"]:
                out.extend(self.feed(fr))
            return out
        if isinstance(obj.get("object"), dict):
            return self._feed_ds_full(obj)
        raw = self._parse_frame(obj)
        if raw is None:
            self.stats["unparsed"] += 1
            self._log_once("unparsed", "adapter: could not recognise a message layout (keys: %s)",
                           sorted(obj)[:12], level=logging.WARNING)
            return []
        frame = self._finish(*raw)
        return [frame] if frame is not None else []

    def flush(self) -> List[DetFrame]:
        """Emit a frame still being grouped (ds_full layout) at end of stream."""
        if self._group_meta is None:
            return []
        meta, dets = self._group_meta, self._group
        self._group_meta, self._group, self._group_key = None, [], None
        frame = self._finish(*meta, dets)
        return [frame] if frame is not None else []

    # -- layouts --------------------------------------------------------------------
    def _set_layout(self, name: str) -> None:
        if self._layout != name:
            self._layout = name
            self._log_once("layout:" + name, "adapter: layout %s", name)

    def _feed_ds_full(self, obj: dict) -> List[DetFrame]:
        self._set_layout("ds_full")
        key = (obj.get("id"), obj.get("@timestamp"))
        ts_raw, ts_kind = self._timestamp(obj)
        frame_no = _to_int(obj.get("id"))
        o = obj["object"]
        det = None
        bb = o.get("bbox") or {}
        x1, y1 = _to_float(bb.get("topleftx")), _to_float(bb.get("toplefty"))
        x2, y2 = _to_float(bb.get("bottomrightx")), _to_float(bb.get("bottomrighty"))
        if None not in (x1, y1, x2, y2):
            label = None
            for cand in ("person", "vehicle", "face", "bag"):
                if cand in o:
                    label = cand
                    break
            det = Det(_to_int(o.get("id")), x1, y1, x2, y2, _to_float(o.get("confidence")), label)
        out: List[DetFrame] = []
        if self._group_key is not None and key != self._group_key:
            out.extend(self.flush())
        if self._group_key is None:
            self._group_key = key
            self._group_meta = (ts_raw, ts_kind, frame_no, None, None, "ds_full")
            self._group = []
        if det is not None:
            self._group.append(det)
        return out

    def _parse_frame(self, obj: dict):
        """Return (ts_raw, ts_kind, frame_no, width, height, layout, dets) or None."""
        width = _to_int(obj.get("width"))
        height = _to_int(obj.get("height"))
        frame_no = _to_int(_first(obj, ("frame", "frame_num", "frame_number", "frameId")))
        dets: Optional[list] = None
        layout = None

        objects = obj.get("objects")
        if isinstance(objects, list) and objects and all(isinstance(s, str) for s in objects):
            layout = "ds_minimal"
            dets = [d for d in (self._parse_minimal(s) for s in objects) if d is not None]
            if frame_no is None:
                frame_no = _to_int(obj.get("id"))
        elif isinstance(obj.get("shapes"), list) and not (isinstance(objects, list) and objects):
            boxes = [s for s in obj["shapes"] if isinstance(s, dict) and s.get("kind") == "box"]
            layout = "pipegen_vms" if "t" in obj or "cam" in obj else "pipegen_frame"
            dets = [self._parse_shape_box(s) for s in boxes]
            dets = [d for d in dets if d is not None]
        else:
            lst = None
            for k in _LIST_KEYS:
                if isinstance(obj.get(k), list):
                    lst = obj[k]
                    break
            if lst is None:
                return None
            if "pts_ns" in obj or (lst and isinstance(lst[0], dict) and isinstance(lst[0].get("bbox"), dict)
                                   and "left" in lst[0]["bbox"]):
                layout = "pipegen_frame"
            else:
                layout = "generic"
            self._decide_bbox_format(lst)
            dets = [d for d in (self._parse_generic(o) for o in lst) if d is not None]
            if frame_no is None and "id" in obj and not isinstance(obj.get("id"), str):
                frame_no = _to_int(obj.get("id"))

        ts_raw, ts_kind = self._timestamp(obj)
        return ts_raw, ts_kind, frame_no, width, height, layout, dets

    def _parse_minimal(self, s: str) -> Optional[Det]:
        parts = s.split("|")
        if len(parts) < 5:
            return None
        nums = [_to_float(p) for p in parts[1:5]]
        if None in nums:
            return None
        label = parts[5].strip() if len(parts) > 5 else None
        conf = _to_float(parts[6]) if len(parts) > 6 else None
        return Det(_to_int(parts[0]), nums[0], nums[1], nums[2], nums[3], conf, label or None)

    def _parse_shape_box(self, s: dict) -> Optional[Det]:
        x, y, w, h = (_to_float(s.get(k)) for k in ("x", "y", "w", "h"))
        if None in (x, y, w, h):
            return None
        return Det(_to_int(s.get("id")), x, y, x + w, y + h, _to_float(s.get("score")), s.get("label"))

    def _decide_bbox_format(self, lst: list) -> None:
        if self._bbox_fmt is not None:
            return
        if self.opts.bbox_format in ("xyxy", "xywh"):
            self._bbox_fmt = self.opts.bbox_format
            return
        boxes = [o.get("bbox") for o in lst if isinstance(o, dict) and isinstance(o.get("bbox"), (list, tuple))]
        boxes = [b for b in boxes if len(b) == 4 and None not in (_to_float(v) for v in b)]
        if not boxes:
            return
        # x1y1x2y2 requires x2 > x1 and y2 > y1 for every box; xywh breaks that often.
        xyxy_ok = all(float(b[2]) > float(b[0]) and float(b[3]) > float(b[1]) for b in boxes)
        self._bbox_fmt = "xyxy" if xyxy_ok else "xywh"
        self._log_once("bbox", "adapter: list bboxes read as %s", self._bbox_fmt)

    def _parse_generic(self, o) -> Optional[Det]:
        if not isinstance(o, dict):
            return None
        bb = o.get("bbox", o.get("box", o.get("rect")))
        x1 = y1 = x2 = y2 = None
        if isinstance(bb, (list, tuple)) and len(bb) == 4:
            a, b, c, d = (_to_float(v) for v in bb)
            if None in (a, b, c, d):
                return None
            if (self._bbox_fmt or "xyxy") == "xyxy":
                x1, y1, x2, y2 = a, b, c, d
            else:
                x1, y1, x2, y2 = a, b, a + c, b + d
        else:
            src = bb if isinstance(bb, dict) else o
            if "left" in src and "top" in src:
                x1, y1 = _to_float(src.get("left")), _to_float(src.get("top"))
                if "width" in src:
                    w, h = _to_float(src.get("width")), _to_float(src.get("height"))
                    x2 = x1 + w if None not in (x1, w) else None
                    y2 = y1 + h if None not in (y1, h) else None
                else:
                    x2, y2 = _to_float(src.get("right")), _to_float(src.get("bottom"))
            elif "x1" in src:
                x1, y1, x2, y2 = (_to_float(src.get(k)) for k in ("x1", "y1", "x2", "y2"))
            elif "topleftx" in src:
                x1, y1, x2, y2 = (_to_float(src.get(k)) for k in
                                  ("topleftx", "toplefty", "bottomrightx", "bottomrighty"))
            elif "x" in src and "w" in src:
                x, y, w, h = (_to_float(src.get(k)) for k in ("x", "y", "w", "h"))
                if None in (x, y, w, h):
                    return None
                x1, y1, x2, y2 = x, y, x + w, y + h
        if None in (x1, y1, x2, y2):
            return None
        tid = _to_int(_first(o, _ID_KEYS))
        conf = _to_float(_first(o, _CONF_KEYS))
        label = _first(o, _LABEL_KEYS)
        if label == "":
            label = None
        if label is None and o.get("class_id") is not None:
            label = str(o["class_id"])
        return Det(tid, x1, y1, x2, y2, conf, str(label) if label is not None else None)

    # -- timestamps -----------------------------------------------------------------
    def _timestamp(self, obj: dict):
        """Return (raw value, kind). kind: 'iso'|'epoch'|'s'|'ns'|'num'|'frame'|None."""
        fields_ = obj.get("fields") if isinstance(obj.get("fields"), dict) else {}
        if "pts_ns" in obj and _to_float(obj["pts_ns"]) is not None:
            return float(obj["pts_ns"]), "ns"
        if "t" in obj and _to_float(obj["t"]) is not None:
            v = float(obj["t"])
            return v, ("epoch" if v > 1e9 and v < 1e11 else "num")
        if "timestamp_s" in fields_ and _to_float(fields_["timestamp_s"]) is not None:
            return float(fields_["timestamp_s"]), "s"
        for k in ("@timestamp", "timestamp", "ts", "time", "pts", "ntp_timestamp", "ntp_ts"):
            if k not in obj or obj[k] is None:
                continue
            v = obj[k]
            if isinstance(v, str):
                iso = _parse_iso(v)
                if iso is not None:
                    return iso, "iso"
                v = _to_float(v)
                if v is None:
                    continue
            v = _to_float(v)
            if v is None:
                continue
            return v, "num"
        return None, None

    def _resolve_scale(self, kind: str, raw: float) -> Optional[float]:
        unit = self.opts.ts_unit
        if kind in ("iso", "epoch", "s"):
            return 1.0
        if kind == "ns":
            return 1e-9 if unit == "auto" else _UNIT_SCALE[unit]
        if unit != "auto":
            return _UNIT_SCALE[unit]
        if self._scale is not None:
            return self._scale
        if self._last_raw is not None and raw != self._last_raw:
            self._pending_deltas.append(abs(raw - self._last_raw))
        if not self._pending_deltas:
            return None
        delta = median(self._pending_deltas[:10])
        best = None
        for name in ("s", "ms", "us", "ns"):
            dt = delta * _UNIT_SCALE[name]
            if 0.002 <= dt <= 2.0:
                best = name
                break
        if best is None:
            best = "s"
        self._log_once("unit", "adapter: timestamp unit inferred as %s (median frame delta %.6g raw)", best, delta)
        return _UNIT_SCALE[best]

    def _finish(self, ts_raw, ts_kind, frame_no, width, height, layout, dets) -> Optional[DetFrame]:
        self._set_layout(layout)
        # Label allow-list (keep unlabelled detections).
        allowed = {s.lower() for s in self.opts.labels}
        kept = []
        for d in dets:
            if d.label is None or str(d.label).lower() in allowed:
                kept.append(d)
            else:
                self.stats["filtered_labels"] += 1
        dets = kept
        dets = self._coords(dets, width, height)

        abs_ts = ts_raw if ts_kind in ("iso", "epoch") else None
        if ts_raw is None or ts_kind is None:
            if frame_no is None:
                self._log_once("notime", "adapter: no timestamp or frame number; dropping frame",
                               level=logging.WARNING)
                return None
            self._log_once("frametime", "adapter: no timestamp field; using frame / %.1f fps",
                           self.opts.default_fps, level=logging.WARNING)
            ts_raw, ts_kind = float(frame_no), "frame"
            scale = 1.0 / self.opts.default_fps
        else:
            scale = self._resolve_scale(ts_kind, ts_raw)

        if self._raw0 is None:
            self._raw0, self._raw_kind = ts_raw, ts_kind
            self._log_once("timesrc", "adapter: time from %s timestamps", ts_kind)
        if scale is None:
            # Unit not known yet (first frame of an auto-unit stream): it is t = 0 by definition.
            t = 0.0
        else:
            if self._scale is None and ts_kind == "num":
                self._scale = scale
            t = (ts_raw - self._raw0) * scale
        self._last_raw = ts_raw

        if self._last_t is not None and t < self._last_t:
            if self._last_t - t > self.opts.max_backstep_s:
                self.stats["dropped_backwards"] += 1
                log.warning("adapter: frame at t=%.3f goes back %.3f s; dropped", t, self._last_t - t)
                return None
            t = self._last_t  # small jitter: clamp to keep t monotonic
        self._last_t = t
        self.stats["frames"] += 1
        return DetFrame(t=t, frame=frame_no, width=width, height=height, dets=dets,
                        abs_ts=abs_ts, layout=layout)

    def _coords(self, dets: List[Det], width, height) -> List[Det]:
        if not dets:
            return dets
        mode = self.opts.coords
        if mode == "auto":
            if self._coord_mode is None:
                vals = [abs(v) for d in dets for v in (d.x1, d.y1, d.x2, d.y2)]
                self._coord_mode = "norm" if max(vals) <= 1.5 else "px"
                self._log_once("coords", "adapter: coordinates read as %s", self._coord_mode)
            mode = self._coord_mode
        if mode == "px":
            return dets
        W, H = (width, height) if width and height else (self.opts.frame_size or (None, None))
        if not W or not H:
            self._log_once("normsize", "adapter: normalised coordinates but no frame size; pass --frame-size WxH",
                           level=logging.ERROR)
            return []
        return [Det(d.track_id, d.x1 * W, d.y1 * H, d.x2 * W, d.y2 * H, d.conf, d.label) for d in dets]


def auto_parse(obj, opts: Optional[AdapterOptions] = None) -> Optional[DetFrame]:
    """One-shot parse of a single-frame message (stateless convenience wrapper)."""
    frames = Adapter(opts).feed(obj)
    return frames[0] if frames else None


# ===================================================================================
# Line decoding shared by every transport
# ===================================================================================

def decode_line(line: str):
    """Decode one line of JSONL, PipeGen's metadata.json (one frame per line) or
    `{"topic","payload"}` mirror lines. Returns a JSON value or None."""
    s = line.strip()
    if not s:
        return None
    s = s.rstrip(",").strip()
    if not s.startswith("{") and not s.startswith("["):
        return None
    if s in ("{", "["):
        return None
    try:
        obj = json.loads(s)
    except json.JSONDecodeError:
        return None
    if isinstance(obj, dict) and "payload" in obj and "topic" in obj and len(obj) <= 3:
        payload = obj["payload"]
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except json.JSONDecodeError:
                return None
        return payload
    return obj


def _open_text(path: Path):
    if str(path).endswith(".gz"):
        return io.TextIOWrapper(gzip.open(path, "rb"), encoding="utf-8")
    return open(path, encoding="utf-8")


def _frames_from_lines(lines: Iterable[str], adapter: Adapter) -> Iterator[DetFrame]:
    for line in lines:
        obj = decode_line(line)
        if obj is None:
            continue
        for fr in adapter.feed(obj):
            yield fr
    for fr in adapter.flush():
        yield fr


def _whole_document(path: Path):
    """A single-document JSON file (e.g. a finished metadata.json written in one piece)."""
    with _open_text(path) as f:
        text = f.read()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def jsonl_source(path: str | Path, adapter: Optional[Adapter] = None,
                 realtime: bool = False, speed: float = 1.0) -> Iterator[DetFrame]:
    """Read a JSONL / metadata.json / .gz file. With realtime=True, pace frames by their t."""
    path = Path(path)
    adapter = adapter or Adapter()
    with _open_text(path) as f:
        frames = _frames_from_lines(f, adapter)
        got_any = False
        start_wall = None
        for fr in frames:
            got_any = True
            if realtime:
                if start_wall is None:
                    start_wall = time.monotonic() - fr.t / speed
                delay = start_wall + fr.t / speed - time.monotonic()
                if delay > 0:
                    time.sleep(delay)
            yield fr
    if not got_any:
        doc = _whole_document(path)
        if doc is not None:
            for fr in adapter.feed(doc):
                yield fr
            for fr in adapter.flush():
                yield fr


def tail_jsonl(path: str | Path, adapter: Optional[Adapter] = None, poll_s: float = 0.02,
               from_start: bool = True, stop: Optional[threading.Event] = None) -> Iterator[Optional[DetFrame]]:
    """Follow a growing file (PipeGen writes metadata.json one frame per line, flushed).

    Yields None roughly every poll interval when idle so callers can run timers.
    Detects truncation (PipeGen restarted) and resets the adapter's time base.
    """
    path = Path(path)
    adapter = adapter or Adapter()
    pos = 0          # bytes read so far
    line_end = 0     # offset just after the last newline consumed
    head = b""       # first bytes of the file, to notice a rewrite
    buf = ""

    def replaced(size: int) -> bool:
        if size < pos:
            return True
        if line_end == 0 and not head:
            return False
        with open(path, "rb") as f:
            if line_end > 0:
                f.seek(line_end - 1)
                if f.read(1) != b"\n":
                    return True
            f.seek(0)
            return bool(head) and f.read(len(head)) != head

    while not (stop and stop.is_set()):
        try:
            size = path.stat().st_size
        except FileNotFoundError:
            yield None
            time.sleep(0.25)
            continue
        if size != pos and (pos or line_end) and replaced(size):
            log.warning("tail: %s was rewritten (producer restarted); resetting time base", path)
            pos, line_end, head, buf = 0, 0, b"", ""
            adapter.reset_stream()
        if not from_start and pos == 0 and size > 0:
            pos = line_end = size
        if size > pos:
            with open(path, "rb") as f:
                f.seek(pos)
                chunk = f.read(size - pos)
                if len(head) < 64:
                    f.seek(0)
                    head = f.read(min(64, size))
            pos = size
            text = buf + chunk.decode("utf-8", errors="replace")
            *lines, buf = text.split("\n")
            line_end = pos - len(buf.encode("utf-8"))
            emitted = False
            for line in lines:
                obj = decode_line(line)
                if obj is None:
                    continue
                for fr in adapter.feed(obj):
                    emitted = True
                    yield fr
            if not emitted:
                yield None
        else:
            yield None
            time.sleep(poll_s)


def folder_source(directory: str | Path, pattern: str = "*.json", adapter: Optional[Adapter] = None,
                  follow: bool = True, poll_s: float = 0.1,
                  stop: Optional[threading.Event] = None) -> Iterator[Optional[DetFrame]]:
    """Per-frame JSON files in a folder, processed in name order; follows new files."""
    directory = Path(directory)
    adapter = adapter or Adapter()
    seen: set = set()
    while not (stop and stop.is_set()):
        files = sorted(p for p in directory.glob(pattern) if p.name not in seen)
        for p in files:
            seen.add(p.name)
            try:
                with _open_text(p) as f:
                    text = f.read()
            except OSError:
                continue
            objs = []
            try:
                objs = [json.loads(text)]
            except json.JSONDecodeError:
                objs = [o for o in (decode_line(l) for l in text.splitlines()) if o is not None]
            for o in objs:
                for fr in adapter.feed(o):
                    yield fr
        if not follow:
            for fr in adapter.flush():
                yield fr
            return
        yield None
        time.sleep(poll_s)


def stdin_source(adapter: Optional[Adapter] = None) -> Iterator[DetFrame]:
    adapter = adapter or Adapter()
    yield from _frames_from_lines(sys.stdin, adapter)


def tcp_source(host: str, port: int, adapter: Optional[Adapter] = None,
               stop: Optional[threading.Event] = None) -> Iterator[Optional[DetFrame]]:
    """Newline-delimited JSON over TCP (PipeGen VMS mode: run.vms.sh, metadata port 7002).

    Reconnects with backoff and never raises to the consumer.
    """
    adapter = adapter or Adapter()
    backoff = 0.5
    while not (stop and stop.is_set()):
        try:
            with socket.create_connection((host, port), timeout=3.0) as sock:
                sock.settimeout(0.25)
                log.info("tcp: connected to %s:%d", host, port)
                backoff = 0.5
                buf = b""
                while not (stop and stop.is_set()):
                    try:
                        chunk = sock.recv(65536)
                    except socket.timeout:
                        yield None
                        continue
                    if not chunk:
                        raise ConnectionError("closed by peer")
                    buf += chunk
                    *lines, buf = buf.split(b"\n")
                    for line in lines:
                        obj = decode_line(line.decode("utf-8", errors="replace"))
                        if obj is None:
                            continue
                        for fr in adapter.feed(obj):
                            yield fr
        except (OSError, ConnectionError) as e:
            log.warning("tcp: %s:%d unavailable (%s); retrying in %.1f s", host, port, e, backoff)
            t_end = time.monotonic() + backoff
            while time.monotonic() < t_end and not (stop and stop.is_set()):
                yield None
                time.sleep(0.1)
            backoff = min(backoff * 2, 5.0)


def mqtt_source(host: str, port: int, topic: str, adapter: Optional[Adapter] = None,
                stop: Optional[threading.Event] = None, client_id: str = "") -> Iterator[Optional[DetFrame]]:
    """Subscribe to PipeGen's MQTT detections. paho 1.x and 2.x, reconnect with backoff."""
    import paho.mqtt.client as mqtt  # local import: optional dependency for file replays

    adapter = adapter or Adapter()
    q: "queue.Queue[bytes]" = queue.Queue(maxsize=2000)

    def on_connect(client, userdata, flags, rc, *args):
        log.info("mqtt in: connected to %s:%d (rc=%s), subscribing %s", host, port, rc, topic)
        client.subscribe(topic, qos=0)

    def on_message(client, userdata, msg):
        try:
            q.put_nowait(msg.payload)
        except queue.Full:
            pass  # consumer is behind; dropping input is better than unbounded memory

    try:
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id)  # paho >= 2
    except AttributeError:
        client = mqtt.Client(client_id=client_id)  # paho 1.x
    client.on_connect = on_connect
    client.on_message = on_message
    client.reconnect_delay_set(min_delay=1, max_delay=5)
    try:
        client.connect_async(host, port, keepalive=20)
    except Exception as e:  # noqa: BLE001  - never crash the consumer
        log.warning("mqtt in: connect to %s:%d failed: %s", host, port, e)
    client.loop_start()
    try:
        while not (stop and stop.is_set()):
            try:
                payload = q.get(timeout=0.1)
            except queue.Empty:
                yield None
                continue
            try:
                obj = json.loads(payload.decode("utf-8", errors="replace"))
            except json.JSONDecodeError:
                adapter.stats["unparsed"] += 1
                continue
            for fr in adapter.feed(obj):
                yield fr
    finally:
        client.loop_stop()
        try:
            client.disconnect()
        except Exception:  # noqa: BLE001
            pass


def open_source(spec: str, adapter: Adapter, realtime: bool = False, speed: float = 1.0,
                stop: Optional[threading.Event] = None) -> Iterator[Optional[DetFrame]]:
    """Parse a --source spec:
       mqtt://host:port/topic   tcp://host:port   jsonl:PATH   tail:PATH   folder:PATH[:glob]   stdin
    """
    if spec.startswith("mqtt://"):
        rest = spec[len("mqtt://"):]
        hostport, _, topic = rest.partition("/")
        host, _, port = hostport.partition(":")
        return mqtt_source(host or "localhost", int(port or 1883), topic or "crowdshield/detections",
                           adapter, stop=stop)
    if spec.startswith("tcp://"):
        host, _, port = spec[len("tcp://"):].partition(":")
        return tcp_source(host or "localhost", int(port or 7002), adapter, stop=stop)
    if spec.startswith("jsonl:"):
        return jsonl_source(os.path.expanduser(spec[len("jsonl:"):]), adapter, realtime=realtime, speed=speed)
    if spec.startswith("tail:"):
        return tail_jsonl(os.path.expanduser(spec[len("tail:"):]), adapter, stop=stop)
    if spec.startswith("folder:"):
        rest = os.path.expanduser(spec[len("folder:"):])
        if os.path.isdir(rest):
            return folder_source(rest, adapter=adapter, stop=stop)
        d, _, pat = rest.rpartition(":")
        return folder_source(d, pat or "*.json", adapter=adapter, stop=stop)
    if spec == "stdin":
        return stdin_source(adapter)
    raise ValueError(f"unknown --source {spec!r}")
