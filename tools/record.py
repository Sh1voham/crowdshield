"""Record PipeGen's detection stream to recordings/<name>.jsonl (raw DetFrames, for replays).

    python tools/record.py NAME [--source tail:~/.crowdshield/run/metadata.json] [--seconds 300]

Positions only: no video, no faces. The recording replays with
`python -m crowdshield.run --source jsonl:recordings/NAME.jsonl --realtime --source-label replay`.
"""
from __future__ import annotations

import argparse
import threading
import time

from evalcommon import ROOT
from crowdshield.io_pipegen import Adapter, open_source
from crowdshield.run import record_line


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("name")
    ap.add_argument("--source", default="tail:~/.crowdshield/run/metadata.json")
    ap.add_argument("--seconds", type=float, default=300.0, help="stop after this much stream time")
    args = ap.parse_args()
    out = ROOT / "recordings" / f"{args.name}.jsonl"
    out.parent.mkdir(exist_ok=True)
    stop = threading.Event()
    n, last_t, started = 0, 0.0, time.monotonic()
    with open(out, "w", encoding="utf-8") as f:
        try:
            for fr in open_source(args.source, Adapter(), stop=stop):
                if fr is None:
                    if time.monotonic() - started > args.seconds + 30:
                        break
                    continue
                f.write(record_line(fr) + "\n")
                n += 1
                last_t = fr.t
                if n % 250 == 0:
                    print(f"{n} frames, t = {fr.t:.1f} s", flush=True)
                if fr.t >= args.seconds:
                    break
        except KeyboardInterrupt:
            pass
        finally:
            stop.set()
    print(f"wrote {out} ({n} frames, {last_t:.1f} s of stream time)")


if __name__ == "__main__":
    main()
