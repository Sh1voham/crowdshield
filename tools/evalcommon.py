"""Shared helpers for the evaluation tools (recordings are DetFrame JSONL written by --record)."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from crowdshield.config import load_config  # noqa: E402
from crowdshield.engine import Engine  # noqa: E402
from crowdshield.geometry import Calibration  # noqa: E402
from crowdshield.io_pipegen import Adapter, jsonl_source  # noqa: E402


def frames(path):
    return jsonl_source(path, Adapter())


def run_engine(recording, calibration, config=None, profile="default", cfg=None):
    """Yield engine snapshots for a recording (frame time only; no wall clock)."""
    cfg = cfg or load_config(config, profile)
    eng = Engine(cfg, Calibration.load(calibration))
    for fr in frames(recording):
        s = eng.process(fr)
        if s is not None:
            yield s
