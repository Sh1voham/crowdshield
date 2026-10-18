"""python -m gateway --config gateway/config.json [--port 8080] [--mqtt localhost:1883]

Serves CrowdShield Command on http://<host>:8080/ and pushes live data on /ws.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import socket
import sys
from pathlib import Path

from .server import Gateway

ROOT = Path(__file__).resolve().parent.parent


def lan_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m gateway", description=__doc__)
    ap.add_argument("--config", default=str(ROOT / "gateway" / "config.json"))
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--mqtt", default="localhost:1883", help="broker HOST:PORT or 'none'")
    ap.add_argument("--replay-jsonl", default=None,
                    help="feed {topic,payload} lines (e.g. tools/sample_stream.jsonl) without a broker")
    ap.add_argument("--replay-speed", type=float, default=1.0)
    ap.add_argument("--allow-lan-control", action="store_true",
                    help="allow control actions from other devices (PIN required)")
    ap.add_argument("--log-level", default="INFO")
    args = ap.parse_args(argv)
    logging.basicConfig(level=getattr(logging, args.log_level.upper(), logging.INFO),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s", stream=sys.stderr)
    cfg = json.loads(Path(args.config).read_text(encoding="utf-8"))
    gw = Gateway(cfg, ROOT, allow_lan_control=args.allow_lan_control)
    ip = lan_ip()
    print(f"""
CrowdShield gateway
  Command    http://localhost:{args.port}/        LAN: http://{ip}:{args.port}/
  Steward    http://{ip}:{args.port}/steward
  Calibrate  http://localhost:{args.port}/calibrate
  Judge      http://localhost:{args.port}/judge
  Classic    http://localhost:{args.port}/classic
  Control PIN for other devices: {gw.control.pin}{'' if args.allow_lan_control else '  (LAN control is OFF)'}
""", flush=True)
    try:
        asyncio.run(gw.serve(args.host, args.port, mqtt=args.mqtt,
                             replay=Path(args.replay_jsonl) if args.replay_jsonl else None,
                             replay_speed=args.replay_speed))
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
