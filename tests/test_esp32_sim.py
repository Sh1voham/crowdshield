"""The SIMULATED gate sign follows firmware/PRD_gate_sign.md (R4, R5, R7) without a broker."""
import json
import sys

from cs_paths import ROOT

sys.path.insert(0, str(ROOT / "tools"))
from esp32_sim import OFFLINE_S, SignSim  # noqa: E402

CMD = {"v": 1, "seq": 7, "action": "SLOW_ENTRY", "level": "AMBER", "text": "SLOW ENTRY", "zone": "Gate A",
       "t": 12.0, "ts": None, "advisory": True}


def send(sim, now, **over):
    return sim.handle(json.dumps({**CMD, **over}).encode(), now)


def test_valid_command_is_acknowledged_and_labelled_simulated():
    sim = SignSim("esp32-sim", t0=100.0)
    ack, line = send(sim, 112.0)
    assert ack == {"v": 1, "device": "esp32-sim", "ack_seq": 7, "shown": "SLOW_ENTRY", "rssi": None,
                   "uptime_s": 12, "simulated": True}
    assert line.startswith("cmd seq=7 action=SLOW_ENTRY latency_ms=")
    assert sim.shown == "SLOW_ENTRY"


def test_bad_commands_are_rejected_and_the_sign_keeps_its_state():
    sim = SignSim(t0=0.0)
    send(sim, 1.0, action="HOLD_ENTRY", level="RED")
    for payload in (b"not json", b"\xff\xfe",
                    json.dumps({**CMD, "action": "OPEN_GATES"}).encode(),
                    json.dumps({**CMD, "advisory": False}).encode(),
                    json.dumps([1, 2]).encode()):
        ack, line = sim.handle(payload, 2.0)
        assert ack is None and line.startswith("cmd rejected"), payload
    assert sim.shown == "HOLD_ENTRY"


def test_offline_after_15_s_without_commands_and_recovery():
    sim = SignSim(t0=0.0)
    send(sim, 1.0)
    assert not sim.check_offline(1.0 + OFFLINE_S - 0.1)
    assert sim.check_offline(1.0 + OFFLINE_S)
    assert not sim.check_offline(1.0 + OFFLINE_S + 5)  # reported once
    send(sim, 30.0)
    assert not sim.offline
    assert sim.check_offline(30.0 + OFFLINE_S)
