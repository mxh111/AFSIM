from __future__ import annotations

import csv
import json
import math
import re
import time
from pathlib import Path
from typing import Any, Iterable

from app.core.config import PROJECT_ROOT
from app.services.afsim_aer_reader import inspect_aer_file


REPLAY_CACHE_ROOT = PROJECT_ROOT / "runtime" / "workbench" / "replay_cache"
REPLAY_PARSER_VERSION = 3

EVENT_START_RE = re.compile(
    r"^\s*(?P<time>\d+(?:\.\d+)?)\s+(?P<kind>[A-Z][A-Z0-9_]+)\s*(?P<rest>.*)$"
)
LLA_RE = re.compile(
    r"LLA:\s+(?P<lat>\S+)\s+(?P<lon>\S+)\s+(?P<alt>[-+]?\d+(?:\.\d+)?)\s*(?P<unit>ft|feet|m|km)?",
    re.IGNORECASE,
)
RANGE_RE = re.compile(r"Range:\s+([-+]?\d+(?:\.\d+)?)\s*km", re.IGNORECASE)
PD_RE = re.compile(r"\bPd:\s+([-+]?\d+(?:\.\d+)?)", re.IGNORECASE)
REQUIRED_PD_RE = re.compile(r"\bRequiredPd:\s+([-+]?\d+(?:\.\d+)?)", re.IGNORECASE)
DETECTED_RE = re.compile(r"\bDetected:\s+([01]|true|false)", re.IGNORECASE)
HEADING_RE = re.compile(r"\bHeading:\s+([-+]?\d+(?:\.\d+)?)\s*deg", re.IGNORECASE)
SPEED_RE = re.compile(r"\bSpeed:\s+([-+]?\d+(?:\.\d+)?)\s*(m/s|kts|knots)?", re.IGNORECASE)
TYPE_RE = re.compile(r"\bType:\s+([A-Za-z0-9_\-./]+)", re.IGNORECASE)


def _read_json(path: Path, fallback: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return fallback


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


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


def _altitude_m(value: float, unit: str | None) -> float:
    unit = (unit or "m").lower()
    if unit in {"ft", "feet"}:
        return value * 0.3048
    if unit == "km":
        return value * 1000
    return value


def _parse_lla(text: str) -> dict[str, float] | None:
    match = LLA_RE.search(text)
    if not match:
        return None
    lat = _parse_coord(match.group("lat"))
    lon = _parse_coord(match.group("lon"))
    if lat is None or lon is None:
        return None
    return {
        "lat": lat,
        "lon": lon,
        "alt_m": _altitude_m(float(match.group("alt")), match.group("unit")),
    }


def _line_value(regex: re.Pattern[str], text: str) -> float | None:
    match = regex.search(text)
    if not match:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None


def _record_text_lines(path: Path, *, max_records: int, max_bytes: int) -> Iterable[list[str]]:
    if not path.exists() or path.stat().st_size == 0:
        return
    consumed = 0
    current: list[str] = []
    with path.open("r", encoding="utf-8", errors="ignore") as handle:
        for raw in handle:
            consumed += len(raw.encode("utf-8", errors="ignore"))
            line = raw.rstrip("\n")
            if EVENT_START_RE.match(line):
                if current:
                    yield current
                    max_records -= 1
                    if max_records <= 0:
                        return
                current = [line]
            elif current and line.strip():
                current.append(line)
            if consumed >= max_bytes:
                break
    if current and max_records > 0:
        yield current


def _event_title(kind: str, detected: bool | None) -> str:
    if kind == "SENSOR_DETECTION_ATTEMPT":
        return "目标被探测" if detected else "探测尝试"
    if "HIT" in kind or "KILL" in kind:
        return "命中事件"
    if "TRACK" in kind:
        return "航迹事件"
    if "MESSAGE" in kind or "COMM" in kind:
        return "通信事件"
    return kind.replace("_", " ").title()


def _event_type(kind: str, detected: bool | None) -> str:
    lower = kind.lower()
    if "detection" in lower:
        return "detected" if detected else "detection_attempt"
    if "hit" in lower or "kill" in lower:
        return "hit"
    if "intercept" in lower:
        return "intercept"
    if "lost" in lower or "drop" in lower:
        return "lost"
    if "track" in lower:
        return "track"
    if "message" in lower or "comm" in lower:
        return "communication"
    return "event"


def _entity_from_line(entity_id: str, role: str, line: str, time_s: float) -> dict[str, Any] | None:
    position = _parse_lla(line)
    if not position:
        return None
    type_match = TYPE_RE.search(line)
    speed_mps = _line_value(SPEED_RE, line) or 0.0
    return {
        "id": entity_id,
        "name": entity_id,
        "side": "neutral",
        "type": type_match.group(1) if type_match else role,
        "category": role,
        "kind": "radar" if role == "receiver" else "aircraft",
        "symbol": "radar" if role == "receiver" else "aircraft",
        "lat": position["lat"],
        "lon": position["lon"],
        "alt_m": position.get("alt_m", 0.0),
        "heading_deg": _line_value(HEADING_RE, line) or 0.0,
        "speed_mps": speed_mps,
        "speed_kts": round(speed_mps / 0.514444, 1) if speed_mps else 0.0,
        "route": [position],
        "source": "afsim-event-output",
        "metadata": {"role": role, "time": time_s},
    }


def _parse_evt_record(lines: list[str], source: str, index: int) -> dict[str, Any] | None:
    start = EVENT_START_RE.match(lines[0])
    if not start:
        return None
    time_s = float(start.group("time"))
    kind = start.group("kind")
    actors = start.group("rest").split()
    detector_id = actors[0] if actors else None
    target_id = actors[1] if len(actors) > 1 else None
    joined = " ".join(line.strip().rstrip("\\").strip() for line in lines)
    detected_match = DETECTED_RE.search(joined)
    detected: bool | None = None
    if detected_match:
        value = detected_match.group(1).lower()
        detected = value in {"1", "true"}

    receiver_line = next((line for line in lines if line.strip().startswith("Rcvr:")), "")
    target_line = next((line for line in lines if line.strip().startswith("Tgt:")), "")
    receiver = _entity_from_line(detector_id or f"receiver_{index}", "receiver", receiver_line, time_s) if receiver_line else None
    target = _entity_from_line(target_id or f"target_{index}", "target", target_line, time_s) if target_line else None

    event = {
        "id": f"{source}_{index}",
        "time": round(time_s, 5),
        "type": _event_type(kind, detected),
        "kind": kind,
        "severity": "info",
        "title": _event_title(kind, detected),
        "message": joined[:700],
        "source": source,
        "platform_id": target_id or detector_id,
        "related_ids": [item for item in [detector_id, target_id] if item],
        "detector_id": detector_id,
        "target_id": target_id,
        "range_km": _line_value(RANGE_RE, joined),
        "pd": _line_value(PD_RE, joined),
        "required_pd": _line_value(REQUIRED_PD_RE, joined),
        "detected": detected,
        "layer_id": "replay.event_markers",
    }
    if event["type"] == "hit":
        event["layer_id"] = "replay.hit_events"
    elif event["type"] == "lost":
        event["layer_id"] = "replay.lost_track"
    elif event["type"] == "intercept":
        event["layer_id"] = "replay.intercepts"

    entities = [item for item in [receiver, target] if item]
    return {"event": event, "entities": entities}


def _parse_evt_file(
    path: Path,
    *,
    max_records: int,
    max_bytes: int,
    include_observations: bool = True,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    events: list[dict[str, Any]] = []
    observations: list[dict[str, Any]] = []
    source = path.stem
    for index, record in enumerate(_record_text_lines(path, max_records=max_records, max_bytes=max_bytes), start=1):
        parsed = _parse_evt_record(record, source, index)
        if not parsed:
            continue
        event = parsed["event"]
        events.append(event)
        if include_observations:
            observations.append({"time": event["time"], "event_id": event["id"], "entities": parsed["entities"], "event": event})
    meta = {
        "name": path.name,
        "path": str(path),
        "size": path.stat().st_size,
        "parsed_records": len(events),
        "truncated": path.stat().st_size > max_bytes or len(events) >= max_records,
        "format": "event_output_text",
    }
    return events, observations, meta


def _parse_log_lines(run: dict[str, Any], start_index: int = 1) -> list[dict[str, Any]]:
    lines = [str(line) for line in (run.get("summary") or {}).get("tail", []) if str(line).strip()]
    events: list[dict[str, Any]] = []
    for offset, line in enumerate(lines[-80:], start=start_index):
        match = re.search(r"\b(start|complete)\b\s*([-+]?\d+(?:\.\d+)?)?", line, re.IGNORECASE)
        time_s = float(match.group(2)) if match and match.group(2) else float(offset)
        lower = line.lower()
        event_type = "complete" if "complete" in lower else "start" if "start" in lower else "log"
        events.append(
            {
                "id": f"run_{run.get('run_id', 'latest')}_log_{offset}",
                "time": time_s,
                "type": event_type,
                "severity": "info",
                "title": "仿真完成" if event_type == "complete" else "仿真启动" if event_type == "start" else "运行日志",
                "message": line,
                "source": "run.summary",
                "run_id": run.get("run_id"),
                "layer_id": "replay.semantic_events",
            }
        )
    return events


def _parse_csv_file(path: Path, *, max_rows: int, include_observations: bool = True) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    events: list[dict[str, Any]] = []
    observations: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8", errors="ignore", newline="") as handle:
        reader = csv.DictReader(handle)
        for index, row in enumerate(reader, start=1):
            if index > max_rows:
                break
            lower = {str(key).lower(): value for key, value in row.items()}
            time_s = float(lower.get("time") or lower.get("sim_time") or index)
            entity_id = lower.get("platform") or lower.get("platform_id") or lower.get("entity") or lower.get("target") or f"csv_entity_{index}"
            lat = lower.get("lat") or lower.get("latitude")
            lon = lower.get("lon") or lower.get("longitude")
            event = {
                "id": f"{path.stem}_csv_{index}",
                "time": time_s,
                "type": lower.get("event") or lower.get("type") or "csv_event",
                "severity": "info",
                "title": lower.get("event") or lower.get("type") or "CSV 事件",
                "message": json.dumps(row, ensure_ascii=False)[:700],
                "source": path.name,
                "platform_id": entity_id,
                "layer_id": "replay.event_markers",
            }
            events.append(event)
            if include_observations and lat is not None and lon is not None:
                try:
                    entity = {
                        "id": entity_id,
                        "name": entity_id,
                        "side": lower.get("side") or "neutral",
                        "type": lower.get("platform_type") or "csv",
                        "category": lower.get("category") or "csv",
                        "kind": lower.get("kind") or "aircraft",
                        "lat": float(lat),
                        "lon": float(lon),
                        "alt_m": float(lower.get("alt") or lower.get("alt_m") or lower.get("altitude") or 0),
                        "heading_deg": float(lower.get("heading") or lower.get("heading_deg") or 0),
                        "speed_kts": float(lower.get("speed_kts") or 0),
                        "route": [],
                        "source": "csv_event_output",
                    }
                    observations.append({"time": time_s, "event_id": event["id"], "entities": [entity], "event": event})
                except ValueError:
                    pass
    meta = {
        "name": path.name,
        "path": str(path),
        "size": path.stat().st_size,
        "parsed_records": len(events),
        "truncated": len(events) >= max_rows,
        "format": "csv_event_output",
    }
    return events, observations, meta


def _bounds_from_entities(entities: Iterable[dict[str, Any]]) -> dict[str, float] | None:
    points = [item for item in entities if item.get("lat") is not None and item.get("lon") is not None]
    if not points:
        return None
    lats = [float(item["lat"]) for item in points]
    lons = [float(item["lon"]) for item in points]
    return {"min_lat": min(lats), "max_lat": max(lats), "min_lon": min(lons), "max_lon": max(lons)}


def _merge_bounds(a: dict[str, float] | None, b: dict[str, float] | None) -> dict[str, float] | None:
    if not a:
        return b
    if not b:
        return a
    return {
        "min_lat": min(a["min_lat"], b["min_lat"]),
        "max_lat": max(a["max_lat"], b["max_lat"]),
        "min_lon": min(a["min_lon"], b["min_lon"]),
        "max_lon": max(a["max_lon"], b["max_lon"]),
    }


def _heading_deg(start: dict[str, Any], end: dict[str, Any]) -> float:
    lat1 = math.radians(float(start.get("lat", 0.0)))
    lat2 = math.radians(float(end.get("lat", 0.0)))
    d_lon = math.radians(float(end.get("lon", 0.0)) - float(start.get("lon", 0.0)))
    y = math.sin(d_lon) * math.cos(lat2)
    x = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(d_lon)
    return round((math.degrees(math.atan2(y, x)) + 360.0) % 360.0, 1)


def _build_frames(observations: list[dict[str, Any]], *, max_frames: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, float] | None]:
    if max_frames <= 0 or not observations:
        return [], [], None
    observations = sorted(observations, key=lambda item: float(item.get("time", 0.0)))
    start = float(observations[0]["time"])
    end = float(observations[-1]["time"])
    bucket_seconds = max(0.125, (end - start) / max(max_frames - 1, 1)) if end > start else 1.0
    buckets: dict[float, dict[str, Any]] = {}
    latest_entities: dict[str, dict[str, Any]] = {}
    entity_tracks: dict[str, list[dict[str, float]]] = {}
    replay_bounds: dict[str, float] | None = None

    for observation in observations:
        time_s = float(observation.get("time", 0.0))
        bucket = round(start + math.floor((time_s - start) / bucket_seconds) * bucket_seconds, 3) if end > start else round(time_s, 3)
        frame = buckets.setdefault(bucket, {"time": bucket, "events": [], "entities": {}})
        frame["events"].append(observation["event_id"])
        for entity in observation.get("entities", []):
            entity_id = str(entity.get("id") or "")
            if not entity_id:
                continue
            previous = latest_entities.get(entity_id)
            point = {"lat": float(entity["lat"]), "lon": float(entity["lon"]), "alt_m": float(entity.get("alt_m", 0.0))}
            track = entity_tracks.setdefault(entity_id, [])
            if not track or abs(track[-1]["lat"] - point["lat"]) > 1e-7 or abs(track[-1]["lon"] - point["lon"]) > 1e-7:
                track.append(point)
            entity_copy = dict(entity)
            if previous:
                entity_copy["heading_deg"] = _heading_deg(previous, entity_copy)
            entity_copy["route"] = track[-120:]
            latest_entities[entity_id] = entity_copy
            frame["entities"][entity_id] = entity_copy
            replay_bounds = _merge_bounds(replay_bounds, _bounds_from_entities([entity_copy]))

    frames: list[dict[str, Any]] = []
    current_entities: dict[str, dict[str, Any]] = {}
    for frame_id, bucket in enumerate(sorted(buckets.values(), key=lambda item: float(item["time"]))):
        current_entities.update(bucket["entities"])
        entities = sorted(current_entities.values(), key=lambda item: str(item.get("id", "")))
        frames.append(
            {
                "frame_id": frame_id,
                "source": "afsim-run-replay",
                "authoritative": True,
                "sim_time": round(float(bucket["time"]), 3),
                "entity_count": len(entities),
                "entities": entities,
                "events": bucket["events"][-40:],
                "bounds": _bounds_from_entities(entities),
            }
        )

    tracks = [
        {
            "id": f"replay_trk_{entity_id}",
            "platform_id": entity_id,
            "side": "neutral",
            "kind": "replay",
            "points": points,
            "history": points,
            "predicted": [],
            "quality": 0.7,
            "layer_ids": ["dynamic.history_tracks"],
        }
        for entity_id, points in sorted(entity_tracks.items())
    ]
    return frames[:max_frames], tracks, replay_bounds


def _file_metadata(file: dict[str, Any]) -> dict[str, Any] | None:
    path = Path(str(file.get("path", "")))
    if not path.exists():
        return None
    if path.suffix.lower() == ".aer":
        return inspect_aer_file(path)
    suffix = path.suffix.lower().lstrip(".") or "unknown"
    return {
        "name": path.name,
        "path": str(path),
        "size": path.stat().st_size,
        "format": suffix,
        "parsed_records": 0,
        "truncated": False,
        "note": "AER/DIS-like binary output is indexed as a source artifact; text/csv event streams provide replay frames.",
    }


def _cache_limits(max_events: int, max_frames: int, max_records: int, max_bytes: int) -> dict[str, int]:
    return {
        "max_events": max(0, int(max_events)),
        "max_frames": max(0, int(max_frames)),
        "max_records": max(0, int(max_records)),
        "max_bytes": max(0, int(max_bytes)),
    }


def _cache_limit_signature(limits: dict[str, int]) -> str:
    return f"e{limits['max_events']}_f{limits['max_frames']}_r{limits['max_records']}_b{limits['max_bytes']}"


def _cache_key(run: dict[str, Any], limits: dict[str, int]) -> dict[str, Any]:
    files = []
    for file in run.get("files", []):
        path = Path(str(file.get("path", "")))
        if path.exists():
            stat = path.stat()
            files.append({"path": str(path), "size": stat.st_size, "mtime": stat.st_mtime})
    return {"parser_version": REPLAY_PARSER_VERSION, "run_id": run.get("run_id"), "files": files, "limits": limits}


def build_run_replay(
    run: dict[str, Any],
    *,
    max_events: int = 700,
    max_frames: int = 260,
    max_records: int = 8000,
    max_bytes: int = 24_000_000,
    cache_dir: Path = REPLAY_CACHE_ROOT,
) -> dict[str, Any]:
    limits = _cache_limits(max_events, max_frames, max_records, max_bytes)
    max_events = limits["max_events"]
    max_frames = limits["max_frames"]
    max_records = limits["max_records"]
    max_bytes = limits["max_bytes"]
    cache_dir.mkdir(parents=True, exist_ok=True)
    run_id = str(run.get("run_id") or "latest")
    safe_run_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", run_id)
    cache_path = cache_dir / f"{safe_run_id}_{_cache_limit_signature(limits)}.json"
    key = _cache_key(run, limits)
    cached = _read_json(cache_path, None)
    if isinstance(cached, dict) and cached.get("cache_key") == key:
        return cached["replay"]

    events: list[dict[str, Any]] = []
    observations: list[dict[str, Any]] = []
    source_files: list[dict[str, Any]] = []
    include_observations = max_frames > 0
    for file in run.get("files", []):
        path = Path(str(file.get("path", "")))
        if not path.exists():
            continue
        suffix = path.suffix.lower()
        if suffix == ".evt":
            parsed_events, parsed_observations, meta = _parse_evt_file(
                path,
                max_records=max_records,
                max_bytes=max_bytes,
                include_observations=include_observations,
            )
            events.extend(parsed_events)
            observations.extend(parsed_observations)
            source_files.append(meta)
        elif suffix == ".csv":
            parsed_events, parsed_observations, meta = _parse_csv_file(path, max_rows=max_records, include_observations=include_observations)
            events.extend(parsed_events)
            observations.extend(parsed_observations)
            source_files.append(meta)
        elif suffix == ".aer":
            meta = _file_metadata(file)
            if meta:
                source_files.append(meta)
    if not events:
        events = _parse_log_lines(run)
    elif len(events) < 20:
        events.extend(_parse_log_lines(run, start_index=len(events) + 1))

    events = sorted(events, key=lambda item: float(item.get("time", 0.0)))[:max_events]
    for event in events:
        event.setdefault("run_id", run_id)
    frames, tracks, bounds = _build_frames(observations, max_frames=max_frames)
    timeline_end = max([float(event.get("time", 0.0)) for event in events] + [float(frame.get("sim_time", 0.0)) for frame in frames] + [0.0])
    replay = {
        "schema_version": "afsim-replay.v1",
        "run": run,
        "events": events,
        "frames": frames,
        "tracks": tracks,
        "bounds": bounds,
        "source_files": source_files,
        "semantic_events": [
            {
                "id": f"sem_{event['id']}",
                "time": event["time"],
                "summary": event.get("title", event.get("type", "event")),
                "detail": event.get("message", ""),
                "event_id": event["id"],
            }
            for event in events[:120]
        ],
        "summary": {
            "run_id": run.get("run_id"),
            "event_count": len(events),
            "frame_count": len(frames),
            "track_count": len(tracks),
            "source": "runtime/afsim_runs",
            "timeline": {"start": 0.0, "end": timeline_end},
            "generated_at": time.time(),
            "lightweight": max_frames <= 0,
            "limits": limits,
        },
    }
    _write_json(cache_path, {"cache_key": key, "replay": replay})
    return replay


def build_latest_replay(
    runs: list[dict[str, Any]],
    *,
    max_events: int = 700,
    max_frames: int = 260,
    max_records: int = 8000,
    max_bytes: int = 24_000_000,
    prefer_replay_frames: bool = True,
    cache_dir: Path = REPLAY_CACHE_ROOT,
) -> dict[str, Any]:
    if not runs:
        return {
            "schema_version": "afsim-replay.v1",
            "run": None,
            "events": [],
            "frames": [],
            "tracks": [],
            "bounds": None,
            "source_files": [],
            "semantic_events": [],
            "summary": {"run_id": None, "event_count": 0, "frame_count": 0, "track_count": 0, "source": "none"},
        }

    latest_run_id = runs[0].get("run_id")
    if not prefer_replay_frames:
        replay = dict(
            build_run_replay(
                runs[0],
                max_events=max_events,
                max_frames=max_frames,
                max_records=max_records,
                max_bytes=max_bytes,
                cache_dir=cache_dir,
            )
        )
        summary = dict(replay.get("summary") or {})
        summary["latest_run_id"] = latest_run_id
        summary["selected_run_index"] = 0
        summary["selection_policy"] = "latest_run_lightweight" if max_frames <= 0 else "latest_run"
        replay["summary"] = summary
        return replay

    latest_replay: dict[str, Any] | None = None
    for index, run in enumerate(runs):
        replay = build_run_replay(
            run,
            max_events=max_events,
            max_frames=max_frames,
            max_records=max_records,
            max_bytes=max_bytes,
            cache_dir=cache_dir,
        )
        if latest_replay is None:
            latest_replay = replay
        summary = replay.get("summary") if isinstance(replay.get("summary"), dict) else {}
        frame_count = int(summary.get("frame_count") or len(replay.get("frames", [])))
        if frame_count > 0:
            replay = dict(replay)
            replay_summary = dict(summary)
            replay_summary["latest_run_id"] = latest_run_id
            replay_summary["selected_run_index"] = index
            replay_summary["selection_policy"] = "latest_with_replay_frames"
            replay["summary"] = replay_summary
            return replay

    replay = dict(
        latest_replay
        or build_run_replay(
            runs[0],
            max_events=max_events,
            max_frames=max_frames,
            max_records=max_records,
            max_bytes=max_bytes,
            cache_dir=cache_dir,
        )
    )
    summary = dict(replay.get("summary") or {})
    summary["latest_run_id"] = latest_run_id
    summary["selected_run_index"] = 0
    summary["selection_policy"] = "latest_without_replay_frames"
    replay["summary"] = summary
    return replay
