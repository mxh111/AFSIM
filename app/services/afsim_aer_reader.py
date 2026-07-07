from __future__ import annotations

from pathlib import Path
from typing import Any

from app.services.afsim_runner import afsim_paths


def discover_pymystic() -> dict[str, Any]:
    root = afsim_paths().root
    candidates = [
        root / "bin" / "python" / "pymystic.py",
        root / "swdev" / "src" / "mystic" / "python" / "pymystic.py",
    ]
    existing = [path for path in candidates if path.exists()]
    return {
        "available": bool(existing),
        "paths": [str(path) for path in existing],
        "searched": [str(path) for path in candidates],
    }


def aer_capabilities() -> dict[str, Any]:
    pymystic = discover_pymystic()
    return {
        "schema_version": "afsim-aer-reader.v1",
        "status": "available" if pymystic["available"] else "unsupported",
        "pymystic": pymystic,
        "supported_outputs": [],
        "note": "AER binary replay parsing is an extension point. Current replay frames still come from .evt/.csv outputs.",
    }


def inspect_aer_file(path: Path) -> dict[str, Any]:
    capability = aer_capabilities()
    exists = path.exists()
    return {
        "name": path.name,
        "path": str(path),
        "exists": exists,
        "size": path.stat().st_size if exists else 0,
        "format": "aer",
        "status": "unsupported",
        "reader_status": capability["status"],
        "pymystic_available": capability["pymystic"]["available"],
        "pymystic_paths": capability["pymystic"]["paths"],
        "parsed_records": 0,
        "truncated": False,
        "note": "AER source artifact indexed only; no coordinates decoded from AER yet.",
    }
