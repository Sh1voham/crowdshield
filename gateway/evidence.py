"""Evidence feed for judge mode: only numbers that come from files, with the file named."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import List

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg"}


def parse_md_tables(text: str) -> List[dict]:
    """Markdown tables with the heading they sit under: [{"section","headers","rows"}]."""
    out = []
    section = ""
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        m = re.match(r"^#{1,6}\s+(.*)", line)
        if m:
            section = m.group(1).strip()
        if line.strip().startswith("|") and i + 1 < len(lines) and re.match(r"^\s*\|[\s:\-|]+\|\s*$", lines[i + 1]):
            headers = [c.strip() for c in line.strip().strip("|").split("|")]
            rows = []
            i += 2
            while i < len(lines) and lines[i].strip().startswith("|"):
                rows.append([c.strip() for c in lines[i].strip().strip("|").split("|")])
                i += 1
            out.append({"section": section, "headers": headers, "rows": rows})
            continue
        i += 1
    return out


def collect(root: Path) -> dict:
    def read(p: Path):
        return p.read_text(encoding="utf-8") if p.exists() else None

    results_md = read(root / "docs" / "RESULTS.md")
    validation_md = read(root / "VALIDATION.md") or read(root / "data" / "synthetic" / "VALIDATION.md")
    evidence_dir = root / "evidence"
    files = []
    if evidence_dir.is_dir():
        for p in sorted(evidence_dir.rglob("*")):
            if p.is_file():
                rel = p.relative_to(evidence_dir).as_posix()
                files.append({"name": rel, "url": f"/evidence/{rel}", "image": p.suffix.lower() in IMAGE_EXT,
                              "size": p.stat().st_size})
    cfg = {}
    try:
        cfg = json.loads((root / "config" / "default.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        pass
    validate_json = None
    vj = root / "docs" / "validation_variants.json"
    if vj.exists():
        try:
            validate_json = json.loads(vj.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            validate_json = None
    return {
        "results_md": results_md, "results_file": "docs/RESULTS.md" if results_md else None,
        "results_tables": parse_md_tables(results_md or ""),
        "validation": {"file": "VALIDATION.md", "tables": parse_md_tables(validation_md or "")} if validation_md else None,
        "engine_validation": {"file": "docs/validation_variants.json", "rows": validate_json} if validate_json else None,
        "evidence_files": files,
        "sources": read(root / "data" / "real" / "SOURCES.md"),
        "config": {"file": "config/default.json", "status": cfg.get("status"), "pressure": cfg.get("pressure"),
                   "recall": cfg.get("recall"), "pipeline": cfg.get("pipeline")},
        "project": read(root / "docs" / "PROJECT_DESCRIPTION.md"),
        "incidents": {"file": "docs/INCIDENTS.md", "tables": parse_md_tables(read(root / "docs" / "INCIDENTS.md") or ""),
                      "text": read(root / "docs" / "INCIDENTS.md")},
    }
