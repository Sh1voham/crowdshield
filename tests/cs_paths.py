"""Shared test paths (a uniquely named module: two conftest.py files would collide)."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VARIANTS = ROOT / "data" / "synthetic" / "variants"
