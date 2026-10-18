"""Algorithms use frame timestamps only: no wall clock inside the algorithm modules."""
import re

from cs_paths import ROOT

ALGORITHM_MODULES = ["geometry.py", "tracker.py", "kinematics.py", "fields.py", "assess.py", "engine.py"]
BANNED = re.compile(r"\btime\.time\(|\bdatetime\.now\(|\bdatetime\.utcnow\(|\btime\.monotonic\(|\bperf_counter\(")


def test_no_wall_clock_in_algorithms():
    for name in ALGORITHM_MODULES:
        src = (ROOT / "crowdshield" / name).read_text(encoding="utf-8")
        hits = [l for l in src.splitlines() if BANNED.search(l)]
        assert not hits, f"{name} uses the wall clock: {hits}"


def test_no_neural_network_in_product_path():
    """All perception comes from PipeGen; the engine never runs a detector itself."""
    banned = re.compile(r"\b(torch|tensorflow|onnxruntime|ultralytics|tensorrt|cv2\.dnn)\b")
    for p in (ROOT / "crowdshield").glob("*.py"):
        src = p.read_text(encoding="utf-8")
        assert not banned.search(src), f"{p.name} imports a neural-network runtime"
