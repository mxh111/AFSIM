from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from app.services.afsim_runner import _candidate_inputs, _safe_child, afsim_paths


PLATFORM_RE = re.compile(r"^\s*platform\s+(\S+)\s+(\S+)", re.IGNORECASE)
END_PLATFORM_RE = re.compile(r"^\s*end_platform\b", re.IGNORECASE)
INCLUDE_RE = re.compile(r"^\s*include(?:_once)?\s+(.+?)\s*$", re.IGNORECASE)


def _parse_coord(token: str) -> float | None:
    token = token.strip().lower()
    if not token:
        return None
    hemi = token[-1]
    sign = -1 if hemi in {"s", "w"} else 1
    if hemi in {"n", "s", "e", "w"}:
        token = token[:-1]
    try:
        if ":" in token:
            parts = [float(part) for part in token.split(":")]
            value = parts[0] + (parts[1] / 60 if len(parts) > 1 else 0) + (parts[2] / 3600 if len(parts) > 2 else 0)
        else:
            value = float(token)
    except ValueError:
        return None
    return sign * value


def _parse_altitude(line: str) -> float | None:
    match = re.search(r"\baltitude\s+([-+]?\d+(?:\.\d+)?)\s*(ft|feet|m|km)?", line, re.IGNORECASE)
    if not match:
        return None
    value = float(match.group(1))
    unit = (match.group(2) or "m").lower()
    if unit in {"ft", "feet"}:
        return value * 0.3048
    if unit == "km":
        return value * 1000
    return value


def _parse_position(line: str) -> dict[str, float] | None:
    match = re.search(r"\bposition\s+(\S+)\s+(\S+)", line, re.IGNORECASE)
    if not match:
        return None
    lat = _parse_coord(match.group(1))
    lon = _parse_coord(match.group(2))
    if lat is None or lon is None:
        return None
    return {"lat": lat, "lon": lon, "alt_m": _parse_altitude(line) or 0.0}


def _strip_comment(line: str) -> str:
    return line.split("#", 1)[0].strip()


def _read_lines_recursive(
    path: Path,
    visited: set[Path] | None = None,
    included_files: list[str] | None = None,
) -> list[tuple[Path, str]]:
    visited = visited or set()
    included_files = included_files if included_files is not None else []
    path = path.resolve()
    if path in visited or not path.exists():
        return []
    visited.add(path)
    included_files.append(str(path))
    rows: list[tuple[Path, str]] = []
    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = _strip_comment(raw)
        if not line:
            continue
        include = INCLUDE_RE.match(line)
        if include:
            include_path = (path.parent / include.group(1).replace("\\", "/")).resolve()
            rows.extend(_read_lines_recursive(include_path, visited, included_files))
        else:
            rows.append((path, line))
    return rows


def _bounds(platforms: list[dict[str, Any]]) -> dict[str, float] | None:
    points = [
        point
        for platform in platforms
        for point in platform.get("positions", [])
        if point.get("lat") is not None and point.get("lon") is not None
    ]
    if not points:
        return None
    lats = [float(point["lat"]) for point in points]
    lons = [float(point["lon"]) for point in points]
    return {
        "min_lat": min(lats),
        "max_lat": max(lats),
        "min_lon": min(lons),
        "max_lon": max(lons),
    }


def _geojson(platforms: list[dict[str, Any]]) -> dict[str, Any]:
    features: list[dict[str, Any]] = []
    for platform in platforms:
        positions = platform.get("positions", [])
        if not positions:
            continue
        first = positions[0]
        properties = {
            "id": platform.get("id", ""),
            "type": platform.get("type", ""),
            "side": platform.get("side", "neutral"),
            "category": platform.get("category", ""),
            "source": platform.get("source", ""),
        }
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [first["lon"], first["lat"], first.get("alt_m", 0.0)]},
                "properties": {**properties, "feature_type": "platform"},
            }
        )
        if len(positions) > 1:
            features.append(
                {
                    "type": "Feature",
                    "geometry": {
                        "type": "LineString",
                        "coordinates": [[point["lon"], point["lat"], point.get("alt_m", 0.0)] for point in positions],
                    },
                    "properties": {**properties, "feature_type": "route"},
                }
            )
    return {"type": "FeatureCollection", "features": features}


def parse_scenario_file(path: Path) -> dict[str, Any]:
    platforms: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    included_files: list[str] = []
    for source, line in _read_lines_recursive(path, included_files=included_files):
        platform_match = PLATFORM_RE.match(line)
        if platform_match:
            current = {
                "id": platform_match.group(1),
                "type": platform_match.group(2),
                "side": "neutral",
                "category": "",
                "icon": "",
                "source": str(source),
                "positions": [],
            }
            continue
        if current is None:
            continue
        if END_PLATFORM_RE.match(line):
            if current["positions"]:
                current["position"] = current["positions"][0]
            platforms.append(current)
            current = None
            continue
        lower = line.lower()
        if lower.startswith("side "):
            current["side"] = line.split()[1].lower()
        elif lower.startswith("category "):
            current["category"] = line.split(maxsplit=1)[1]
        elif lower.startswith("icon "):
            current["icon"] = line.split(maxsplit=1)[1]
        elif "position" in lower:
            position = _parse_position(line)
            if position:
                current["positions"].append(position)
    for platform in platforms:
        positions = platform.get("positions", [])
        platform["route"] = positions[1:] if len(positions) > 1 else []
    return {
        "input_file": str(path),
        "included_files": included_files,
        "platforms": platforms,
        "platform_count": len(platforms),
        "route_count": sum(1 for platform in platforms if platform.get("route")),
        "bounds": _bounds(platforms),
        "geojson": _geojson(platforms),
    }


def parse_demo_scenario(demo_name: str, input_file: str | None = None) -> dict[str, Any]:
    paths = afsim_paths()
    demo_dir = _safe_child(paths.demos_dir, demo_name)
    if input_file:
        path = _safe_child(demo_dir, input_file)
    else:
        candidates = _candidate_inputs(demo_dir)
        if not candidates:
            raise FileNotFoundError(f"no runnable .txt input found in {demo_dir}")
        path = candidates[0]
    parsed = parse_scenario_file(path)
    parsed.update({"demo_name": demo_name, "input_name": path.name, "demo_dir": str(demo_dir)})
    return parsed
