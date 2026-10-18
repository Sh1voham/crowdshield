"""Allow-listed process control for the engine, PipeGen's pipeline and replays.

Commands come ONLY from the fixed table in gateway/config.json. Request data is used
to pick a table entry and, for replays, a recording NAME that must match an entry of
the server-side listing exactly. No shell is ever involved (argv lists only), and no
request string is ever placed in a command.
"""
from __future__ import annotations

import logging
import os
import re
import secrets
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional

log = logging.getLogger("gateway.control")

NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]{0,80}$")


class ControlError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def list_recordings(root: Path) -> List[dict]:
    """Replays the gateway may start: recordings/*.jsonl and the synthetic variants."""
    out = []
    rec_dir = root / "recordings"
    if rec_dir.is_dir():
        for p in sorted(rec_dir.glob("*.jsonl")):
            if NAME_RE.match(p.stem):
                out.append({"name": p.stem, "kind": "recording", "label": "REPLAY", "path": str(p),
                            "size": p.stat().st_size})
    var_dir = root / "data" / "synthetic" / "variants"
    if var_dir.is_dir():
        for d in sorted(var_dir.iterdir()):
            f = d / "detections_ds_minimal.jsonl.gz"
            if f.exists() and NAME_RE.match(d.name):
                calib = d / ("calibration_head.json" if (d / "calibration_head.json").exists() else "calibration.json")
                out.append({"name": d.name, "kind": "synthetic", "label": "SYNTHETIC", "path": str(f),
                            "calibration": str(calib), "frame": str(d / "frame.png"), "size": f.stat().st_size})
    return out


class Controller:
    def __init__(self, cfg: dict, root: Path, run_dir: Path, allow_lan: bool = False, pin: Optional[str] = None):
        self.cfg = cfg
        self.root = root
        self.run_dir = run_dir
        self.allow_lan = allow_lan
        self.pin = pin or f"{secrets.randbelow(10**6):06d}"
        self.procs: Dict[str, subprocess.Popen] = {}
        self.meta: Dict[str, dict] = {}
        run_dir.mkdir(parents=True, exist_ok=True)

    # -- auth -------------------------------------------------------------------------------
    def authorize(self, is_local: bool, pin: Optional[str]) -> None:
        if is_local:
            return
        if not self.allow_lan:
            raise ControlError(403, "Control is only allowed from this laptop (start the gateway with "
                                    "--allow-lan-control to allow other devices).")
        if not pin or not secrets.compare_digest(str(pin), self.pin):
            raise ControlError(401, "Wrong or missing PIN. The PIN is printed in the gateway terminal.")

    # -- commands ------------------------------------------------------------------------------
    def _python(self) -> str:
        return self.cfg.get("python") or sys.executable

    def _engine_argv(self, profile: str, recording: Optional[dict]) -> List[str]:
        profiles = self.cfg.get("engine_profiles", {})
        if profile not in profiles:
            raise ControlError(400, f"unknown engine profile {profile!r}; allowed: {sorted(profiles)}")
        template = profiles[profile]
        if any("{recording}" in a or "{calibration}" in a for a in template) and recording is None:
            raise ControlError(400, f"profile {profile!r} needs a recording")
        subst = {}
        if recording is not None:
            subst = {"{recording}": recording["path"], "{calibration}": recording.get("calibration", "")}
        argv = [self._python()]
        for a in template:
            for k, v in subst.items():
                a = a.replace(k, v)
            argv.append(a)
        return argv

    def describe(self) -> dict:
        """Every action with the exact command it runs (shown on the System screen)."""
        actions = {}
        for prof, tmpl in self.cfg.get("engine_profiles", {}).items():
            actions[f"engine.start:{prof}"] = [self._python(), *tmpl]
        actions["engine.stop"] = ["(stop the engine process the gateway started)"]
        ctl = self.cfg.get("pipegen_ctl")
        if ctl:
            for sub in ("run-video", "run-vms", "stop", "status"):
                actions[f"pipegen.{sub}"] = ["bash", ctl, sub]
        return actions

    def status(self) -> dict:
        out = {}
        for name, p in list(self.procs.items()):
            alive = p.poll() is None
            out[name] = {"running": alive, "pid": p.pid, "returncode": None if alive else p.returncode,
                         **self.meta.get(name, {})}
        return out

    def _start(self, name: str, argv: List[str], meta: dict) -> dict:
        self._stop(name)
        log_path = self.run_dir / f"{name}.log"
        logf = open(log_path, "ab")
        kwargs = {}
        if os.name == "posix":
            kwargs["start_new_session"] = True
        p = subprocess.Popen(argv, cwd=self.root, stdout=logf, stderr=subprocess.STDOUT, **kwargs)
        self.procs[name] = p
        self.meta[name] = dict(meta, argv=argv, started=time.time(), log=str(log_path))
        (self.run_dir / f"{name}.pid").write_text(str(p.pid))
        log.info("control: started %s pid %d: %s", name, p.pid, argv)
        return {"ok": True, "name": name, "pid": p.pid, "argv": argv}

    def _stop(self, name: str) -> dict:
        p = self.procs.get(name)
        if p is None or p.poll() is not None:
            return {"ok": True, "name": name, "running": False}
        try:
            if os.name == "posix":
                os.killpg(p.pid, signal.SIGINT)
            else:
                p.terminate()
            p.wait(timeout=6)
        except (subprocess.TimeoutExpired, ProcessLookupError, PermissionError):
            p.kill()
        log.info("control: stopped %s", name)
        return {"ok": True, "name": name, "running": False}

    def run(self, action: str, body: dict) -> dict:
        if not isinstance(action, str):
            raise ControlError(400, "action must be a string")
        if action == "engine.start":
            profile = body.get("profile")
            if not isinstance(profile, str):
                raise ControlError(400, "profile must be a string")
            recording = None
            name = body.get("recording")
            if name is not None:
                if not isinstance(name, str) or not NAME_RE.match(name):
                    raise ControlError(400, "invalid recording name")
                matches = [r for r in list_recordings(self.root) if r["name"] == name]
                if not matches:
                    raise ControlError(404, f"no recording named {name!r}")
                recording = matches[0]
            argv = self._engine_argv(profile, recording)
            return self._start("engine", argv, {"profile": profile, "recording": name})
        if action == "engine.stop":
            return self._stop("engine")
        if action.startswith("pipegen."):
            sub = action.split(".", 1)[1]
            ctl = self.cfg.get("pipegen_ctl")
            if sub not in ("run-video", "run-vms", "stop", "status") or not ctl:
                raise ControlError(400, f"unknown action {action!r}")
            script = (self.root / ctl).resolve()
            if not script.exists():
                raise ControlError(503, "PipeGen control script not available")
            argv = ["bash", str(script), sub]
            if sub in ("stop", "status"):
                out = subprocess.run(argv, cwd=self.root, capture_output=True, text=True, timeout=30)
                return {"ok": out.returncode == 0, "output": (out.stdout + out.stderr)[-4000:], "argv": argv}
            return self._start("pipegen", argv, {"mode": sub})
        raise ControlError(400, f"unknown action {action!r}")

    def shutdown(self) -> None:
        for name in list(self.procs):
            self._stop(name)
