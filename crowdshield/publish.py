"""Publisher: builds the v1 messages of docs/DATA_CONTRACT.md and ships them.

Sinks: MQTT (paho 1.x/2.x, never crashes when the broker is down) and a JSONL mirror
(`{"topic","payload"}` lines) for recordings, replays and the gateway's file ingest.
"""
from __future__ import annotations

import json
import logging
import math
import os
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, List, Optional

import numpy as np

from . import CONTRACT_VERSION
from .assess import LABEL
from .config import Config
from .engine import Snapshot
from .geometry import Calibration

log = logging.getLogger("crowdshield.publish")

SIGN_TEXT = {"NONE": "ENTRY OPEN", "SLOW_ENTRY": "SLOW ENTRY", "HOLD_ENTRY": "HOLD ENTRY"}
ADVICE = {
    "GREEN": "Normal flow.",
    "AMBER": "Slow entry. Watch the peak area.",
    "RED": "Hold entry. Open all exits. Alert supervisors.",
}


def iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + f"{int((ts % 1) * 1000):03d}Z"


def _r(v, nd):
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(f):
        return None
    return round(f, nd)


def grid_list(a: np.ndarray, nd: int) -> list:
    out = []
    for row in a:
        out.append([None if not math.isfinite(v) else round(float(v), nd) for v in row])
    return out


def alert_message(level: str, kind: str, m: dict, zone: str) -> str:
    d, p = m.get("dmax", 0.0) or 0.0, m.get("pidx", 0.0) or 0.0
    if kind == "RECOVERED":
        return f"Back to normal at {zone}. Peak density {d:.1f} people/m²."
    if kind == "TURBULENCE":
        return (f"Crowd turbulence at {zone}: pressure {p:.1f}× threshold at {d:.1f} people/m². "
                "Hold entry. Open all exits. Alert supervisors.")
    if kind == "FORECAST":
        tr = m.get("t_red_s")
        when = f"{int(tr // 60)}:{int(tr % 60):02d}" if tr is not None else "soon"
        return f"Forecast: Critical at {zone} in about {when} if the trend continues."
    if kind == "PIPELINE":
        if level == "CLEAR":
            return "Detections are flowing again."
        return "No frames from PipeGen for over 3 s. Check that the pipeline is running (System → PipeGen)."
    word = LABEL.get(level, level)
    if kind == "PRESSURE":
        body = f"crowd pressure {p:.1f}× threshold at {d:.1f} people/m²"
    else:
        body = f"density {d:.1f} people/m²"
    return f"{word}: {body} at {zone}. {ADVICE.get(level, '')}".strip()


# ===================================================================================
# Sinks
# ===================================================================================
class Sink:
    def publish(self, topic: str, payload: dict, qos: int = 0, retain: bool = False) -> None:
        raise NotImplementedError

    def actuator_state(self) -> Optional[dict]:
        return None

    def close(self) -> None:
        pass


class JsonlSink(Sink):
    """Mirror every message as {"topic","payload"} lines (path or '-' for stdout)."""

    def __init__(self, path: str):
        self.fh = sys.stdout if path == "-" else open(path, "a", encoding="utf-8")
        self.lock = threading.Lock()

    def publish(self, topic, payload, qos=0, retain=False):
        line = json.dumps({"topic": topic, "payload": payload}, separators=(",", ":"), ensure_ascii=False)
        with self.lock:
            self.fh.write(line + "\n")
            self.fh.flush()

    def close(self):
        if self.fh is not sys.stdout:
            self.fh.close()


class MqttSink(Sink):
    def __init__(self, host: str, port: int, prefix: str, client_id: str = ""):
        import paho.mqtt.client as mqtt

        self.prefix = prefix
        self.connected = False
        self._warned = False
        self._act: Optional[dict] = None
        self._act_wall: Optional[float] = None
        try:
            self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id)
        except AttributeError:
            self.client = mqtt.Client(client_id=client_id)
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_message = self._on_message
        self.client.reconnect_delay_set(min_delay=1, max_delay=10)
        self.client.max_queued_messages_set(200)
        self.host, self.port = host, port
        try:
            self.client.connect_async(host, port, keepalive=20)
        except Exception as e:  # noqa: BLE001
            log.warning("mqtt: cannot start connection to %s:%d: %s", host, port, e)
        self.client.loop_start()

    def _on_connect(self, client, userdata, flags, rc, *args):
        ok = (rc == 0) if isinstance(rc, int) else not getattr(rc, "is_failure", False)
        self.connected = ok
        if ok:
            log.info("mqtt: connected to %s:%d", self.host, self.port)
            self._warned = False
            client.subscribe(f"{self.prefix}/actuator/state", qos=1)
        else:
            log.warning("mqtt: connection refused (%s)", rc)

    def _on_disconnect(self, client, userdata, *args):
        self.connected = False
        if not self._warned:
            log.warning("mqtt: disconnected from %s:%d; retrying in the background", self.host, self.port)
            self._warned = True

    def _on_message(self, client, userdata, msg):
        try:
            d = json.loads(msg.payload.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return
        if isinstance(d, dict):
            self._act, self._act_wall = d, time.time()

    def actuator_state(self) -> Optional[dict]:
        if self._act is None:
            return None
        out = dict(self._act)
        out["age_s"] = round(time.time() - (self._act_wall or time.time()), 1)
        return out

    def publish(self, topic, payload, qos=0, retain=False):
        if not self.connected:
            if not self._warned:
                log.warning("mqtt: broker %s:%d not reachable; messages are dropped until it is",
                            self.host, self.port)
                self._warned = True
            return
        try:
            self.client.publish(topic, json.dumps(payload, separators=(",", ":"), ensure_ascii=False),
                                qos=qos, retain=retain)
        except Exception as e:  # noqa: BLE001  - never crash on publish
            log.debug("mqtt publish failed: %s", e)

    def close(self):
        try:
            self.client.loop_stop()
            self.client.disconnect()
        except Exception:  # noqa: BLE001
            pass


# ===================================================================================
# Publisher
# ===================================================================================
class Publisher:
    def __init__(self, cfg: Config, calib: Calibration, sinks: List[Sink], prefix: str = "crowdshield",
                 zone: Optional[str] = None, source: str = "live", incidents_path: Optional[str] = None,
                 clock: Optional[Callable[[], float]] = None, start_wallclock: Optional[float] = None,
                 deterministic: bool = False):
        self.cfg = cfg
        self.calib = calib
        self.sinks = sinks
        self.prefix = prefix
        self.source = source
        self.zone = self._zone_name(zone or calib.zone)
        self.clock = clock or time.time
        self.start_wallclock = start_wallclock
        self.deterministic = deterministic
        self.seq = 0
        self.alert_seq = 0
        self.cmd_seq = 0
        self.last_action: Optional[str] = None
        self.last_cmd: Optional[dict] = None
        self.last_cmd_wall: Optional[float] = None
        self.last_status_t: Optional[float] = None
        self.last_status_wall: Optional[float] = None
        self.last_frame_wall: Optional[float] = None
        self.last_snapshot: Optional[Snapshot] = None
        self.pipeline_alerted = False
        self.frames = 0
        self.layout: Optional[str] = None
        self.tracker_kind = "pipegen"
        self.latency_ms: Optional[float] = None
        self.incidents = open(incidents_path, "a", encoding="utf-8") if incidents_path else None

    def _zone_name(self, zone: str) -> str:
        suffixes = []
        if self.cfg.profile == "demo":
            suffixes.append(" (demo profile)")
        if self.source == "replay":
            suffixes.append(" (replay)")
        if self.source == "synthetic" and "synthetic" not in zone.lower():
            suffixes.append(" (synthetic)")
        for s in suffixes:
            if s not in zone:
                zone += s
        return zone

    def set_calibration(self, calib: Calibration) -> None:
        self.calib = calib

    # -- time ----------------------------------------------------------------------
    def wall_for(self, t: float) -> float:
        if self.start_wallclock is not None:
            return self.start_wallclock + t
        return self.clock()

    def _send(self, topic: str, payload: dict, qos: int = 0, retain: bool = False) -> None:
        for s in self.sinks:
            s.publish(f"{self.prefix}/{topic}", payload, qos, retain)

    # -- frames ----------------------------------------------------------------------
    def on_frame(self, wall_now: float, abs_ts: Optional[float], layout: Optional[str]) -> None:
        self.frames += 1
        self.layout = layout
        if self.pipeline_alerted and not self.deterministic:
            self.pipeline_alerted = False
            self._alert("CLEAR", "PIPELINE", self.last_snapshot, wall_now)
        self.last_frame_wall = wall_now
        if abs_ts is not None and not self.deterministic and self.source in ("live", "video"):
            lat = (wall_now - abs_ts) * 1000.0
            self.latency_ms = round(lat, 1) if -1000.0 < lat < 60000.0 else None
        else:
            self.latency_ms = None

    def on_snapshot(self, snap: Snapshot, wall_now: Optional[float] = None) -> dict:
        self.last_snapshot = snap
        wall = self.wall_for(snap.t) if (self.deterministic or self.start_wallclock is not None) else (
            wall_now if wall_now is not None else self.clock())
        state = self.build_state(snap, wall)
        self._send("state", state, qos=0)
        for ev in snap.events:
            self._alert(ev["level"], ev["kind"], snap, wall)
        a = snap.assessment
        if a["action"] != self.last_action:
            self.last_action = a["action"]
            self.cmd_seq += 1
            self._send_cmd(snap, wall)
        if (self.last_status_t is None or snap.t < self.last_status_t
                or snap.t - self.last_status_t >= 1.0 / self.cfg.publish.status_hz - 1e-6):
            self.last_status_t = snap.t
            self._send_status(snap, wall, stale=False)
        return state

    def tick(self, wall_now: float) -> None:
        """Wall-clock duties: PIPELINE alert, stale heartbeat, actuator repeat."""
        if self.deterministic:
            return
        stale_s = self.cfg.publish.stale_s
        if self.last_frame_wall is not None and wall_now - self.last_frame_wall > stale_s:
            if not self.pipeline_alerted:
                self.pipeline_alerted = True
                self._alert("AMBER", "PIPELINE", self.last_snapshot, wall_now)
            if self.last_status_wall is None or wall_now - self.last_status_wall >= 1.0:
                self._send_status(self.last_snapshot, wall_now, stale=True)
        if self.last_cmd is not None and self.last_cmd_wall is not None and \
                wall_now - self.last_cmd_wall >= self.cfg.publish.actuator_period_s:
            self.last_cmd_wall = wall_now
            self._send("actuator/cmd", self.last_cmd, qos=1, retain=True)

    # -- builders --------------------------------------------------------------------
    def thresholds(self) -> dict:
        s = self.cfg.status
        return {"amber_density": s.amber_density, "red_density": s.red_density, "amber_pidx": s.amber_pidx,
                "red_pidx": s.red_pidx, "amber_pidx_density": s.amber_pidx_density,
                "red_pidx_density": s.red_pidx_density, "p_thr": self.cfg.pressure.p_thr,
                "profile": self.cfg.profile}

    def build_state(self, snap: Snapshot, wall: float) -> dict:
        self.seq += 1
        a, s = snap.assessment, snap.summary
        cap = self.cfg.publish.tracks_cap
        tracks = [{"id": None if tid is None else int(tid), "x": round(x, 2), "y": round(y, 2),
                   "vx": None if vx is None else round(vx, 2), "vy": None if vy is None else round(vy, 2)}
                  for (tid, x, y, vx, vy) in snap.tracks[:cap]]
        actuator = None
        for sk in self.sinks:
            actuator = actuator or sk.actuator_state()
        return {
            "v": CONTRACT_VERSION, "type": "state", "seq": self.seq,
            "t": round(snap.t, 3), "ts": iso(wall),
            "zone": self.zone,
            "area": {"width_m": self.calib.width_m, "height_m": self.calib.height_m},
            "gate": self.calib.gate,
            "camera": {"image_points": self.calib.image_points, "world_points": self.calib.world_points,
                       "frame_size": list(self.calib.frame_size) if self.calib.frame_size else None,
                       "foot_mode": self.calib.foot_mode},
            "status": {"level": a["status"], "label": a["label"], "since_t": _r(a["status_since"], 3),
                       "raw": a["raw"], "cause": a["cause"]},
            "action": a["action"],
            "count": snap.count,
            "density": {"max": _r(s["density"]["max"], 3), "mean": _r(s["density"]["mean"], 3),
                        "p95": _r(s["density"]["p95"], 3), "peak_cell": s["density"]["peak_cell"],
                        "measured_max": _r(s["density"]["measured_max"], 3), "unit": "persons/m2"},
            "pressure": {"max": _r(s["pressure"]["max"], 5), "mean": _r(s["pressure"]["mean"], 5),
                         "threshold": s["pressure"]["threshold"], "index": _r(snap.pidx, 3)},
            "speed": {"mean": _r(s["speed"]["mean"], 3)},
            "turbulence": {"on": a["turb_on"], "since_t": _r(a["turb_since"], 3)},
            "forecast": {"trend_per_min": _r(a["trend_per_min"], 3), "t_amber_s": _r(a["t_amber_s"], 1),
                         "t_red_s": _r(a["t_red_s"], 1), "r2": _r(a["r2"], 3)},
            "risk": _r(a["risk"], 3),
            "thresholds": self.thresholds(),
            "grid": {"cell_m": self.cfg.grid.cell_m, "rows": int(snap.rho.shape[0]), "cols": int(snap.rho.shape[1]),
                     "density": grid_list(snap.rho, 3), "pressure": grid_list(snap.pressure, 5),
                     "speed": grid_list(snap.speed, 3), "vx": grid_list(snap.vx, 3), "vy": grid_list(snap.vy, 3)},
            "tracks": tracks,
            "pipeline": {"source": self.source, "fps": _r(snap.fps, 1), "latency_ms": self.latency_ms,
                         "frames": self.frames, "model": self.cfg.pipeline.model,
                         "precision": self.cfg.pipeline.precision, "layout": self.layout,
                         "tracker": self.tracker_kind},
            "actuator": actuator,
        }

    def _send_status(self, snap: Optional[Snapshot], wall: float, stale: bool) -> None:
        self.last_status_wall = wall
        if snap is None:
            payload = {"v": CONTRACT_VERSION, "type": "status", "t": None, "ts": iso(wall), "zone": self.zone,
                       "level": None, "label": None, "since_t": None, "action": None, "count": None,
                       "dmax": None, "pidx": None, "risk": None, "turbulence": None, "source": self.source,
                       "data_age_s": None, "stale": True}
        else:
            a = snap.assessment
            age = None if self.last_frame_wall is None or self.deterministic else round(wall - self.last_frame_wall, 1)
            payload = {"v": CONTRACT_VERSION, "type": "status", "t": round(snap.t, 3), "ts": iso(wall),
                       "zone": self.zone, "level": a["status"], "label": a["label"],
                       "since_t": _r(a["status_since"], 3), "action": a["action"], "count": snap.count,
                       "dmax": _r(snap.summary["density"]["max"], 3), "pidx": _r(snap.pidx, 3),
                       "risk": _r(a["risk"], 3), "turbulence": a["turb_on"], "source": self.source,
                       "data_age_s": age, "stale": stale}
        self._send("status", payload, qos=1, retain=True)

    def _send_cmd(self, snap: Snapshot, wall: float) -> None:
        a = snap.assessment
        self.last_cmd = {"v": CONTRACT_VERSION, "seq": self.cmd_seq, "action": a["action"], "level": a["status"],
                         "text": SIGN_TEXT[a["action"]], "zone": self.zone, "t": round(snap.t, 3), "ts": iso(wall),
                         "advisory": True}
        self.last_cmd_wall = wall
        self._send("actuator/cmd", self.last_cmd, qos=1, retain=True)

    def _alert(self, level: str, kind: str, snap: Optional[Snapshot], wall: float) -> dict:
        self.alert_seq += 1
        metrics = {}
        cell = None
        status = action = None
        t = None
        if snap is not None:
            a = snap.assessment
            metrics = {"dmax": _r(snap.summary["density"]["max"], 3), "pidx": _r(snap.pidx, 3),
                       "count": snap.count, "risk": _r(a["risk"], 3)}
            if kind == "FORECAST":
                metrics["t_red_s"] = _r(a["t_red_s"], 1)
            cell = snap.summary["density"]["peak_cell"]
            status, action, t = a["status"], a["action"], round(snap.t, 3)
        msg = {"v": CONTRACT_VERSION, "type": "alert", "id": f"a-{self.alert_seq:06d}", "t": t, "ts": iso(wall),
               "zone": self.zone, "level": level, "kind": kind, "status": status, "action": action,
               "message": alert_message(level, kind, metrics, self.zone), "metrics": metrics, "cell": cell}
        self._send("alerts", msg, qos=1)
        if self.incidents is not None:
            self.incidents.write(json.dumps(msg, ensure_ascii=False) + "\n")
            self.incidents.flush()
            try:
                os.fsync(self.incidents.fileno())
            except OSError:
                pass
        log.info("ALERT %s:%s %s", level, kind, msg["message"])
        return msg

    def close(self) -> None:
        for s in self.sinks:
            s.close()
        if self.incidents is not None:
            self.incidents.close()
