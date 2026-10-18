"""Step the gate sign through NONE -> SLOW_ENTRY -> HOLD_ENTRY -> NONE and check each acknowledgement.

    python tools/actuator_test.py [--broker localhost:1883] [--prefix crowdshield]

Pass: every command is acknowledged on <prefix>/actuator/state with the matching ack_seq and shown
action within 500 ms (firmware/PRD_gate_sign.md, R4). Run it while the CrowdShield engine is
stopped, or the engine's own commands will interleave with these.
"""
from __future__ import annotations

import argparse
import json
import threading
import time

import paho.mqtt.client as mqtt

TEXT = {"NONE": "ENTRY OPEN", "SLOW_ENTRY": "SLOW ENTRY", "HOLD_ENTRY": "HOLD ENTRY"}
LEVEL = {"NONE": "GREEN", "SLOW_ENTRY": "AMBER", "HOLD_ENTRY": "RED"}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--broker", default="localhost:1883")
    ap.add_argument("--prefix", default="crowdshield")
    ap.add_argument("--hold", type=float, default=3.0)
    args = ap.parse_args()
    host, _, port = args.broker.partition(":")
    acks, got = {}, threading.Event()

    def on_message(c, u, msg):
        try:
            d = json.loads(msg.payload)
        except ValueError:
            return
        acks[d.get("ack_seq")] = (time.monotonic(), d)
        got.set()

    try:
        c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    except AttributeError:
        c = mqtt.Client()
    c.on_message = on_message
    c.connect(host or "localhost", int(port or 1883))
    c.subscribe(f"{args.prefix}/actuator/state", qos=1)
    c.loop_start()
    ok = True
    base = int(time.time()) % 100000 * 10
    for i, action in enumerate(["NONE", "SLOW_ENTRY", "HOLD_ENTRY", "NONE"]):
        seq = base + i
        msg = {"v": 1, "seq": seq, "action": action, "level": LEVEL[action], "text": TEXT[action], "zone": "test",
               "t": None, "ts": None, "advisory": True}
        sent = time.monotonic()
        c.publish(f"{args.prefix}/actuator/cmd", json.dumps(msg), qos=1, retain=True)
        deadline = sent + max(args.hold, 1.0)
        while time.monotonic() < deadline and seq not in acks:
            got.wait(0.05)
            got.clear()
        if seq in acks:
            t_ack, d = acks[seq]
            ms = (t_ack - sent) * 1000
            good = d.get("shown") == action and ms <= 500
            ok &= good
            print(f"{action:11s} seq={seq} acknowledged in {ms:.0f} ms, shown={d.get('shown')} rssi={d.get('rssi')} {'PASS' if good else 'FAIL'}")
        else:
            ok = False
            print(f"{action:11s} seq={seq} NO ACKNOWLEDGEMENT  FAIL")
        time.sleep(max(0.0, deadline - time.monotonic()))
    c.loop_stop()
    print("ACTUATOR TEST", "PASS" if ok else "FAIL")
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
