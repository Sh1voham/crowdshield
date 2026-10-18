"""SIMULATED ESP32 gate sign: a software stand-in for firmware/PRD_gate_sign.md until a board exists.

    python tools/esp32_sim.py [--broker localhost:1883] [--prefix crowdshield] [--device esp32-sim]

Follows the PRD: subscribes to <prefix>/actuator/cmd (R2), shows the advice as a terminal sign face (R3),
acknowledges on <prefix>/actuator/state at once (R4), logs one line per command (R6), shows SYSTEM OFFLINE
after 15 s without a command (R5), and rejects malformed, unknown or non-advisory commands (R7).
Every acknowledgement carries "simulated": true and rssi null (there is no radio), so the dashboard labels
it as a simulated sign. It is not FirmGen firmware and does not count as hardware.
"""
from __future__ import annotations

import argparse
import json
import signal
import sys
import time
from pathlib import Path
from typing import Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from crowdshield.schema import validate_cmd  # noqa: E402

OFFLINE_S = 15.0
FACE = {"NONE": ("ENTRY OPEN", "42"), "SLOW_ENTRY": ("SLOW ENTRY", "43"), "HOLD_ENTRY": ("HOLD ENTRY", "41"),
        "OFFLINE": ("SYSTEM OFFLINE", "47")}


class SignSim:
    """The sign's logic with no MQTT, so it can be tested: feed it payloads and times."""

    def __init__(self, device: str = "esp32-sim", t0: float = 0.0):
        self.device = device
        self.t0 = t0
        self.shown: Optional[str] = None
        self.last_cmd: Optional[float] = None
        self.offline = False

    def handle(self, payload: bytes, now: float) -> Tuple[Optional[dict], str]:
        """One command in; (acknowledgement or None, UART-style log line) out."""
        try:
            d = json.loads(payload.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return None, "cmd rejected: not JSON"
        problems = validate_cmd(d)
        if problems:
            return None, f"cmd rejected: {problems[0]}"
        self.last_cmd, self.offline, self.shown = now, False, d["action"]
        ack = {"v": 1, "device": self.device, "ack_seq": d["seq"], "shown": d["action"], "rssi": None,
               "uptime_s": int(now - self.t0), "simulated": True}
        return ack, f"cmd seq={d['seq']} action={d['action']} latency_ms={(time.monotonic() - now) * 1000:.0f}"

    def check_offline(self, now: float) -> bool:
        """True exactly once when the sign goes OFFLINE (no command for 15 s)."""
        if not self.offline and (now - (self.last_cmd if self.last_cmd is not None else self.t0)) >= OFFLINE_S:
            self.offline = True
            return True
        return False


def face(action: str, color: bool) -> str:
    text, bg = FACE.get(action, FACE["OFFLINE"])
    line = f"   [SIMULATED SIGN]  {text:^16s}" + ("  (buzzer: 3 x 200 ms every 10 s)" if action == "HOLD_ENTRY" else "")
    return f"\033[30;{bg}m{line}\033[0m" if color else line


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--broker", default="localhost:1883")
    ap.add_argument("--prefix", default="crowdshield")
    ap.add_argument("--device", default="esp32-sim")
    args = ap.parse_args()
    import paho.mqtt.client as mqtt

    host, _, port = args.broker.partition(":")
    color = sys.stdout.isatty()
    sim = SignSim(args.device, time.monotonic())

    def say(msg):
        print(msg, flush=True)

    def on_connect(c, u, flags, rc, *a):
        say(f"mqtt: connected to {host or 'localhost'}:{port or 1883}; waiting for {args.prefix}/actuator/cmd")
        c.subscribe(f"{args.prefix}/actuator/cmd", qos=1)

    def on_message(c, u, msg):
        before = sim.shown
        ack, line = sim.handle(msg.payload, time.monotonic())
        if ack is not None:
            c.publish(f"{args.prefix}/actuator/state", json.dumps(ack), qos=1)
        say(line)
        if ack is not None and ack["shown"] != before:
            say(face(ack["shown"], color))

    try:
        c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"{args.device}-{int(time.time())}")
    except AttributeError:
        c = mqtt.Client(client_id=f"{args.device}-{int(time.time())}")
    c.on_connect, c.on_message = on_connect, on_message
    c.reconnect_delay_set(min_delay=1, max_delay=5)
    c.connect(host or "localhost", int(port or 1883), keepalive=20)
    c.loop_start()
    say("SIMULATED ESP32 gate sign (no hardware). Ctrl-C stops it.")
    signal.signal(signal.SIGTERM, signal.default_int_handler)  # demo.sh stops its jobs with TERM
    try:
        while True:
            time.sleep(0.5)
            if sim.check_offline(time.monotonic()):
                sim.shown = None
                say(f"no command for {OFFLINE_S:.0f} s")
                say(face("OFFLINE", color))
    except KeyboardInterrupt:
        pass
    finally:
        c.loop_stop()
        c.disconnect()


if __name__ == "__main__":
    main()
