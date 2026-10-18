"""Parity: crowdshield/assess.py must reproduce the browser Assessor (ui/js/sim/assessor.js).

tools/assessor_parity.jsonl is recorded from the JS simulator at 5 Hz by
`node tools/make_parity.mjs` (exact inputs and outputs). If this fails, the port is
wrong: fix it, don't loosen the test. Don't use tools/sample_stream.jsonl here (1 Hz).
"""
import json
import math
import shutil
import subprocess

import pytest

from crowdshield.assess import Assessor
from crowdshield.config import load_config

from cs_paths import ROOT

PARITY = ROOT / "tools" / "assessor_parity.jsonl"
NUMERIC = ("trend_per_min", "t_amber_s", "t_red_s", "risk")


def same(a, b):
    if a is None or b is None:
        return a is None and b is None
    return math.isclose(a, b, rel_tol=1e-6, abs_tol=1e-9)


def test_parity_with_browser_assessor():
    rows = [json.loads(l) for l in PARITY.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(rows) > 2000
    cfg = load_config()
    a = None
    run = None
    mismatches = []
    for i, r in enumerate(rows):
        if r["run"] != run:
            run = r["run"]
            a = Assessor.from_config(cfg)
        o = a.step(r["t"], r["dmax"], r["pidx"])
        ev = [f"{e['level']}:{e['kind']}" for e in o["events"]]
        bad = []
        if o["status"] != r["status"]:
            bad.append(("status", o["status"], r["status"]))
        if o["turb_on"] != r["turb_on"]:
            bad.append(("turb_on", o["turb_on"], r["turb_on"]))
        if ev != r["events"]:
            bad.append(("events", ev, r["events"]))
        for k in NUMERIC:
            if not same(o[k], r[k]):
                bad.append((k, o[k], r[k]))
        if bad:
            mismatches.append((i, r["scenario"], r["t"], bad))
    print(f"\n{len(rows)} rows, {len(mismatches)} mismatches")
    assert not mismatches, mismatches[:5]
    kinds = {e for r in rows for e in r["events"]}
    # the recording exercises every transition the dashboard can show
    assert {"AMBER:DENSITY", "RED:DENSITY", "RED:TURBULENCE", "CLEAR:RECOVERED", "INFO:FORECAST"} <= kinds


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js not installed")
def test_parity_file_is_current():
    """Regenerating the recording from the JS sources gives the committed file."""
    before = PARITY.read_bytes()
    out = subprocess.run(["node", str(ROOT / "tools" / "make_parity.mjs")], cwd=ROOT, capture_output=True,
                         text=True, timeout=300)
    assert out.returncode == 0, out.stderr
    assert PARITY.read_bytes() == before
