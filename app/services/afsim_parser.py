from __future__ import annotations

import re
from copy import deepcopy
from pathlib import Path
from typing import Any

from app.services.afsim_runner import _candidate_inputs, _safe_child, afsim_paths


PLATFORM_RE = re.compile(r"^\s*platform\s+(\S+)\s+(\S+)", re.IGNORECASE)
END_PLATFORM_RE = re.compile(r"^\s*end_platform\b", re.IGNORECASE)
PLATFORM_TYPE_RE = re.compile(r"^\s*platform_type\s+(\S+)\s+(\S+)", re.IGNORECASE)
END_PLATFORM_TYPE_RE = re.compile(r"^\s*end_platform_type\b", re.IGNORECASE)
INCLUDE_RE = re.compile(r"^\s*include(?:_once)?\s+(.+?)\s*$", re.IGNORECASE)
ATTACHMENT_RE = re.compile(r"^\s*(sensor|weapon|processor|task_processor|comm)\s+(\S+)(?:\s+(\S+))?", re.IGNORECASE)
EDIT_ATTACHMENT_RE = re.compile(r"^\s*edit\s+(sensor|weapon|processor|task_processor|comm)\s+(\S+)(?:\s+(\S+))?", re.IGNORECASE)
END_ATTACHMENT_RE = re.compile(r"^\s*end_(sensor|weapon|processor|task_processor|comm)\b", re.IGNORECASE)
DISTANCE_RE = re.compile(
    r"\b(maximum_range|minimum_range|one_m2_detect_range|maximum_radius|radius_and_pk)\s+([-+]?\d+(?:\.\d+)?)\s*(ft|feet|kft|m|km|nm|nmi|mi)?",
    re.IGNORECASE,
)
POWER_RE = re.compile(r"\bpower\s+([-+]?\d+(?:\.\d+)?)\s*(w|kw|mw|dbw)?", re.IGNORECASE)
FREQ_RE = re.compile(r"\bfrequency\s+([-+]?\d+(?:\.\d+)?)\s*(hz|khz|mhz|ghz)?", re.IGNORECASE)
ANGLE_LIMIT_RE = re.compile(
    r"\b(azimuth_field_of_view|azimuth_scan_limits|azimuth_slew_limits|elevation_field_of_view|elevation_scan_limits|elevation_slew_limits)\s+"
    r"([-+]?\d+(?:\.\d+)?)\s*(?:deg|degree|degrees)?\s+([-+]?\d+(?:\.\d+)?)",
    re.IGNORECASE,
)
BEAMWIDTH_RE = re.compile(r"\b(azimuth_beamwidth|elevation_beamwidth|beam_width)\s+([-+]?\d+(?:\.\d+)?)", re.IGNORECASE)
QUANTITY_RE = re.compile(r"\bquantity\s+(\d+)", re.IGNORECASE)


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
    match = re.search(r"\baltitude\s+([-+]?\d+(?:\.\d+)?)\s*(ft|feet|kft|m|km)?", line, re.IGNORECASE)
    if not match:
        return None
    value = float(match.group(1))
    unit = (match.group(2) or "m").lower()
    if unit in {"ft", "feet"}:
        return value * 0.3048
    if unit == "kft":
        return value * 304.8
    if unit == "km":
        return value * 1000
    return value


def _parse_heading(line: str) -> float | None:
    match = re.search(r"\bheading\s+([-+]?\d+(?:\.\d+)?)\s*(deg|degree|degrees)?", line, re.IGNORECASE)
    if not match:
        return None
    return float(match.group(1)) % 360


def _parse_speed_kts(line: str) -> float | None:
    match = re.search(r"\bspeed\s+([-+]?\d+(?:\.\d+)?)\s*(kts|kt|knots|m/s|mps|km/h|kph|mph)?", line, re.IGNORECASE)
    if not match:
        return None
    value = float(match.group(1))
    unit = (match.group(2) or "kts").lower()
    if unit in {"m/s", "mps"}:
        return value / 0.514444
    if unit in {"km/h", "kph"}:
        return value / 1.852
    if unit == "mph":
        return value * 0.868976
    return value


def _distance_to_km(value: float, unit: str | None) -> float:
    unit = (unit or "m").lower()
    if unit in {"ft", "feet"}:
        return value * 0.0003048
    if unit == "kft":
        return value * 0.3048
    if unit == "m":
        return value / 1000.0
    if unit in {"nm", "nmi"}:
        return value * 1.852
    if unit == "mi":
        return value * 1.609344
    return value


def _parse_distance_km(line: str) -> float | None:
    match = DISTANCE_RE.search(line)
    if not match:
        return None
    return _distance_to_km(float(match.group(2)), match.group(3))


def _parse_power_kw(line: str) -> float | None:
    match = POWER_RE.search(line)
    if not match:
        return None
    value = float(match.group(1))
    unit = (match.group(2) or "kw").lower()
    if unit == "w":
        return value / 1000.0
    if unit == "mw":
        return value * 1000.0
    if unit == "dbw":
        return (10 ** (value / 10.0)) / 1000.0
    return value


def _parse_frequency_mhz(line: str) -> float | None:
    match = FREQ_RE.search(line)
    if not match:
        return None
    value = float(match.group(1))
    unit = (match.group(2) or "mhz").lower()
    if unit == "hz":
        return value / 1_000_000.0
    if unit == "khz":
        return value / 1000.0
    if unit == "ghz":
        return value * 1000.0
    return value


def _parse_position(line: str) -> dict[str, float] | None:
    match = re.search(r"\bposition\s+(\S+)\s+(\S+)", line, re.IGNORECASE)
    if not match:
        return None
    lat = _parse_coord(match.group(1))
    lon = _parse_coord(match.group(2))
    if lat is None or lon is None:
        return None
    position = {"lat": lat, "lon": lon, "alt_m": _parse_altitude(line) or 0.0}
    heading = _parse_heading(line)
    speed = _parse_speed_kts(line)
    if heading is not None:
        position["heading_deg"] = heading
    if speed is not None:
        position["speed_kts"] = speed
    return position


def _strip_comment(line: str) -> str:
    return line.split("#", 1)[0].strip()


def _read_lines_recursive(
    path: Path,
    visited: set[Path] | None = None,
    included_files: list[str] | None = None,
    search_roots: list[Path] | None = None,
) -> list[tuple[Path, int, str, str]]:
    visited = visited or set()
    included_files = included_files if included_files is not None else []
    path = path.resolve()
    search_roots = search_roots or [path.parent]
    if path.parent not in search_roots:
        search_roots = [*search_roots, path.parent]
    if path in visited or not path.exists():
        return []
    visited.add(path)
    included_files.append(str(path))
    rows: list[tuple[Path, int, str, str]] = []
    for line_no, raw in enumerate(path.read_text(encoding="utf-8", errors="ignore").splitlines(), start=1):
        line = _strip_comment(raw)
        if not line:
            continue
        include = INCLUDE_RE.match(line)
        if include:
            include_name = include.group(1).strip().strip('"').strip("'")
            normalized = include_name.replace("\\", "/")
            candidates = [(path.parent / normalized).resolve()]
            candidates.extend((root / normalized).resolve() for root in search_roots)
            include_path = next((candidate for candidate in candidates if candidate.exists()), candidates[0])
            rows.extend(_read_lines_recursive(include_path, visited, included_files, search_roots))
        else:
            rows.append((path, line_no, line, raw.rstrip()))
    return rows


def _source_ref(source: Path, line_no: int, raw: str) -> dict[str, Any]:
    return {"file": str(source), "line": line_no, "raw": raw.strip()}


def _blank_definitions() -> dict[str, list[dict[str, Any]]]:
    return {"sensors": [], "weapons": [], "processors": [], "communications": []}


def _definition_key(kind: str) -> str:
    if kind == "sensor":
        return "sensors"
    if kind == "weapon":
        return "weapons"
    if kind in {"processor", "task_processor"}:
        return "processors"
    return "communications"


def _attachment(line: str, source: Path, line_no: int, raw: str) -> tuple[str, dict[str, Any]] | None:
    match = ATTACHMENT_RE.match(line) or EDIT_ATTACHMENT_RE.match(line)
    if not match:
        return None
    kind = match.group(1).lower()
    mode = "edit" if line.lower().startswith("edit ") else "define"
    attachment_type = match.group(3) or ""
    if mode == "edit":
        attachment_type = ""
    key = _definition_key(kind)
    return key, {
        "name": match.group(2),
        "type": attachment_type,
        "kind": kind,
        "mode": mode,
        "source_ref": _source_ref(source, line_no, raw),
        "fields": [],
    }


def _enrich_attachment(attachment: dict[str, Any] | None, line: str, source: Path, line_no: int, raw: str) -> None:
    if attachment is None:
        return
    field_ref = _source_ref(source, line_no, raw)
    attachment.setdefault("fields", []).append(field_ref)
    distance = _parse_distance_km(line)
    if distance is not None:
        key = "min_range_km" if "minimum_range" in line.lower() else "range_km"
        if key == "range_km":
            attachment[key] = max(float(attachment.get(key, 0.0)), round(distance, 3))
        else:
            attachment[key] = round(distance, 3)
    power = _parse_power_kw(line)
    if power is not None:
        attachment["power_kw"] = round(power, 3)
    frequency = _parse_frequency_mhz(line)
    if frequency is not None:
        attachment["frequency_mhz"] = round(frequency, 3)
    angle = ANGLE_LIMIT_RE.search(line)
    if angle:
        key = "azimuth_limits_deg" if angle.group(1).lower().startswith("azimuth") else "elevation_limits_deg"
        attachment[key] = [float(angle.group(2)), float(angle.group(3))]
    beam = BEAMWIDTH_RE.search(line)
    if beam:
        attachment["beam_width_deg"] = float(beam.group(2))
    quantity = QUANTITY_RE.search(line)
    if quantity:
        attachment["quantity"] = int(quantity.group(1))
    lower = line.lower()
    if lower in {"on", "off"}:
        attachment["enabled"] = lower == "on"
    if lower.startswith("edit ") and re.search(r"\bon\b", lower):
        attachment["enabled"] = True
    if lower.startswith("edit ") and re.search(r"\boff\b", lower):
        attachment["enabled"] = False
    if lower.endswith(" on") and any(token in lower for token in ("sensor", "comm", "processor")):
        attachment["enabled"] = True
    if lower.endswith(" off") and any(token in lower for token in ("sensor", "comm", "processor")):
        attachment["enabled"] = False


def _merge_attachment(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(base)
    for key, value in override.items():
        if key == "fields":
            merged["fields"] = list(base.get("fields", [])) + list(value or [])
        elif value not in ("", None, []):
            merged[key] = value
    if override.get("source_ref"):
        merged["edit_ref"] = override.get("source_ref")
    merged["inherited"] = bool(base.get("inherited", True))
    return merged


def _apply_platform_type_defaults(platforms: list[dict[str, Any]], platform_types: dict[str, dict[str, Any]]) -> None:
    for platform in platforms:
        type_name = str(platform.get("type", ""))
        type_def = platform_types.get(type_name)
        if not type_def:
            continue
        platform["platform_type_ref"] = type_def.get("source_ref")
        platform.setdefault("platform_type_base", type_def.get("base", ""))
        if not platform.get("category") and type_def.get("category"):
            platform["category"] = type_def["category"]
        if not platform.get("icon") and type_def.get("icon"):
            platform["icon"] = type_def["icon"]
        for key in ("sensors", "weapons", "processors", "communications"):
            inherited = []
            for item in type_def.get(key, []):
                clone = deepcopy(item)
                clone["inherited"] = True
                inherited.append(clone)
            explicit = platform.get(key, [])
            if not inherited:
                continue
            if not explicit:
                platform[key] = inherited
                continue
            inherited_by_name = {str(item.get("name", "")): item for item in inherited if item.get("name")}
            merged_items: list[dict[str, Any]] = []
            used: set[str] = set()
            for item in explicit:
                name = str(item.get("name", ""))
                if name in inherited_by_name:
                    merged_items.append(_merge_attachment(inherited_by_name[name], item))
                    used.add(name)
                else:
                    merged_items.append(item)
            merged_items.extend(item for item in inherited if str(item.get("name", "")) not in used)
            platform[key] = merged_items


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
    current_type: dict[str, Any] | None = None
    current_attachment: dict[str, Any] | None = None
    platform_types: dict[str, dict[str, Any]] = {}
    definitions = _blank_definitions()
    included_files: list[str] = []
    last_position: dict[str, Any] | None = None
    in_route = False
    for source, line_no, line, raw in _read_lines_recursive(path, included_files=included_files):
        platform_type_match = PLATFORM_TYPE_RE.match(line)
        if current is None and platform_type_match:
            current_type = {
                "name": platform_type_match.group(1),
                "base": platform_type_match.group(2),
                "category": "",
                "icon": "",
                "source": str(source),
                "source_ref": _source_ref(source, line_no, raw),
                "sensors": [],
                "weapons": [],
                "processors": [],
                "communications": [],
                "zones": [],
                "afsim_fields": [],
            }
            current_attachment = None
            continue
        if current_type is not None:
            if END_PLATFORM_TYPE_RE.match(line):
                platform_types[str(current_type["name"])] = current_type
                current_type = None
                current_attachment = None
                continue
            current_type["afsim_fields"].append(_source_ref(source, line_no, raw))
            if END_ATTACHMENT_RE.match(line):
                current_attachment = None
                continue
            lower = line.lower()
            attached = _attachment(line, source, line_no, raw)
            if attached:
                current_attachment = attached[1]
                _enrich_attachment(current_attachment, line, source, line_no, raw)
                current_type[attached[0]].append(current_attachment)
                continue
            if lower.startswith("category "):
                current_type["category"] = line.split(maxsplit=1)[1]
            elif lower.startswith("icon "):
                current_type["icon"] = line.split(maxsplit=1)[1]
            elif lower.startswith("zone "):
                current_type["zones"].append({"name": line.split()[1], "source_ref": _source_ref(source, line_no, raw)})
            _enrich_attachment(current_attachment, line, source, line_no, raw)
            continue
        platform_match = PLATFORM_RE.match(line)
        if platform_match:
            current = {
                "id": platform_match.group(1),
                "type": platform_match.group(2),
                "side": "neutral",
                "category": "",
                "icon": "",
                "source": str(source),
                "source_ref": _source_ref(source, line_no, raw),
                "positions": [],
                "route_metadata": {"labels": [], "gotos": []},
                "groups": [],
                "commander": None,
                "sensors": [],
                "weapons": [],
                "processors": [],
                "communications": [],
                "afsim_fields": [],
            }
            last_position = None
            current_attachment = None
            in_route = False
            continue
        if current is None:
            attached = _attachment(line, source, line_no, raw)
            if attached:
                current_attachment = attached[1]
                _enrich_attachment(current_attachment, line, source, line_no, raw)
                definitions[attached[0]].append(current_attachment)
                continue
            if END_ATTACHMENT_RE.match(line):
                current_attachment = None
                continue
            _enrich_attachment(current_attachment, line, source, line_no, raw)
            continue
        if END_PLATFORM_RE.match(line):
            current["end_ref"] = _source_ref(source, line_no, raw)
            if current["positions"]:
                current["position"] = current["positions"][0]
            platforms.append(current)
            current = None
            last_position = None
            current_attachment = None
            in_route = False
            continue
        lower = line.lower()
        current["afsim_fields"].append(_source_ref(source, line_no, raw))
        if END_ATTACHMENT_RE.match(line):
            current_attachment = None
            continue
        attached = _attachment(line, source, line_no, raw)
        if attached:
            current_attachment = attached[1]
            _enrich_attachment(current_attachment, line, source, line_no, raw)
            current[attached[0]].append(current_attachment)
            continue
        if lower.startswith("route"):
            in_route = True
        elif lower.startswith("end_route"):
            in_route = False
            current["route_end_ref"] = _source_ref(source, line_no, raw)
        elif in_route and lower.startswith("label "):
            current["route_metadata"]["labels"].append({"name": line.split(maxsplit=1)[1], "source_ref": _source_ref(source, line_no, raw)})
        elif in_route and lower.startswith("goto "):
            current["route_metadata"]["gotos"].append({"target": line.split(maxsplit=1)[1], "source_ref": _source_ref(source, line_no, raw)})
        elif lower.startswith("commander "):
            current["commander"] = line.split()[1]
            current["commander_ref"] = _source_ref(source, line_no, raw)
        elif "group_join" in lower:
            parts = line.split()
            if "group_join" in [part.lower() for part in parts]:
                group_name = parts[-1]
                current["groups"].append({"name": group_name, "source_ref": _source_ref(source, line_no, raw)})
        if lower.startswith("side "):
            current["side"] = line.split()[1].lower()
        elif lower.startswith("category "):
            current["category"] = line.split(maxsplit=1)[1]
        elif lower.startswith("icon "):
            current["icon"] = line.split(maxsplit=1)[1]
        elif "position" in lower:
            position = _parse_position(line)
            if position:
                position["waypoint_index"] = len(current["positions"]) + 1
                position["source_ref"] = _source_ref(source, line_no, raw)
                current["positions"].append(position)
                last_position = position
        elif lower.startswith("altitude ") and last_position is not None:
            altitude = _parse_altitude(line)
            if altitude is not None:
                last_position["alt_m"] = altitude
                last_position["altitude_ref"] = _source_ref(source, line_no, raw)
        elif lower.startswith("speed ") and last_position is not None:
            speed = _parse_speed_kts(line)
            if speed is not None:
                last_position["speed_kts"] = speed
                last_position["speed_ref"] = _source_ref(source, line_no, raw)
        elif lower.startswith("heading ") and last_position is not None:
            heading = _parse_heading(line)
            if heading is not None:
                last_position["heading_deg"] = heading
                last_position["heading_ref"] = _source_ref(source, line_no, raw)
        _enrich_attachment(current_attachment, line, source, line_no, raw)
    if current is not None:
        if current["positions"]:
            current["position"] = current["positions"][0]
        platforms.append(current)
    if current_type is not None:
        platform_types[str(current_type["name"])] = current_type
    _apply_platform_type_defaults(platforms, platform_types)
    for platform in platforms:
        positions = platform.get("positions", [])
        platform["route"] = positions[1:] if len(positions) > 1 else []
    return {
        "input_file": str(path),
        "included_files": included_files,
        "definitions": definitions,
        "platform_types": platform_types,
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
