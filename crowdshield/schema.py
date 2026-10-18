"""Minimal validator for the v1 messages in docs/DATA_CONTRACT.md (no dependencies).

`validate(topic_suffix, payload)` returns a list of problems; empty means valid.
Shared by the engine tests and the gateway's ingest.
"""
from __future__ import annotations

import math
from typing import Any, List

LEVELS = {"GREEN", "AMBER", "RED"}
ACTIONS = {"NONE", "SLOW_ENTRY", "HOLD_ENTRY"}
ALERT_LEVELS = {"GREEN", "AMBER", "RED", "CLEAR", "INFO"}
ALERT_KINDS = {"DENSITY", "PRESSURE", "TURBULENCE", "FORECAST", "RECOVERED", "PIPELINE"}
SOURCES = {"live", "video", "replay", "synthetic", "sim"}


class _V:
    def __init__(self):
        self.errors: List[str] = []

    def err(self, msg: str):
        if len(self.errors) < 50:
            self.errors.append(msg)

    def num(self, d: dict, key: str, path: str, nullable=False, lo=None, hi=None):
        if key not in d:
            self.err(f"{path}.{key}: missing")
            return
        v = d[key]
        if v is None:
            if not nullable:
                self.err(f"{path}.{key}: null not allowed")
            return
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
            self.err(f"{path}.{key}: expected a finite number, got {v!r}")
            return
        if lo is not None and v < lo:
            self.err(f"{path}.{key}: {v} < {lo}")
        if hi is not None and v > hi:
            self.err(f"{path}.{key}: {v} > {hi}")

    def obj(self, d: Any, key: str, path: str, nullable=False) -> dict:
        if not isinstance(d, dict) or key not in d:
            self.err(f"{path}.{key}: missing")
            return {}
        v = d[key]
        if v is None and nullable:
            return {}
        if not isinstance(v, dict):
            self.err(f"{path}.{key}: expected an object")
            return {}
        return v

    def enum(self, d: dict, key: str, path: str, allowed, nullable=False):
        v = d.get(key, "<missing>")
        if v is None and nullable:
            return
        if v not in allowed:
            self.err(f"{path}.{key}: {v!r} not in {sorted(allowed)}")

    def boolean(self, d: dict, key: str, path: str, nullable=False):
        v = d.get(key, "<missing>")
        if v is None and nullable:
            return
        if not isinstance(v, bool):
            self.err(f"{path}.{key}: expected true/false")

    def string(self, d: dict, key: str, path: str, nullable=False):
        v = d.get(key, "<missing>")
        if v is None and nullable:
            return
        if not isinstance(v, str):
            self.err(f"{path}.{key}: expected a string")


def _grid(v: _V, g: dict, rows: int, cols: int, name: str, nullable_cells: bool):
    a = g.get(name)
    if not isinstance(a, list) or len(a) != rows:
        v.err(f"grid.{name}: expected {rows} rows")
        return
    for r, row in enumerate(a):
        if not isinstance(row, list) or len(row) != cols:
            v.err(f"grid.{name}[{r}]: expected {cols} values")
            return
        for c, x in enumerate(row):
            if x is None:
                if not nullable_cells:
                    v.err(f"grid.{name}[{r}][{c}]: null not allowed")
                    return
            elif isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x):
                v.err(f"grid.{name}[{r}][{c}]: bad value {x!r}")
                return


def validate_state(p: dict) -> List[str]:
    v = _V()
    if not isinstance(p, dict):
        return ["state: not an object"]
    if p.get("v") != 1:
        v.err("v: expected 1")
    if p.get("type") != "state":
        v.err("type: expected 'state'")
    v.num(p, "t", "state", lo=0)
    v.num(p, "seq", "state", lo=0)
    v.string(p, "ts", "state")
    v.string(p, "zone", "state")
    area = v.obj(p, "area", "state")
    v.num(area, "width_m", "area", lo=1e-6)
    v.num(area, "height_m", "area", lo=1e-6)
    st = v.obj(p, "status", "state")
    v.enum(st, "level", "status", LEVELS)
    v.enum(st, "raw", "status", LEVELS)
    v.num(st, "since_t", "status", nullable=True)
    v.enum(p, "action", "state", ACTIONS)
    v.num(p, "count", "state", lo=0)
    den = v.obj(p, "density", "state")
    for k in ("max", "mean", "p95", "measured_max"):
        v.num(den, k, "density", lo=0)
    pk = den.get("peak_cell")
    if not (isinstance(pk, list) and len(pk) == 2 and all(isinstance(x, int) for x in pk)):
        v.err("density.peak_cell: expected [row, col]")
    pr = v.obj(p, "pressure", "state")
    for k in ("max", "mean", "threshold", "index"):
        v.num(pr, k, "pressure", lo=0)
    v.num(v.obj(p, "speed", "state"), "mean", "speed", nullable=True, lo=0)
    tb = v.obj(p, "turbulence", "state")
    v.boolean(tb, "on", "turbulence")
    v.num(tb, "since_t", "turbulence", nullable=True)
    fc = v.obj(p, "forecast", "state")
    for k in ("trend_per_min", "t_amber_s", "t_red_s", "r2"):
        v.num(fc, k, "forecast", nullable=True)
    v.num(p, "risk", "state", lo=0, hi=1)
    g = v.obj(p, "grid", "state")
    rows, cols = g.get("rows"), g.get("cols")
    if not (isinstance(rows, int) and isinstance(cols, int) and rows > 0 and cols > 0):
        v.err("grid.rows/cols: expected positive integers")
    else:
        v.num(g, "cell_m", "grid", lo=1e-6)
        _grid(v, g, rows, cols, "density", False)
        _grid(v, g, rows, cols, "pressure", False)
        for name in ("speed", "vx", "vy"):
            if name in g:
                _grid(v, g, rows, cols, name, True)
    tr = p.get("tracks")
    if not isinstance(tr, list):
        v.err("tracks: expected a list")
    else:
        if len(tr) > 400:
            v.err("tracks: more than 400 entries")
        for i, t in enumerate(tr[:400]):
            if not isinstance(t, dict):
                v.err(f"tracks[{i}]: expected an object")
                break
            v.num(t, "x", f"tracks[{i}]")
            v.num(t, "y", f"tracks[{i}]")
            v.num(t, "vx", f"tracks[{i}]", nullable=True)
            v.num(t, "vy", f"tracks[{i}]", nullable=True)
    pl = v.obj(p, "pipeline", "state")
    v.enum(pl, "source", "pipeline", SOURCES)
    v.num(pl, "fps", "pipeline", nullable=True, lo=0)
    v.num(pl, "latency_ms", "pipeline", nullable=True)
    return v.errors


def validate_status(p: dict) -> List[str]:
    v = _V()
    if not isinstance(p, dict):
        return ["status: not an object"]
    if p.get("v") != 1 or p.get("type") != "status":
        v.err("status: expected v=1, type='status'")
    v.enum(p, "level", "status", LEVELS, nullable=True)
    v.boolean(p, "stale", "status")
    v.string(p, "zone", "status")
    v.enum(p, "source", "status", SOURCES)
    return v.errors


def validate_alert(p: dict) -> List[str]:
    v = _V()
    if not isinstance(p, dict):
        return ["alert: not an object"]
    if p.get("v") != 1 or p.get("type") != "alert":
        v.err("alert: expected v=1, type='alert'")
    v.string(p, "id", "alert")
    v.enum(p, "level", "alert", ALERT_LEVELS)
    v.enum(p, "kind", "alert", ALERT_KINDS)
    v.string(p, "message", "alert")
    v.num(p, "t", "alert", nullable=True)
    return v.errors


def validate_cmd(p: dict) -> List[str]:
    v = _V()
    if not isinstance(p, dict):
        return ["actuator/cmd: not an object"]
    v.num(p, "seq", "cmd", lo=0)
    v.enum(p, "action", "cmd", ACTIONS)
    v.enum(p, "level", "cmd", LEVELS)
    if p.get("advisory") is not True:
        v.err("cmd.advisory: must be true")
    return v.errors


VALIDATORS = {"state": validate_state, "status": validate_status, "alerts": validate_alert,
              "actuator/cmd": validate_cmd}


def validate(topic_suffix: str, payload: Any) -> List[str]:
    fn = VALIDATORS.get(topic_suffix)
    if fn is None:
        return [] if isinstance(payload, dict) else ["payload must be an object"]
    return fn(payload)
