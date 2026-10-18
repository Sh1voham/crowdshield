"""The gateway application: ingest, history, incidents, health, REST API, WebSocket push, static UI."""
from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Dict, Optional, Set

from crowdshield.geometry import save_calibration, validate_calibration
from crowdshield.schema import validate as validate_msg

from . import evidence
from .control import ControlError, Controller, list_recordings
from .frames import FrameSource
from .health import DataAges, GpuMonitor, pgrep
from .history import History
from .http import (HttpError, Request, Response, read_request, static_file, write_response, ws_frame,
                   ws_handshake, ws_read_frame)
from .incidents import Incidents

log = logging.getLogger("gateway")

STATIC_PAGES = {"/": "index.html", "/steward": "steward.html", "/calibrate": "calibrate.html",
                "/judge": "judge.html", "/selftest": "selftest.html"}


class WsClient:
    def __init__(self, req: Request, max_hz: float):
        self.req = req
        self.writer = req.writer
        self.topics: Set[str] = {"state", "alert", "health", "incident", "status"}
        self.min_period = 1.0 / max_hz
        self.pending_state: Optional[bytes] = None
        self.queue: list = []          # alerts / incidents / health: never dropped
        self.wake = asyncio.Event()
        self.last_state_sent = 0.0
        self.closed = False
        self.sent = 0
        self.dropped_states = 0


class Gateway:
    def __init__(self, cfg: dict, root: Path, allow_lan_control: bool = False, clock=time.time):
        self.cfg = cfg
        self.root = root
        self.clock = clock
        self.started = clock()
        self.prefix = cfg.get("topic_prefix", "crowdshield")
        data_dir = root / cfg.get("data_dir", "gateway/data")
        self.data_dir = data_dir
        self.history = History(full_s=cfg.get("history_full_s", 120), keep_s=cfg.get("history_keep_s", 3600),
                               summary_hz=cfg.get("history_summary_hz", 2), data_dir=data_dir, clock=clock)
        self.incidents = Incidents(clock=clock)
        self.incidents.listeners.append(self._incident_event)
        self.ages = DataAges(clock)
        self.latest: Dict[str, dict] = {}
        self.invalid = 0
        self.invalid_examples: list = []
        self.clients: Set[WsClient] = set()
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self.mqtt = None
        self.gpu: Optional[GpuMonitor] = None
        self.frames = FrameSource(cfg.get("frames", {}), data_dir, root)
        self.control = Controller(cfg.get("control", {}), root, root / ".cs_run", allow_lan=allow_lan_control,
                                  pin=cfg.get("pin"))
        self.static_root = root / cfg.get("static_root", "ui")
        self.push_hz = float(cfg.get("ws_max_hz", 5))

    # ===================================================================================
    # Ingest
    # ===================================================================================
    def on_mqtt(self, topic: str, payload: bytes) -> None:
        """Called from the MQTT thread."""
        if self.loop is not None:
            self.loop.call_soon_threadsafe(self.ingest, topic, payload)

    def ingest(self, topic: str, payload, w: Optional[float] = None) -> bool:
        w = self.clock() if w is None else w
        if not topic.startswith(self.prefix + "/"):
            return False
        suffix = topic[len(self.prefix) + 1:]
        if suffix == "detections":
            self.ages.touch("detections")
            return True  # PipeGen's raw feed: only its age matters here
        if isinstance(payload, (bytes, bytearray)):
            try:
                payload = json.loads(payload.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                self._invalid(suffix, ["not JSON"])
                return False
        errs = validate_msg(suffix, payload)
        if errs:
            self._invalid(suffix, errs)
            return False
        self.ages.touch(suffix)
        self.latest[suffix] = payload
        if suffix == "state":
            summ = self.history.add_state(payload, w)
            self.incidents.on_state(payload, summ, w)
            self._push("state", payload)
        elif suffix == "alerts":
            self.history.add_alert(payload, w)
            self.incidents.on_alert(payload, w)
            self._push("alert", payload)
        elif suffix == "status":
            self._push("status", payload)
        return True

    def _invalid(self, suffix: str, errs) -> None:
        self.invalid += 1
        if len(self.invalid_examples) < 20:
            self.invalid_examples.append({"topic": suffix, "errors": list(errs)[:5]})
        if self.invalid <= 5 or self.invalid % 100 == 0:
            log.warning("invalid %s message #%d: %s", suffix, self.invalid, list(errs)[:3])

    def _incident_event(self, kind: str, inc: dict) -> None:
        self._push("incident", dict(inc, event=kind))

    # ===================================================================================
    # WebSocket push (coalesce states for slow clients; never drop alerts)
    # ===================================================================================
    def _push(self, mtype: str, data: dict) -> None:
        if not self.clients:
            return
        msg = json.dumps({"type": mtype, "data": data}, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        for c in list(self.clients):
            if mtype not in c.topics:
                continue
            if mtype in ("state", "status"):
                if c.pending_state is not None:
                    c.dropped_states += 1
                c.pending_state = msg if mtype == "state" else c.pending_state or msg
                if mtype == "status":
                    c.queue.append(msg)
            else:
                c.queue.append(msg)
            c.wake.set()

    async def _ws_sender(self, c: WsClient) -> None:
        while not c.closed:
            await c.wake.wait()
            c.wake.clear()
            try:
                while c.queue:
                    c.writer.write(ws_frame(c.queue.pop(0)))
                    c.sent += 1
                if c.pending_state is not None:
                    wait = c.min_period - (time.monotonic() - c.last_state_sent)
                    if wait > 0:
                        await asyncio.sleep(wait)
                    if c.pending_state is not None:
                        c.writer.write(ws_frame(c.pending_state))
                        c.pending_state = None
                        c.last_state_sent = time.monotonic()
                        c.sent += 1
                    if c.queue:
                        c.wake.set()
                await c.writer.drain()
            except (ConnectionError, RuntimeError):
                c.closed = True

    async def handle_ws(self, req: Request) -> None:
        if not await ws_handshake(req):
            raise HttpError(400, "WebSocket upgrade expected")
        c = WsClient(req, self.push_hz)
        self.clients.add(c)
        sender = asyncio.create_task(self._ws_sender(c))
        hello = {"type": "hello", "data": {"v": 1, "server": "crowdshield-gateway", "prefix": self.prefix,
                                           "time": self.clock(), "health": self.health()}}
        c.queue.append(json.dumps(hello).encode("utf-8"))
        if "state" in self.latest:
            c.pending_state = json.dumps({"type": "state", "data": self.latest["state"]}).encode("utf-8")
        c.wake.set()
        try:
            while True:
                op, data = await ws_read_frame(req.reader)
                if op == 0x8:  # close
                    req.writer.write(ws_frame(data[:2], opcode=0x8))
                    break
                if op == 0x9:  # ping
                    req.writer.write(ws_frame(data, opcode=0xA))
                    continue
                if op == 0x1:
                    try:
                        msg = json.loads(data.decode("utf-8"))
                    except (UnicodeDecodeError, json.JSONDecodeError):
                        continue
                    if msg.get("type") == "subscribe" and isinstance(msg.get("topics"), list):
                        c.topics = {t for t in msg["topics"] if isinstance(t, str)}
                    elif msg.get("type") == "ping":
                        c.queue.append(json.dumps({"type": "pong", "data": {"time": self.clock()}}).encode())
                        c.wake.set()
        except (asyncio.IncompleteReadError, ConnectionError, HttpError):
            pass
        finally:
            c.closed = True
            c.wake.set()
            self.clients.discard(c)
            sender.cancel()

    # ===================================================================================
    # Health
    # ===================================================================================
    def health(self) -> dict:
        ctl = self.control.status()
        eng = ctl.get("engine")
        pg = ctl.get("pipegen")
        pg_pids = pgrep(self.cfg.get("pipegen_pgrep", "gst-launch-1.0"))
        eng_pids = pgrep(self.cfg.get("engine_pgrep", "crowdshield.run"))
        state = self.latest.get("state") or {}
        pl = state.get("pipeline") or {}
        ages = self.ages.ages()
        return {
            "v": 1,
            "mqtt": self.mqtt.state if self.mqtt else "off",
            "data_age_s": ages,
            "stale": ages.get("state") is None or ages["state"] > 3.0,
            "engine": {"running": bool((eng and eng["running"]) or eng_pids),
                       "pid": (eng or {}).get("pid") or (eng_pids[0] if eng_pids else None),
                       "profile": (eng or {}).get("profile"), "fps": pl.get("fps"), "latency_ms": pl.get("latency_ms"),
                       "source": pl.get("source"), "layout": pl.get("layout"), "tracker": pl.get("tracker")},
            "pipegen": {"running": bool((pg and pg["running"]) or pg_pids), "pids": pg_pids[:4],
                        "mode": (pg or {}).get("mode"), "model": pl.get("model"), "precision": pl.get("precision")},
            "gpu": self.gpu.latest if self.gpu else None,
            "invalid_messages": self.invalid,
            "clients": len(self.clients),
            "uptime_s": round(self.clock() - self.started, 1),
        }

    async def health_loop(self) -> None:
        while True:
            await asyncio.sleep(2.0)
            try:
                self._push("health", self.health())
            except Exception:  # noqa: BLE001
                log.exception("health push failed")

    # ===================================================================================
    # HTTP routing
    # ===================================================================================
    async def route(self, req: Request) -> Optional[Response]:
        p = req.path
        if p == "/ws":
            await self.handle_ws(req)
            return None
        if p.startswith("/api/"):
            return await self.api(req, p[5:])
        if req.method not in ("GET", "HEAD"):
            raise HttpError(405)
        if p == "/classic":
            return Response.redirect("/?renderer=2d")
        if p in STATIC_PAGES:
            return static_file(self.static_root, STATIC_PAGES[p])
        if p.startswith("/evidence/"):
            return static_file(self.root / "evidence", p[len("/evidence/"):])
        return static_file(self.static_root, p)

    async def api(self, req: Request, name: str) -> Response:
        m = req.method
        if name == "health" and m == "GET":
            return Response.json(self.health())
        if name == "state" and m == "GET":
            st = self.latest.get("state")
            return Response.json(st) if st else Response.json({"error": "no state yet"}, 404)
        if name == "status" and m == "GET":
            return Response.json(self.latest.get("status") or {"error": "no status yet"})
        if name == "history" and m == "GET":
            if req.q("at"):
                st = self.history.at(float(req.q("at")))
                return Response.json({"state": st}) if st else Response.json({"error": "no data at that time"}, 404)
            since = float(req.q("since", "0") or 0)
            fields = req.q("fields", "summary")
            if fields == "full":
                return Response.json({"fields": "full", "items": self.history.fulls(since),
                                      "alerts": self.history.alert_list(since)})
            return Response.json({"fields": "summary", "items": self.history.summaries(since),
                                  "alerts": self.history.alert_list(since), "now": self.clock()})
        if name == "incidents" and m == "GET":
            return Response.json({"items": self.incidents.list()})
        if name.startswith("incidents/") and m == "GET":
            inc = self.incidents.get(name.split("/", 1)[1])
            return Response.json(inc) if inc else Response.json({"error": "no such incident"}, 404)
        if name == "calibration":
            return self.api_calibration(req)
        if name == "frame.jpg" and m == "GET":
            t = req.q("t")
            jpg = await asyncio.get_running_loop().run_in_executor(
                None, self.frames.still, req.q("source", "auto") or "auto", float(t) if t else None, True)
            if jpg is None:
                return Response.json({"error": "No frame source. Upload a snapshot on /calibrate, or set "
                                               "frames.video in gateway/config.json."}, 404)
            return Response(200, jpg, {"Content-Type": "image/jpeg", "Cache-Control": "no-store"})
        if name == "frame" and m == "POST":
            if not req.is_local:
                self.control.authorize(False, req.headers.get("x-cs-pin"))
            try:
                self.frames.save_upload(req.body)
            except ValueError as e:
                return Response.json({"error": str(e)}, 400)
            return Response.json({"ok": True})
        if name == "frames" and m == "GET":
            return Response.json(self.frames.describe())
        if name == "video.mjpg" and m == "GET":
            await self.stream_mjpeg(req)
            return None  # type: ignore[return-value]
        if name == "config" and m == "GET":
            return Response.json(self.config_view())
        if name == "evidence" and m == "GET":
            data = await asyncio.get_running_loop().run_in_executor(None, evidence.collect, self.root)
            return Response.json(data)
        if name == "recordings" and m == "GET":
            items = [{k: v for k, v in r.items() if k in ("name", "kind", "label", "size")}
                     for r in list_recordings(self.root)]
            return Response.json({"items": items})
        if name == "control":
            if m == "GET":
                return Response.json({"actions": self.control.describe(), "status": self.control.status(),
                                      "lan_control": self.control.allow_lan})
            if m == "POST":
                try:
                    self.control.authorize(req.is_local, req.headers.get("x-cs-pin"))
                    body = req.json()
                    if not isinstance(body, dict):
                        raise ControlError(400, "body must be an object")
                    result = await asyncio.get_running_loop().run_in_executor(
                        None, self.control.run, body.get("action"), body)
                    return Response.json(result)
                except ControlError as e:
                    return Response.json({"error": e.message}, e.status)
        if name == "invalid" and m == "GET":
            return Response.json({"count": self.invalid, "examples": self.invalid_examples})
        raise HttpError(404, f"no API endpoint {name!r}")

    def _calib_path(self, plane: str) -> Path:
        files = self.cfg.get("calibration_files", {})
        rel = files.get(plane)
        if rel is None:
            raise HttpError(400, "plane must be floor or head")
        return self.root / rel

    def api_calibration(self, req: Request) -> Response:
        plane = req.q("plane", "floor") or "floor"
        path = self._calib_path(plane)
        if req.method == "GET":
            if not path.exists():
                return Response.json({"exists": False, "plane": plane, "path": str(path.relative_to(self.root))})
            d = json.loads(path.read_text(encoding="utf-8"))
            d["exists"] = True
            d["plane"] = plane
            return Response.json(d)
        if req.method == "POST":
            if not req.is_local:
                self.control.authorize(False, req.headers.get("x-cs-pin"))
            d = req.json()
            if not isinstance(d, dict):
                return Response.json({"error": "body must be an object"}, 400)
            for k in ("exists", "plane"):
                d.pop(k, None)
            errs = validate_calibration(d)
            if errs:
                return Response.json({"error": "invalid calibration", "problems": errs}, 400)
            save_calibration(path, d)
            return Response.json({"ok": True, "path": str(path.relative_to(self.root)),
                                  "message": "Saved. The engine reloads it within 2 s."})
        raise HttpError(405)

    def config_view(self) -> dict:
        try:
            cfg = json.loads((self.root / "config" / "default.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            cfg = {}
        st = self.latest.get("state") or {}
        calib = {}
        try:
            calib = json.loads(self._calib_path("floor").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, HttpError):
            pass
        return {"file": "config/default.json", "thresholds": st.get("thresholds") or cfg.get("status"),
                "status": cfg.get("status"), "pressure": cfg.get("pressure"), "recall": cfg.get("recall"),
                "pipeline": cfg.get("pipeline"), "demo_profile": cfg.get("demo_profile"),
                "zone": st.get("zone") or calib.get("zone"), "area": st.get("area") or calib.get("area"),
                "gate": st.get("gate") or calib.get("gate"), "profile": (st.get("thresholds") or {}).get("profile"),
                "calibration": calib or None}

    async def stream_mjpeg(self, req: Request) -> None:
        w = req.writer
        w.write(b"HTTP/1.1 200 OK\r\nContent-Type: multipart/x-mixed-replace; boundary=csframe\r\n"
                b"Cache-Control: no-store\r\nConnection: close\r\n\r\n")
        try:
            def stream_t():
                st = self.latest.get("state") or {}
                src = (st.get("pipeline") or {}).get("source")
                return st.get("t") if src in ("video", "live") and self.ages.ages().get("state", 99) < 3 else None

            async for jpg in self.frames.mjpeg(float(self.cfg.get("mjpeg_fps", 8)), stream_t):
                w.write(b"--csframe\r\nContent-Type: image/jpeg\r\nContent-Length: " + str(len(jpg)).encode()
                        + b"\r\n\r\n" + jpg + b"\r\n")
                await w.drain()
        except (ConnectionError, asyncio.CancelledError):
            pass
        raise ConnectionAbortedError  # close the connection after a stream

    # ===================================================================================
    # Connection loop
    # ===================================================================================
    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            while True:
                req = None
                try:
                    req = await read_request(reader, writer)
                    if req is None:
                        break
                    resp = await self.route(req)
                    if resp is None:  # websocket or stream finished
                        break
                except HttpError as e:
                    resp = Response.json({"error": e.message}, e.status)
                except ControlError as e:
                    resp = Response.json({"error": e.message}, e.status)
                except (asyncio.IncompleteReadError, ConnectionError):
                    break
                except Exception:  # noqa: BLE001
                    log.exception("request failed: %s", req.target if req else "?")
                    resp = Response.json({"error": "internal error"}, 500)
                await write_response(req, writer, resp)
                if req is not None and req.headers.get("connection", "").lower() == "close":
                    break
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        finally:
            try:
                writer.close()
            except Exception:  # noqa: BLE001
                pass

    async def replay_jsonl(self, path: Path, speed: float = 1.0, loop_forever: bool = True) -> None:
        """Feed {"topic","payload"} lines (e.g. tools/sample_stream.jsonl) without a broker."""
        while True:
            lines = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
            t_prev = None
            for rec in lines:
                t = (rec.get("payload") or {}).get("t")
                if t_prev is not None and t is not None and t > t_prev:
                    await asyncio.sleep((t - t_prev) / speed)
                if t is not None:
                    t_prev = t
                self.ingest(rec["topic"], rec["payload"])
            if not loop_forever:
                return

    async def serve(self, host: str, port: int, mqtt: Optional[str] = None,
                    replay: Optional[Path] = None, replay_speed: float = 1.0) -> None:
        self.loop = asyncio.get_running_loop()
        self.history.restore()
        if mqtt and mqtt.lower() != "none":
            from .mqtt_in import MqttIn
            h, _, prt = mqtt.partition(":")
            self.mqtt = MqttIn(h or "localhost", int(prt or 1883), self.prefix, self.on_mqtt)
        self.gpu = GpuMonitor(2.0)
        server = await asyncio.start_server(self.handle, host, port, limit=2 ** 20)
        tasks = [asyncio.create_task(self.health_loop())]
        if replay is not None:
            tasks.append(asyncio.create_task(self.replay_jsonl(replay, replay_speed)))
        log.info("gateway listening on http://%s:%d (WebSocket /ws)", host, port)
        try:
            async with server:
                await server.serve_forever()
        finally:
            for t in tasks:
                t.cancel()
            self.history.close()
            if self.mqtt:
                self.mqtt.stop()
            if self.gpu:
                self.gpu.stop()
            self.control.shutdown()
