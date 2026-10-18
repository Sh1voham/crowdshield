"""Health: data age per topic, GPU vitals from nvidia-smi, process state."""
from __future__ import annotations

import logging
import shutil
import subprocess
import threading
import time
from typing import Dict, List, Optional

log = logging.getLogger("gateway.health")

NVSMI_QUERY = ["--query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu,power.draw",
               "--format=csv,noheader,nounits"]


def _num(s: str) -> Optional[float]:
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def parse_nvidia_smi(line: str) -> Optional[dict]:
    parts = [p.strip() for p in line.strip().split(",")]
    if len(parts) < 6:
        return None
    return {"name": parts[0], "util": _num(parts[1]), "mem_used": _num(parts[2]), "mem_total": _num(parts[3]),
            "temp": _num(parts[4]), "power_w": _num(parts[5])}


class GpuMonitor:
    """Polls nvidia-smi every `period` seconds in a daemon thread (if nvidia-smi exists)."""

    def __init__(self, period: float = 2.0):
        self.period = period
        self.latest: Optional[dict] = None
        self.exe = shutil.which("nvidia-smi")
        self._stop = threading.Event()
        if self.exe:
            threading.Thread(target=self._run, name="gpu-monitor", daemon=True).start()

    def _run(self):
        while not self._stop.is_set():
            try:
                out = subprocess.run([self.exe, *NVSMI_QUERY], capture_output=True, text=True, timeout=5)
                if out.returncode == 0 and out.stdout.strip():
                    self.latest = parse_nvidia_smi(out.stdout.strip().splitlines()[0])
            except (OSError, subprocess.SubprocessError) as e:
                log.debug("nvidia-smi failed: %s", e)
            self._stop.wait(self.period)

    def stop(self):
        self._stop.set()


def pgrep(pattern: str) -> List[int]:
    exe = shutil.which("pgrep")
    if not exe or not pattern:
        return []
    try:
        out = subprocess.run([exe, "-f", "--", pattern], capture_output=True, text=True, timeout=3)
    except (OSError, subprocess.SubprocessError):
        return []
    return [int(p) for p in out.stdout.split() if p.isdigit()]


class DataAges:
    def __init__(self, clock=time.time):
        self.clock = clock
        self.last: Dict[str, float] = {}

    def touch(self, topic: str) -> None:
        self.last[topic] = self.clock()

    def ages(self) -> Dict[str, float]:
        now = self.clock()
        return {k: round(now - v, 1) for k, v in self.last.items()}
