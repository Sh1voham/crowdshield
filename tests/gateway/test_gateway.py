"""Gateway tests: schema, history, incidents, calibration, control allow-list, HTTP + WebSocket e2e."""
import asyncio
import base64
import json
import os
import socket
import struct
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from crowdshield.schema import validate
from gateway.control import ControlError, Controller
from gateway.history import History
from gateway.http import ws_accept_key, ws_frame
from gateway.incidents import Incidents
from gateway.server import Gateway

ROOT = Path(__file__).resolve().parents[2]
SAMPLE = ROOT / "tools" / "sample_stream.jsonl"
CFG = json.loads((ROOT / "gateway" / "config.json").read_text(encoding="utf-8"))


def sample_messages():
    return [json.loads(l) for l in SAMPLE.read_text(encoding="utf-8").splitlines() if l.strip()]


def first_state():
    return next(m["payload"] for m in sample_messages() if m["topic"].endswith("/state"))


# ---------------------------------------------------------------------------------------- schema
def test_schema_accepts_sample_stream():
    msgs = sample_messages()
    assert len(msgs) > 100
    for m in msgs:
        assert validate(m["topic"].split("/", 1)[1], m["payload"]) == [], m["topic"]


@pytest.mark.parametrize("mutate", [
    lambda s: s.pop("grid"),
    lambda s: s["status"].update(level="PURPLE"),
    lambda s: s.update(risk=1.7),
    lambda s: s["grid"]["density"].pop(),
    lambda s: s["density"].update(max=float("nan")),
    lambda s: s.update(v=2),
    lambda s: s["pipeline"].update(source="tv"),
])
def test_schema_rejects_broken_state(mutate):
    s = json.loads(json.dumps(first_state()))
    mutate(s)
    assert validate("state", s)


# ---------------------------------------------------------------------------------------- history
def test_history_downsamples_and_strips_grids(tmp_path):
    now = [1000.0]
    h = History(full_s=120, keep_s=3600, summary_hz=2, data_dir=tmp_path, clock=lambda: now[0])
    st = first_state()
    for k in range(5 * 300):  # 300 s at 5 Hz
        now[0] = 1000.0 + k * 0.2
        h.add_state(st, now[0])
    assert len(h.summary) == pytest.approx(600, abs=2)        # 2 Hz over 300 s
    assert all("grid" not in s and "tracks" not in s for s in h.summary)
    assert now[0] - h.full[0][0] <= 120 and len(h.full) == pytest.approx(600, abs=2)  # full rate, 120 s
    h.add_alert({"v": 1, "type": "alert", "id": "a1", "level": "RED", "kind": "DENSITY", "message": "x", "t": 1})
    h.close()
    h2 = History(data_dir=tmp_path, clock=lambda: now[0])
    assert h2.restore() >= 600
    assert h2.alert_list()[0]["id"] == "a1"


# ---------------------------------------------------------------------------------------- incidents
def _state(level, dmax=1.0, turb=False):
    s = json.loads(json.dumps(first_state()))
    s["status"]["level"] = level
    s["density"]["max"] = dmax
    s["turbulence"]["on"] = turb
    return s


def test_incident_grouping():
    inc = Incidents(close_after_s=60)
    w = 0.0
    for lvl, n in (("GREEN", 50), ("AMBER", 25), ("RED", 50), ("AMBER", 25), ("GREEN", 400)):
        for _ in range(n):
            w += 0.2
            inc.on_state(_state(lvl, {"GREEN": 1, "AMBER": 4.5, "RED": 6.5}[lvl]), {"w": w}, w)
    assert len(inc.items) == 1
    i = inc.items[0]
    assert i["peak_level"] == "RED" and i["peak_dmax"] == 6.5
    assert [e["level"] for e in i["escalations"]] == ["AMBER", "RED"]
    assert i["escalations"][1]["snapshots"], "grid snapshots before the escalation are kept"
    assert i["closed_w"] is not None and i["closed_w"] - 30.0 >= 60  # closed after 60 s of GREEN
    # a new incident opens on the next escalation
    inc.on_state(_state("AMBER", 4.2), {"w": w + 1}, w + 1)
    assert len(inc.items) == 2


# ---------------------------------------------------------------------------------------- control
def test_control_allow_list_and_injection(tmp_path):
    c = Controller(CFG["control"], ROOT, tmp_path)
    with pytest.raises(ControlError) as e:
        c.run("shell.exec", {"cmd": "ls"})
    assert e.value.status == 400
    with pytest.raises(ControlError) as e:
        c.run("engine.start", {"profile": "synthetic", "recording": "; rm -rf /"})
    assert e.value.status == 400
    with pytest.raises(ControlError) as e:
        c.run("engine.start", {"profile": "synthetic", "recording": "../../etc/passwd"})
    assert e.value.status == 400
    with pytest.raises(ControlError) as e:
        c.run("engine.start", {"profile": "rm -rf /"})
    assert e.value.status == 400
    with pytest.raises(ControlError):
        c.run("engine.start", {"profile": "synthetic", "recording": "not_a_recording"})
    # every argv is a fixed template; the recording only selects a listed file
    argv = c._engine_argv("synthetic", {"path": "/x/y.jsonl.gz", "calibration": "/x/c.json"})
    assert argv[1:4] == ["-m", "crowdshield.run", "--source"] and "jsonl:/x/y.jsonl.gz" in argv


def test_control_needs_pin_from_lan(tmp_path):
    c = Controller(CFG["control"], ROOT, tmp_path, allow_lan=False, pin="123456")
    c.authorize(True, None)  # localhost is fine
    with pytest.raises(ControlError) as e:
        c.authorize(False, "123456")
    assert e.value.status == 403
    c2 = Controller(CFG["control"], ROOT, tmp_path, allow_lan=True, pin="123456")
    with pytest.raises(ControlError) as e:
        c2.authorize(False, "000000")
    assert e.value.status == 401
    c2.authorize(False, "123456")


# ---------------------------------------------------------------------------------------- e2e
def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture()
def running_gateway(tmp_path):
    cfg = json.loads(json.dumps(CFG))
    cfg["data_dir"] = str(tmp_path / "data")
    cfg["calibration_files"] = {"floor": str((tmp_path / "calibration.json").relative_to(tmp_path)),
                                "head": "calibration_head.json"}
    cfg["frames"] = {"image": str(ROOT / "data/synthetic/variants/building__pole_720_body/frame.png")}
    port = free_port()
    gw = Gateway(cfg, tmp_path, allow_lan_control=False)
    gw.static_root = ROOT / "ui"
    gw.data_dir = tmp_path / "data"
    loop = asyncio.new_event_loop()
    t = threading.Thread(target=loop.run_forever, daemon=True)
    t.start()
    fut = asyncio.run_coroutine_threadsafe(gw.serve("127.0.0.1", port, mqtt="none"), loop)
    for _ in range(100):
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
            break
        except OSError:
            time.sleep(0.05)
    yield gw, port, loop
    fut.cancel()
    time.sleep(0.2)
    loop.call_soon_threadsafe(loop.stop)
    t.join(timeout=2)


def get(port, path, data=None, method=None, headers=None):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=data, method=method,
                                 headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, r.read(), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read(), dict(e.headers)


def ws_connect(port):
    s = socket.create_connection(("127.0.0.1", port), timeout=5)
    key = base64.b64encode(os.urandom(16)).decode()
    s.sendall((f"GET /ws HTTP/1.1\r\nHost: x\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
               f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
    resp = b""
    while b"\r\n\r\n" not in resp:
        resp += s.recv(1)
    assert b"101" in resp.split(b"\r\n")[0]
    assert ws_accept_key(key).encode() in resp
    return s


def ws_recv(s):
    def rx(n):
        b = b""
        while len(b) < n:
            chunk = s.recv(n - len(b))
            if not chunk:
                raise ConnectionError
            b += chunk
        return b
    b1, b2 = rx(2)
    n = b2 & 0x7F
    if n == 126:
        (n,) = struct.unpack("!H", rx(2))
    elif n == 127:
        (n,) = struct.unpack("!Q", rx(8))
    return b1 & 0x0F, rx(n)


def test_end_to_end_ingest_api_and_ws(running_gateway):
    gw, port, loop = running_gateway
    ws = ws_connect(port)
    op, data = ws_recv(ws)
    assert json.loads(data)["type"] == "hello"
    msgs = sample_messages()[:80]
    for m in msgs:
        fut = asyncio.run_coroutine_threadsafe(asyncio.sleep(0), loop)
        fut.result()
        loop.call_soon_threadsafe(gw.ingest, m["topic"], json.dumps(m["payload"]).encode())
    time.sleep(0.6)
    st, body, _ = get(port, "/api/state")
    assert st == 200 and json.loads(body)["type"] == "state"
    st, body, _ = get(port, "/api/history?since=0")
    hist = json.loads(body)
    assert st == 200 and len(hist["items"]) >= 1 and "grid" not in hist["items"][0]
    types = set()
    ws.settimeout(3)
    try:
        for _ in range(20):
            op, data = ws_recv(ws)
            types.add(json.loads(data)["type"])
            if "state" in types:
                break
    except socket.timeout:
        pass
    assert "state" in types
    # client -> server ping (masked frame) gets a pong
    ws.sendall(ws_frame(json.dumps({"type": "ping"}).encode(), mask=True))
    for _ in range(20):
        op, data = ws_recv(ws)
        if json.loads(data)["type"] == "pong":
            break
    else:
        pytest.fail("no pong")
    ws.close()
    st, body, _ = get(port, "/api/health")
    h = json.loads(body)
    assert st == 200 and h["mqtt"] == "off" and h["invalid_messages"] == 0 and "state" in h["data_age_s"]


def test_static_pages_and_traversal(running_gateway):
    gw, port, _ = running_gateway
    st, body, hdr = get(port, "/js/sim/assessor.js")
    assert st == 200 and "javascript" in hdr["Content-Type"]
    st, _, _ = get(port, "/../gateway/config.json")
    assert st in (403, 404)
    st, _, _ = get(port, "/%2e%2e/%2e%2e/etc/passwd")
    assert st in (403, 404)
    import http.client
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request("GET", "/classic")
    r = conn.getresponse()
    assert r.status == 302 and r.getheader("Location") == "/?renderer=2d"
    conn.close()


def test_calibration_api_round_trip(running_gateway, tmp_path):
    gw, port, _ = running_gateway
    good = json.loads((ROOT / "data/synthetic/variants/building__pole_720_body/calibration_clicked.json").read_text())
    st, body, _ = get(port, "/api/calibration", json.dumps(good).encode(), "POST",
                      {"Content-Type": "application/json"})
    assert st == 200, body
    st, body, _ = get(port, "/api/calibration")
    assert json.loads(body)["exists"] is True
    bad = dict(good, image_points=[good["image_points"][0]] * 4)
    st, body, _ = get(port, "/api/calibration", json.dumps(bad).encode(), "POST")
    assert st == 400 and "distinct" in json.dumps(json.loads(body))
    concave = dict(good, image_points=[good["image_points"][i] for i in (0, 2, 1, 3)])
    st, _, _ = get(port, "/api/calibration", json.dumps(concave).encode(), "POST")
    assert st == 400


def test_frame_and_control_endpoints(running_gateway):
    gw, port, _ = running_gateway
    st, body, hdr = get(port, "/api/frame.jpg")
    if st == 200:
        assert hdr["Content-Type"] == "image/jpeg" and body[:2] == b"\xff\xd8"
    st, body, _ = get(port, "/api/control", json.dumps({"action": "nope"}).encode(), "POST")
    assert st == 400
    st, body, _ = get(port, "/api/control")
    assert st == 200 and "engine.start:synthetic" in json.loads(body)["actions"]
    st, body, _ = get(port, "/api/recordings")
    names = [r["name"] for r in json.loads(body)["items"]]
    assert st == 200 and isinstance(names, list)
