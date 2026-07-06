from __future__ import annotations

import math
from typing import Any


def _points_for_platform(platform: dict[str, Any]) -> list[dict[str, float]]:
    points = [
        {
            "lat": float(point.get("lat", 0.0)),
            "lon": float(point.get("lon", 0.0)),
            "alt_m": float(point.get("alt_m", 0.0)),
        }
        for point in platform.get("positions", [])
        if point.get("lat") is not None and point.get("lon") is not None
    ]
    if points:
        return points
    position = platform.get("position")
    if position and position.get("lat") is not None and position.get("lon") is not None:
        return [
            {
                "lat": float(position.get("lat", 0.0)),
                "lon": float(position.get("lon", 0.0)),
                "alt_m": float(position.get("alt_m", 0.0)),
            }
        ]
    return []


def _interpolate(points: list[dict[str, float]], phase: float) -> dict[str, float]:
    if not points:
        return {"lat": 0.0, "lon": 0.0, "alt_m": 0.0}
    if len(points) == 1:
        return points[0]
    phase = max(0.0, min(0.999999, phase))
    scaled = phase * (len(points) - 1)
    index = min(int(scaled), len(points) - 2)
    local = scaled - index
    start = points[index]
    end = points[index + 1]
    return {
        "lat": start["lat"] + (end["lat"] - start["lat"]) * local,
        "lon": start["lon"] + (end["lon"] - start["lon"]) * local,
        "alt_m": start.get("alt_m", 0.0) + (end.get("alt_m", 0.0) - start.get("alt_m", 0.0)) * local,
    }


def _heading_deg(start: dict[str, float], end: dict[str, float]) -> float:
    lat1 = math.radians(start["lat"])
    lat2 = math.radians(end["lat"])
    d_lon = math.radians(end["lon"] - start["lon"])
    y = math.sin(d_lon) * math.cos(lat2)
    x = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(d_lon)
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0


def build_realtime_frame(
    parsed: dict[str, Any],
    sim_time: float,
    *,
    frame_id: int = 0,
    source: str = "parser-preview",
    loop_seconds: float = 120.0,
) -> dict[str, Any]:
    """Build a stable frontend frame contract from parsed AFSIM scene data.

    This preview stream animates parsed platform routes. The same frame schema is
    intended for later mission.exe event/DIS output parsing.
    """

    loop_seconds = max(loop_seconds, 1.0)
    phase = (sim_time % loop_seconds) / loop_seconds
    entities: list[dict[str, Any]] = []
    for platform in parsed.get("platforms", []):
        points = _points_for_platform(platform)
        if not points:
            continue
        position = _interpolate(points, phase)
        heading = _heading_deg(points[0], points[-1]) if len(points) > 1 else 0.0
        entities.append(
            {
                "id": platform.get("id", ""),
                "side": platform.get("side", "neutral"),
                "type": platform.get("type", ""),
                "category": platform.get("category") or platform.get("icon") or platform.get("type", ""),
                "icon": platform.get("icon", ""),
                "lat": position["lat"],
                "lon": position["lon"],
                "alt_m": position.get("alt_m", 0.0),
                "heading_deg": heading,
                "route": points,
                "source": platform.get("source", ""),
            }
        )
    return {
        "frame_id": frame_id,
        "source": source,
        "authoritative": False,
        "sim_time": round(float(sim_time), 3),
        "loop_seconds": loop_seconds,
        "bounds": parsed.get("bounds"),
        "entity_count": len(entities),
        "entities": entities,
        "events": [],
    }
