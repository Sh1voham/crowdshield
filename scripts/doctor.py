#!/usr/bin/env python3
"""Environment check for the CrowdShield demo machine (stdlib only; runs before any venv).

    python3 scripts/doctor.py [--write docs/ENVIRONMENT.md]

Prints a PASS / WARN / FAIL table: OS/WSL, display, GPU, PipeGen 0.5.1 and its container,
Docker, Mosquitto, Python, free ports, disk, LAN IP, videos.
"""
from __future__ import annotations

import argparse
import os
import platform
import shutil
import socket
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROWS = []


def run(cmd, timeout=10):
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return out.returncode, (out.stdout + out.stderr).strip()
    except (OSError, subprocess.SubprocessError) as e:
        return 127, str(e)


def check(name, status, detail):
    ROWS.append((name, status, detail))


def port_free(port):
    with socket.socket() as s:
        s.settimeout(0.5)
        try:
            s.bind(("0.0.0.0", port))
            return True
        except OSError:
            return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", help="also write the table to this Markdown file")
    args = ap.parse_args()

    ver = ""
    try:
        ver = Path("/proc/version").read_text()
    except OSError:
        pass
    wsl = "microsoft" in ver.lower()
    check("OS", "PASS", f"{platform.system()} {platform.release()}" + (" (WSL)" if wsl else ""))
    disp = os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")
    check("Display (PipeGen GUI needs X11)", "PASS" if disp else "WARN", f"DISPLAY={os.environ.get('DISPLAY')}")

    if shutil.which("nvidia-smi"):
        rc, out = run(["nvidia-smi", "--query-gpu=name,driver_version,memory.total,utilization.gpu", "--format=csv,noheader"])
        check("NVIDIA GPU", "PASS" if rc == 0 else "FAIL", out.splitlines()[0] if out else "no output")
    else:
        check("NVIDIA GPU", "FAIL", "nvidia-smi not found")

    pg = shutil.which("pipegen")
    if pg:
        rc, out = run(["pipegen", "--version"])
        check("PipeGen", "PASS" if "0.5.1" in out else "WARN", out or "no version")
    else:
        check("PipeGen", "FAIL" if not sys.platform.startswith("win") else "WARN", "pipegen not on PATH (run this inside WSL)")

    if shutil.which("docker"):
        rc, out = run(["docker", "ps", "--format", "{{.Names}} {{.Image}} {{.Status}}"])
        cont = [l for l in out.splitlines() if "craftifai" in l or "deepstream" in l]
        check("Docker", "PASS" if rc == 0 else "FAIL", "docker ps ok" if rc == 0 else out[:120])
        check("PipeGen container", "PASS" if cont else "WARN", cont[0] if cont else "not running (start PipeGen: pipegen)")
    else:
        check("Docker", "FAIL", "docker not found")

    mq = shutil.which("mosquitto") or (Path("/usr/sbin/mosquitto").exists() and "/usr/sbin/mosquitto")
    listening = not port_free(1883)
    check("Mosquitto", "PASS" if (mq and listening) else "WARN" if mq else "FAIL",
          f"{'installed' if mq else 'missing (sudo apt install -y mosquitto mosquitto-clients)'}; "
          f"{'a broker is listening on 1883' if listening else 'nothing on 1883'}")
    check("Python", "PASS" if sys.version_info >= (3, 10) else "FAIL", sys.version.split()[0])
    try:
        import venv  # noqa: F401
        check("python3-venv", "PASS", "available")
    except ImportError:
        check("python3-venv", "FAIL", "sudo apt install -y python3-venv")
    for mod in ("numpy", "paho.mqtt", "cv2", "scipy", "pytest"):
        try:
            __import__(mod)
            check(f"module {mod}", "PASS", "importable")
        except ImportError:
            check(f"module {mod}", "WARN", "not in this interpreter (use the venv: ~/.venvs/crowdshield)")
    for port in (8080, 9001, 8765):
        check(f"port {port}", "PASS" if port_free(port) else "WARN", "free" if port_free(port) else "in use")
    du = shutil.disk_usage(str(Path.home()))
    check("disk", "PASS" if du.free > 5e9 else "WARN", f"{du.free / 1e9:.0f} GB free")
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            ip = s.getsockname()[0]
    except OSError:
        ip = "unknown"
    check("LAN IP", "PASS", ip + (" (WSL NAT address: phones reach the gateway via the Windows host, see README)" if wsl else ""))
    vids = sorted((Path.home() / "videos").rglob("*.mp4")) if (Path.home() / "videos").exists() else []
    check("Videos under /home", "PASS" if vids else "WARN", ", ".join(v.name for v in vids[:5]) or "none (scripts/fetch_videos.sh)")
    cams = sorted(Path("/dev").glob("video*")) if Path("/dev").exists() else []
    check("Cameras", "PASS" if cams else "WARN", ", ".join(map(str, cams)) or "none visible (normal in WSL: use a video file or RTSP)")

    w = max(len(r[0]) for r in ROWS)
    lines = [f"{n:<{w}}  {s:<4}  {d}" for n, s, d in ROWS]
    print("\n".join(lines))
    if args.write:
        md = [f"# Environment ({datetime.now().strftime('%Y-%m-%d %H:%M')}, `python3 scripts/doctor.py`)", "",
              "| Check | Result | Detail |", "|---|---|---|"] + [f"| {n} | {s} | {d.replace('|', '/')} |" for n, s, d in ROWS]
        Path(args.write).write_text("\n".join(md) + "\n", encoding="utf-8")
        print(f"\nwrote {args.write}")
    return 1 if any(s == "FAIL" for _, s, _ in ROWS) else 0


if __name__ == "__main__":
    sys.exit(main())
