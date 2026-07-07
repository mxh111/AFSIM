from __future__ import annotations

import json
import math
import re
import time
import uuid
from copy import deepcopy
from pathlib import Path
from typing import Any

from app.core.config import PROJECT_ROOT
from app.services.afsim_aer_reader import aer_capabilities
from app.services.afsim_maps import map_resource_manifest
from app.services.afsim_design import read_generated_scenario
from app.services.afsim_parser import parse_demo_scenario, parse_scenario_file
from app.services.afsim_replay import build_latest_replay, build_run_replay
from app.services.afsim_runner import list_runs


WORKBENCH_ROOT = PROJECT_ROOT / "runtime" / "workbench"
LAYER_STATE_PATH = WORKBENCH_ROOT / "layer_state.json"
DRAFT_ROOT = WORKBENCH_ROOT / "drafts"
AUDIT_PATH = WORKBENCH_ROOT / "audit.jsonl"
CURRENT_DRAFT_PATH = WORKBENCH_ROOT / "current_draft.json"

LAYER_GROUPS: list[dict[str, str]] = [
    {"id": "base", "label": "基础地理图层"},
    {"id": "deployment", "label": "军事部署图层"},
    {"id": "dynamic", "label": "动态态势图层"},
    {"id": "environment", "label": "环境保障图层"},
    {"id": "intelligence", "label": "情报监控图层"},
    {"id": "electromagnetic", "label": "电磁态势图层"},
    {"id": "replay", "label": "复盘事件图层"},
]

_GROUP_LABELS = {item["id"]: item["label"] for item in LAYER_GROUPS}

_LAYER_CATALOG: list[dict[str, Any]] = [
    {"id": "base.imagery", "name": "离线影像/Blue Marble", "group": "base", "visible": True, "opacity": 0.34},
    {"id": "base.dark_basemap", "name": "深色作战底图", "group": "base", "visible": True, "opacity": 1.0},
    {"id": "base.latlon_grid", "name": "经纬网", "group": "base", "visible": True, "opacity": 0.9},
    {"id": "base.coastline", "name": "海岸线/陆海轮廓", "group": "base", "visible": True, "opacity": 0.55},
    {"id": "base.roads", "name": "道路/机动通道", "group": "base", "visible": False, "opacity": 0.44},
    {"id": "base.water", "name": "水系/河流湖泊", "group": "base", "visible": True, "opacity": 0.42},
    {"id": "base.cities", "name": "城市/居民地", "group": "base", "visible": False, "opacity": 0.5},
    {"id": "base.admin_boundaries", "name": "行政边界", "group": "base", "visible": False, "opacity": 0.42},
    {"id": "base.vegetation", "name": "植被/农田", "group": "base", "visible": False, "opacity": 0.36},
    {"id": "base.contours", "name": "等高线", "group": "base", "visible": True, "opacity": 0.38},
    {"id": "base.bathymetry", "name": "海深等值线", "group": "base", "visible": False, "opacity": 0.45},
    {"id": "base.terrain", "name": "地形阴影", "group": "base", "visible": False, "opacity": 0.45},
    {"id": "base.airspace_boundaries", "name": "空域边界", "group": "base", "visible": True, "opacity": 0.5},
    {"id": "deployment.blue_platforms", "name": "蓝方平台", "group": "deployment", "visible": True, "opacity": 1.0},
    {"id": "deployment.red_platforms", "name": "红方平台", "group": "deployment", "visible": True, "opacity": 1.0},
    {"id": "deployment.neutral_platforms", "name": "中立/未知平台", "group": "deployment", "visible": True, "opacity": 0.85},
    {"id": "deployment.bases", "name": "基地/保障点", "group": "deployment", "visible": True, "opacity": 0.9},
    {"id": "deployment.c2_nodes", "name": "指挥控制节点", "group": "deployment", "visible": True, "opacity": 0.95},
    {"id": "deployment.air_defense", "name": "防空阵地", "group": "deployment", "visible": True, "opacity": 0.95},
    {"id": "dynamic.live_tracks", "name": "实时航迹", "group": "dynamic", "visible": True, "opacity": 0.95},
    {"id": "dynamic.history_tracks", "name": "历史轨迹", "group": "dynamic", "visible": True, "opacity": 0.65},
    {"id": "dynamic.predicted_tracks", "name": "预测轨迹", "group": "dynamic", "visible": True, "opacity": 0.48},
    {"id": "dynamic.missile_tracks", "name": "导弹弹道", "group": "dynamic", "visible": True, "opacity": 0.72},
    {"id": "dynamic.engagement_events", "name": "交战事件", "group": "dynamic", "visible": True, "opacity": 0.8},
    {"id": "dynamic.alert_zones", "name": "警戒区", "group": "dynamic", "visible": False, "opacity": 0.32},
    {"id": "dynamic.strike_ranges", "name": "打击范围", "group": "dynamic", "visible": True, "opacity": 0.24},
    {"id": "dynamic.target_labels", "name": "目标标牌", "group": "dynamic", "visible": True, "opacity": 1.0},
    {"id": "dynamic.velocity_vectors", "name": "速度/航向矢量", "group": "dynamic", "visible": True, "opacity": 0.8},
    {"id": "dynamic.batch_groups", "name": "批次编组", "group": "dynamic", "visible": False, "opacity": 0.55},
    {"id": "environment.weather", "name": "天气影响区", "group": "environment", "visible": False, "opacity": 0.42},
    {"id": "environment.clouds", "name": "云层遮蔽", "group": "environment", "visible": False, "opacity": 0.35},
    {"id": "environment.fog", "name": "雾/能见度", "group": "environment", "visible": False, "opacity": 0.35},
    {"id": "environment.rain", "name": "降雨", "group": "environment", "visible": False, "opacity": 0.38},
    {"id": "environment.snow", "name": "降雪/积雪", "group": "environment", "visible": False, "opacity": 0.34},
    {"id": "environment.wind", "name": "高空风场", "group": "environment", "visible": False, "opacity": 0.4},
    {"id": "environment.ocean_state", "name": "海况", "group": "environment", "visible": False, "opacity": 0.38},
    {"id": "environment.terrain_mask", "name": "地形遮蔽", "group": "environment", "visible": False, "opacity": 0.44},
    {"id": "intelligence.detections", "name": "探测结果", "group": "intelligence", "visible": True, "opacity": 0.95},
    {"id": "intelligence.track_quality", "name": "航迹质量", "group": "intelligence", "visible": False, "opacity": 0.7},
    {"id": "intelligence.sensor_fov", "name": "传感器视场", "group": "intelligence", "visible": True, "opacity": 0.5},
    {"id": "intelligence.observation_chain", "name": "观测链", "group": "intelligence", "visible": True, "opacity": 0.75},
    {"id": "intelligence.threat_assessment", "name": "威胁等级", "group": "intelligence", "visible": False, "opacity": 0.72},
    {"id": "electromagnetic.radar_ranges", "name": "雷达探测范围", "group": "electromagnetic", "visible": True, "opacity": 0.42},
    {"id": "electromagnetic.jamming_zones", "name": "干扰压制区", "group": "electromagnetic", "visible": True, "opacity": 0.35},
    {"id": "electromagnetic.comm_coverage", "name": "通信覆盖区", "group": "electromagnetic", "visible": False, "opacity": 0.32},
    {"id": "electromagnetic.comm_links", "name": "通信链路", "group": "electromagnetic", "visible": True, "opacity": 0.68},
    {"id": "electromagnetic.power_heatmap", "name": "雷达/干扰功率热力图", "group": "electromagnetic", "visible": False, "opacity": 0.46},
    {"id": "electromagnetic.beam_direction", "name": "波束方向", "group": "electromagnetic", "visible": True, "opacity": 0.62},
    {"id": "replay.event_markers", "name": "事件标记", "group": "replay", "visible": True, "opacity": 1.0},
    {"id": "replay.hit_events", "name": "命中事件", "group": "replay", "visible": True, "opacity": 1.0},
    {"id": "replay.lost_track", "name": "丢失/中断事件", "group": "replay", "visible": True, "opacity": 0.9},
    {"id": "replay.intercepts", "name": "拦截事件", "group": "replay", "visible": True, "opacity": 0.9},
    {"id": "replay.chain_graph", "name": "链路图", "group": "replay", "visible": True, "opacity": 0.75},
    {"id": "replay.air_defense_chain", "name": "防空链", "group": "replay", "visible": True, "opacity": 0.72},
    {"id": "replay.data_gaps", "name": "数据缺口", "group": "replay", "visible": True, "opacity": 0.68},
    {"id": "replay.semantic_events", "name": "语义事件", "group": "replay", "visible": True, "opacity": 0.9},
]


def _ensure_dirs() -> None:
    WORKBENCH_ROOT.mkdir(parents=True, exist_ok=True)
    DRAFT_ROOT.mkdir(parents=True, exist_ok=True)


def _read_json(path: Path, fallback: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return fallback


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _safe_name(value: str, fallback: str = "item") -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_\-]+", "_", value.strip())
    return cleaned[:100] or fallback


def default_layer_catalog() -> list[dict[str, Any]]:
    layers = deepcopy(_LAYER_CATALOG)
    for order, layer in enumerate(layers):
        layer.setdefault("locked", False)
        layer.setdefault("queryable", True)
        layer.setdefault("focusable", True)
        layer.setdefault("exportable", True)
        layer["order"] = order
        layer["group_label"] = _GROUP_LABELS.get(str(layer["group"]), str(layer["group"]))
    return layers


def load_layer_catalog() -> list[dict[str, Any]]:
    state = _read_json(LAYER_STATE_PATH, {})
    saved = state.get("layers", {}) if isinstance(state, dict) else {}
    if isinstance(saved, list):
        saved = {str(item.get("id")): item for item in saved if item.get("id")}
    layers = default_layer_catalog()
    for layer in layers:
        layer_state = saved.get(layer["id"], {})
        if not isinstance(layer_state, dict):
            continue
        for key in ("visible", "opacity", "locked", "queryable", "focusable", "exportable"):
            if key in layer_state:
                layer[key] = layer_state[key]
        layer["opacity"] = max(0.0, min(1.0, float(layer.get("opacity", 1.0))))
        layer["visible"] = bool(layer.get("visible", True))
        layer["locked"] = bool(layer.get("locked", False))
        layer["queryable"] = bool(layer.get("queryable", True))
        layer["focusable"] = bool(layer.get("focusable", True))
        layer["exportable"] = bool(layer.get("exportable", True))
    return layers


def save_layer_state(payload: dict[str, Any]) -> dict[str, Any]:
    _ensure_dirs()
    incoming = payload.get("layers", payload)
    if not isinstance(incoming, list):
        incoming = []
    known = {layer["id"] for layer in default_layer_catalog()}
    saved: dict[str, dict[str, Any]] = {}
    for item in incoming:
        if not isinstance(item, dict) or item.get("id") not in known:
            continue
        saved[str(item["id"])] = {
            "visible": bool(item.get("visible", True)),
            "opacity": max(0.0, min(1.0, float(item.get("opacity", 1.0)))),
            "locked": bool(item.get("locked", False)),
            "queryable": bool(item.get("queryable", True)),
            "focusable": bool(item.get("focusable", True)),
            "exportable": bool(item.get("exportable", True)),
        }
    _write_json(LAYER_STATE_PATH, {"updated_at": time.time(), "layers": saved})
    return {"groups": LAYER_GROUPS, "layers": load_layer_catalog(), "path": str(LAYER_STATE_PATH)}


def _route_points(platform: dict[str, Any]) -> list[dict[str, Any]]:
    points = platform.get("positions") or []
    route = []
    for point in points:
        if point.get("lat") is None or point.get("lon") is None:
            continue
        route_point: dict[str, Any] = {
            "lat": float(point.get("lat", 0.0)),
            "lon": float(point.get("lon", 0.0)),
            "alt_m": float(point.get("alt_m", 0.0)),
            "waypoint_index": int(point.get("waypoint_index") or len(route) + 1),
        }
        for key in ("heading_deg", "speed_kts", "source_ref", "altitude_ref", "speed_ref", "heading_ref"):
            if key in point:
                route_point[key] = point[key]
        route.append(route_point)
    if route:
        return route
    position = platform.get("position")
    if isinstance(position, dict) and position.get("lat") is not None and position.get("lon") is not None:
        route_point = {
            "lat": float(position.get("lat", 0.0)),
            "lon": float(position.get("lon", 0.0)),
            "alt_m": float(position.get("alt_m", 0.0)),
            "waypoint_index": 1,
        }
        for key in ("heading_deg", "speed_kts", "source_ref", "altitude_ref", "speed_ref", "heading_ref"):
            if key in position:
                route_point[key] = position[key]
        return [route_point]
    return []


def _heading_deg(start: dict[str, float], end: dict[str, float]) -> float:
    lat1 = math.radians(start["lat"])
    lat2 = math.radians(end["lat"])
    d_lon = math.radians(end["lon"] - start["lon"])
    y = math.sin(d_lon) * math.cos(lat2)
    x = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(d_lon)
    return round((math.degrees(math.atan2(y, x)) + 360.0) % 360.0, 1)


def _kind_for(platform: dict[str, Any]) -> str:
    text = " ".join(
        str(platform.get(key, ""))
        for key in ("id", "type", "category", "icon")
    ).lower()
    if "sat" in text or "orbit" in text or "space" in text:
        return "satellite"
    if "missile" in text or "weapon" in text:
        return "missile"
    if "sub" in text or "submarine" in text:
        return "submarine"
    if "ship" in text or "naval" in text or "surface" in text:
        return "ship"
    if "radar" in text:
        return "radar"
    if "jammer" in text or "jamming" in text or "ea-" in text:
        return "jammer"
    if "c2" in text or "command" in text or "指控" in text:
        return "c2"
    if "tank" in text or "armor" in text or "armour" in text:
        return "ground"
    if "sam" in text or "air_defense" in text or "air-defense" in text or "site" in text or "ground" in text or "base" in text:
        return "ground"
    return "aircraft"


def _symbol_for(kind: str, category: str) -> str:
    if kind == "satellite":
        return "satellite"
    if kind == "missile":
        return "missile"
    if kind == "ship":
        return "surface-ship"
    if kind == "submarine":
        return "submarine"
    if kind == "radar":
        return "radar"
    if kind == "jammer":
        return "jammer"
    if kind == "c2":
        return "command-post"
    if "awacs" in category.lower():
        return "awacs"
    if "uav" in category.lower() or "recon" in category.lower():
        return "uav"
    if "bomber" in category.lower():
        return "bomber"
    return "aircraft"


def _speed_for(kind: str, category: str) -> float:
    text = category.lower()
    if kind == "satellite":
        return 14500.0
    if kind == "missile":
        return 1800.0
    if kind in {"ground", "radar", "c2"}:
        return 0.0
    if kind == "ship":
        return 28.0
    if "uav" in text:
        return 210.0
    if "awacs" in text or "tanker" in text:
        return 350.0
    return 420.0


def _haversine_km(a: dict[str, Any], b: dict[str, Any]) -> float:
    lat1 = math.radians(float(a.get("lat", 0.0)))
    lon1 = math.radians(float(a.get("lon", 0.0)))
    lat2 = math.radians(float(b.get("lat", 0.0)))
    lon2 = math.radians(float(b.get("lon", 0.0)))
    d_lat = lat2 - lat1
    d_lon = lon2 - lon1
    value = math.sin(d_lat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(d_lon / 2) ** 2
    return 6371.0 * 2.0 * math.atan2(math.sqrt(value), math.sqrt(max(0.0, 1.0 - value)))


def _bearing_deg(a: dict[str, Any], b: dict[str, Any]) -> float:
    lat1 = math.radians(float(a.get("lat", 0.0)))
    lat2 = math.radians(float(b.get("lat", 0.0)))
    d_lon = math.radians(float(b.get("lon", 0.0)) - float(a.get("lon", 0.0)))
    y = math.sin(d_lon) * math.cos(lat2)
    x = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(d_lon)
    return round((math.degrees(math.atan2(y, x)) + 360.0) % 360.0, 1)


def _merge_bounds(a: dict[str, float] | None, b: dict[str, float] | None) -> dict[str, float] | None:
    if not a:
        return b
    if not b:
        return a
    return {
        "min_lat": min(float(a["min_lat"]), float(b["min_lat"])),
        "max_lat": max(float(a["max_lat"]), float(b["max_lat"])),
        "min_lon": min(float(a["min_lon"]), float(b["min_lon"])),
        "max_lon": max(float(a["max_lon"]), float(b["max_lon"])),
    }


def _platforms_from_parsed(parsed: dict[str, Any]) -> list[dict[str, Any]]:
    platforms: list[dict[str, Any]] = []
    for index, item in enumerate(parsed.get("platforms", []), start=1):
        route = _route_points(item)
        if not route:
            continue
        position = route[0]
        category = str(item.get("category") or item.get("icon") or item.get("type") or "")
        kind = _kind_for(item)
        heading = float(route[0].get("heading_deg") if route[0].get("heading_deg") is not None else (_heading_deg(route[0], route[-1]) if len(route) > 1 else 0.0))
        speed_kts = float(route[0].get("speed_kts") if route[0].get("speed_kts") is not None else _speed_for(kind, category))
        side = str(item.get("side") or "neutral").lower()
        platform_id = str(item.get("id") or f"platform_{index}")
        source_ref = item.get("source_ref") or {"file": item.get("source", ""), "line": None}
        platforms.append(
            {
                "id": platform_id,
                "name": platform_id,
                "side": side,
                "type": item.get("type", ""),
                "category": category,
                "kind": kind,
                "symbol": _symbol_for(kind, category),
                "status": "active",
                "batch_id": f"{side}_{kind}_{(index - 1) // 4 + 1:03d}",
                "position": position,
                "lat": position["lat"],
                "lon": position["lon"],
                "alt_m": position.get("alt_m", 0.0),
                "speed_kts": speed_kts,
                "speed_mps": round(speed_kts * 0.514444, 1),
                "heading_deg": heading,
                "route": route,
                "route_metadata": item.get("route_metadata", {}),
                "commander": item.get("commander"),
                "groups": item.get("groups", []),
                "history_route": route[:1],
                "predicted_route": route[1:] if len(route) > 1 else [],
                "sensor_ids": [],
                "weapon_ids": [],
                "processor_ids": [str(proc.get("name")) for proc in item.get("processors", []) if proc.get("name")],
                "communication_ids": [str(comm.get("name")) for comm in item.get("communications", []) if comm.get("name")],
                "afsim": {
                    "source_ref": source_ref,
                    "platform_type": item.get("type", ""),
                    "platform_type_ref": item.get("platform_type_ref"),
                    "platform_type_base": item.get("platform_type_base", ""),
                    "commander": item.get("commander"),
                    "commander_ref": item.get("commander_ref"),
                    "groups": item.get("groups", []),
                    "route_metadata": item.get("route_metadata", {}),
                    "end_ref": item.get("end_ref"),
                    "fields": item.get("afsim_fields", [])[:120],
                    "sensors": item.get("sensors", []),
                    "weapons": item.get("weapons", []),
                    "processors": item.get("processors", []),
                    "communications": item.get("communications", []),
                },
                "metadata": {
                    "source": item.get("source", ""),
                    "source_ref": source_ref,
                    "commander": item.get("commander") or "",
                    "icon": item.get("icon", ""),
                    "positions": len(route),
                    "sensor_count": len(item.get("sensors", [])),
                    "weapon_count": len(item.get("weapons", [])),
                    "processor_count": len(item.get("processors", [])),
                    "communication_count": len(item.get("communications", [])),
                },
            }
        )
    return platforms


def _tracks_from_platforms(platforms: list[dict[str, Any]]) -> list[dict[str, Any]]:
    tracks = []
    for platform in platforms:
        route = platform.get("route") or []
        tracks.append(
            {
                "id": f"trk_{platform['id']}",
                "platform_id": platform["id"],
                "side": platform["side"],
                "kind": platform["kind"],
                "points": route,
                "history": platform.get("history_route", []),
                "predicted": platform.get("predicted_route", []),
                "quality": 0.82 if len(route) > 1 else 0.68,
                "layer_ids": ["dynamic.live_tracks", "dynamic.history_tracks", "dynamic.predicted_tracks"],
            }
        )
    return tracks


def _sensor_range(platform: dict[str, Any]) -> float:
    kind = platform["kind"]
    category = str(platform.get("category", "")).lower()
    if kind == "satellite":
        return 1800.0
    if kind == "radar":
        return 300.0
    if kind == "jammer":
        return 180.0
    if kind == "ship":
        return 220.0
    if kind == "ground" and ("sam" in category or "air" in category):
        return 240.0
    if "awacs" in category:
        return 550.0
    if "recon" in category:
        return 220.0
    if kind == "aircraft":
        return 120.0
    return 0.0


def _sensor_range_from_text(text: str, platform: dict[str, Any]) -> float:
    lower = text.lower()
    if "space" in lower or "sat" in lower:
        return 1800.0
    if "awacs" in lower:
        return 550.0
    if "radar" in lower:
        return 320.0
    if "esm" in lower or "elint" in lower:
        return 420.0
    if "geo" in lower or "optical" in lower or "ir" in lower:
        return 160.0
    if "acoustic" in lower or "sonar" in lower:
        return 75.0
    return _sensor_range(platform) or 120.0


def _attachment_range_km(attachment: dict[str, Any], platform: dict[str, Any]) -> float:
    value = attachment.get("range_km")
    if value is not None:
        try:
            parsed = float(value)
            if parsed > 0:
                return parsed
        except (TypeError, ValueError):
            pass
    return _sensor_range_from_text(f"{attachment.get('name', '')} {attachment.get('type', '')}", platform)


def _attachment_beam_width(attachment: dict[str, Any], fallback: float) -> float:
    if attachment.get("beam_width_deg") is not None:
        try:
            return max(1.0, min(360.0, float(attachment["beam_width_deg"])))
        except (TypeError, ValueError):
            return fallback
    limits = attachment.get("azimuth_limits_deg")
    if isinstance(limits, list) and len(limits) == 2:
        try:
            return max(1.0, min(360.0, abs(float(limits[1]) - float(limits[0]))))
        except (TypeError, ValueError):
            return fallback
    return fallback


def _sensor_type_from_text(text: str, platform: dict[str, Any]) -> str:
    lower = text.lower()
    if "esm" in lower or "elint" in lower:
        return "esm_sensor"
    if "acoustic" in lower:
        return "acoustic_sensor"
    if "sonar" in lower:
        return "sonar"
    if "geo" in lower or "optical" in lower or "ir" in lower:
        return "geometric_sensor"
    if platform["kind"] == "satellite":
        return "space_sensor"
    return "radar"


def _sensors_from_platforms(platforms: list[dict[str, Any]]) -> list[dict[str, Any]]:
    sensors: list[dict[str, Any]] = []
    for platform in platforms:
        explicit = platform.get("afsim", {}).get("sensors", [])
        for index, attachment in enumerate(explicit, start=1):
            text = f"{attachment.get('name', '')} {attachment.get('type', '')}"
            sensor_type = _sensor_type_from_text(text, platform)
            range_km = _attachment_range_km(attachment, platform)
            default_beam = 80 if sensor_type in {"esm_sensor", "geometric_sensor"} else 360
            beam_width = _attachment_beam_width(attachment, default_beam)
            sensor = {
                "id": f"sen_{platform['id']}_{_safe_name(str(attachment.get('name') or index))}",
                "platform_id": platform["id"],
                "name": str(attachment.get("name") or f"{platform['name']} sensor {index}"),
                "side": platform["side"],
                "type": sensor_type,
                "enabled": bool(attachment.get("enabled", True)),
                "range_km": range_km,
                "azimuth_deg": platform.get("heading_deg", 0.0),
                "beam_width_deg": beam_width,
                "position": platform["position"],
                "source_ref": attachment.get("source_ref"),
                "afsim_type": attachment.get("type", ""),
                "min_range_km": attachment.get("min_range_km", 0.0),
                "azimuth_limits_deg": attachment.get("azimuth_limits_deg"),
                "elevation_limits_deg": attachment.get("elevation_limits_deg"),
                "frequency_mhz": attachment.get("frequency_mhz"),
                "power_kw": attachment.get("power_kw"),
                "inherited": bool(attachment.get("inherited", False)),
                "layer_id": "electromagnetic.radar_ranges",
            }
            if sensor_type == "jammer":
                sensor["layer_id"] = "electromagnetic.jamming_zones"
            platform["sensor_ids"].append(sensor["id"])
            sensors.append(sensor)
        if explicit:
            continue
        range_km = _sensor_range(platform)
        if range_km <= 0:
            continue
        sensor_type = "jammer" if platform["kind"] == "jammer" else "radar"
        if platform["kind"] == "satellite":
            sensor_type = "space_sensor"
        sensor = {
            "id": f"sen_{platform['id']}_primary",
            "platform_id": platform["id"],
            "name": f"{platform['name']} 主传感器",
            "side": platform["side"],
            "type": sensor_type,
            "enabled": True,
            "range_km": range_km,
            "azimuth_deg": platform.get("heading_deg", 0.0),
            "beam_width_deg": 90 if sensor_type == "jammer" else 360,
            "position": platform["position"],
            "layer_id": "electromagnetic.jamming_zones" if sensor_type == "jammer" else "electromagnetic.radar_ranges",
        }
        platform["sensor_ids"].append(sensor["id"])
        sensors.append(sensor)
    return sensors


def _weapons_from_platforms(platforms: list[dict[str, Any]]) -> list[dict[str, Any]]:
    weapons: list[dict[str, Any]] = []
    for platform in platforms:
        explicit = platform.get("afsim", {}).get("weapons", [])
        for index, attachment in enumerate(explicit, start=1):
            text = f"{attachment.get('name', '')} {attachment.get('type', '')}".lower()
            if "jam" in text:
                weapon_type, name, range_km, layer_id = "jammer", "电子压制/干扰载荷", 180.0, "electromagnetic.jamming_zones"
            elif "sam" in text or "missile" in text:
                weapon_type, name, range_km, layer_id = "missile", "导弹/防空武器", 180.0, "dynamic.strike_ranges"
            elif "gun" in text:
                weapon_type, name, range_km, layer_id = "gun", "火炮/近防武器", 24.0, "dynamic.strike_ranges"
            else:
                weapon_type, name, range_km, layer_id = "weapon", "AFSIM 武器", 80.0, "dynamic.strike_ranges"
            if attachment.get("range_km") is not None:
                try:
                    range_km = max(range_km, float(attachment["range_km"]))
                except (TypeError, ValueError):
                    pass
            weapon = {
                "id": f"wpn_{platform['id']}_{_safe_name(str(attachment.get('name') or index))}",
                "platform_id": platform["id"],
                "side": platform["side"],
                "type": weapon_type,
                "name": str(attachment.get("name") or name),
                "range_km": range_km,
                "ready": True,
                "position": platform["position"],
                "source_ref": attachment.get("source_ref"),
                "afsim_type": attachment.get("type", ""),
                "quantity": attachment.get("quantity"),
                "azimuth_limits_deg": attachment.get("azimuth_limits_deg"),
                "elevation_limits_deg": attachment.get("elevation_limits_deg"),
                "inherited": bool(attachment.get("inherited", False)),
                "layer_id": layer_id,
            }
            platform["weapon_ids"].append(weapon["id"])
            weapons.append(weapon)
        if explicit:
            continue
        kind = platform["kind"]
        category = str(platform.get("category", "")).lower()
        spec: tuple[str, str, float] | None = None
        if kind == "ground" and ("sam" in category or "air" in category):
            spec = ("sam", "区域防空火力单元", 180.0)
        elif kind == "ship":
            spec = ("ship_missile", "舰载防空/反舰武器", 160.0)
        elif kind == "aircraft" and "bomber" in category:
            spec = ("strike", "远程打击武器", 260.0)
        elif kind == "aircraft":
            spec = ("aam", "空空武器", 80.0)
        elif kind == "missile":
            spec = ("missile", "导弹飞行器", 40.0)
        if spec is None:
            continue
        weapon = {
            "id": f"wpn_{platform['id']}_{spec[0]}",
            "platform_id": platform["id"],
            "side": platform["side"],
            "type": spec[0],
            "name": spec[1],
            "range_km": spec[2],
            "ready": True,
            "position": platform["position"],
            "layer_id": "deployment.air_defense" if spec[0] == "sam" else "dynamic.live_tracks",
        }
        platform["weapon_ids"].append(weapon["id"])
        weapons.append(weapon)
    return weapons


def _detections(platforms: list[dict[str, Any]], sensors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    detections: list[dict[str, Any]] = []
    by_id = {platform["id"]: platform for platform in platforms}
    for sensor in sensors:
        if sensor["type"] == "jammer" or not sensor.get("enabled", True):
            continue
        detector = by_id.get(sensor["platform_id"])
        if not detector:
            continue
        for target in platforms:
            if target["id"] == detector["id"] or target["side"] == detector["side"]:
                continue
            distance = _haversine_km(detector["position"], target["position"])
            if distance > float(sensor["range_km"]):
                continue
            quality = max(0.15, 1.0 - distance / float(sensor["range_km"]))
            detections.append(
                {
                    "id": f"det_{sensor['id']}_{target['id']}",
                    "sensor_id": sensor["id"],
                    "detector_id": detector["id"],
                    "target_id": target["id"],
                    "target_side": target["side"],
                    "range_km": round(distance, 2),
                    "bearing_deg": _bearing_deg(detector["position"], target["position"]),
                    "quality": round(quality, 2),
                    "status": "detected",
                    "layer_id": "intelligence.detections",
                }
            )
            if len(detections) >= 500:
                return detections
    return detections


def _communications(platforms: list[dict[str, Any]]) -> list[dict[str, Any]]:
    links: list[dict[str, Any]] = []
    by_id = {item["id"]: item for item in platforms}
    for subordinate in platforms:
        commander_id = subordinate.get("commander")
        if not commander_id or commander_id not in by_id:
            continue
        commander = by_id[commander_id]
        distance = _haversine_km(commander["position"], subordinate["position"])
        links.append(
            {
                "id": f"cmd_{commander['id']}_{subordinate['id']}",
                "source_id": commander["id"],
                "target_id": subordinate["id"],
                "side": subordinate["side"],
                "range_km": round(distance, 2),
                "status": "nominal" if distance < 900 else "degraded",
                "band": "afsim_command_chain",
                "chain_type": "command",
                "source_ref": subordinate.get("afsim", {}).get("commander_ref"),
                "layer_id": "electromagnetic.comm_links",
                "layer_ids": ["electromagnetic.comm_links", "replay.chain_graph"],
            }
        )
    explicit_nodes = [
        item for item in platforms
        if item.get("afsim", {}).get("communications")
    ]
    for controller in explicit_nodes:
        for target in platforms:
            if target["id"] == controller["id"] or target["side"] != controller["side"]:
                continue
            distance = _haversine_km(controller["position"], target["position"])
            if distance > 900:
                continue
            links.append(
                {
                    "id": f"comm_explicit_{controller['id']}_{target['id']}",
                    "source_id": controller["id"],
                    "target_id": target["id"],
                    "side": controller["side"],
                    "range_km": round(distance, 2),
                    "status": "nominal" if distance < 650 else "degraded",
                    "band": "afsim_comm_transceiver",
                    "chain_type": "communication",
                    "source_ref": controller.get("afsim", {}).get("communications", [{}])[0].get("source_ref"),
                    "layer_id": "electromagnetic.comm_links",
                    "layer_ids": ["electromagnetic.comm_links", "replay.chain_graph"],
                }
            )
            if len(links) >= 260:
                return links
    control_kinds = {"c2", "radar", "ship"}
    controllers = [
        item
        for item in platforms
        if item["kind"] in control_kinds or "awacs" in str(item.get("category", "")).lower()
    ]
    for controller in controllers:
        for target in platforms:
            if target["id"] == controller["id"] or target["side"] != controller["side"]:
                continue
            distance = _haversine_km(controller["position"], target["position"])
            if distance > 850:
                continue
            links.append(
                {
                    "id": f"comm_{controller['id']}_{target['id']}",
                    "source_id": controller["id"],
                    "target_id": target["id"],
                    "side": controller["side"],
                    "range_km": round(distance, 2),
                    "status": "nominal" if distance < 650 else "degraded",
                    "band": "tactical_data_link",
                    "layer_id": "electromagnetic.comm_links",
                    "chain_type": "communication",
                    "layer_ids": ["electromagnetic.comm_links", "replay.chain_graph"],
                }
            )
            if len(links) >= 260:
                return links
    return links


def _scene_events(platforms: list[dict[str, Any]], detections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = [
        {
            "id": "evt_scene_loaded",
            "time": 0.0,
            "type": "scene_loaded",
            "severity": "info",
            "title": "场景已解析",
            "message": f"已载入 {len(platforms)} 个平台、{len(detections)} 条探测关系。",
            "platform_id": platforms[0]["id"] if platforms else None,
            "layer_id": "replay.semantic_events",
        }
    ]
    for index, detection in enumerate(detections[:20], start=1):
        events.append(
            {
                "id": f"evt_detection_{index}",
                "time": float(index),
                "type": "detected",
                "severity": "info",
                "title": "目标被探测",
                "message": f"{detection['detector_id']} 探测到 {detection['target_id']}，距离 {detection['range_km']} km。",
                "platform_id": detection["target_id"],
                "related_ids": [detection["detector_id"], detection["target_id"]],
                "layer_id": "replay.event_markers",
            }
        )
    return events


def _event_type(line: str) -> str:
    lower = line.lower()
    if "hit" in lower or "kill" in lower or "impact" in lower:
        return "hit"
    if "intercept" in lower:
        return "intercept"
    if "detect" in lower or "track" in lower:
        return "detected"
    if "lost" in lower or "drop" in lower or "disconnect" in lower:
        return "lost"
    if "warn" in lower:
        return "warning"
    if "fatal" in lower or "error" in lower:
        return "error"
    if "complete" in lower:
        return "complete"
    return "log"


def _event_time(line: str, fallback: float) -> float:
    match = re.search(r"(?<![A-Za-z])T?[+= ]?(\d+(?:\.\d+)?)\s*(?:sec|s)?", line)
    if not match:
        return fallback
    try:
        return float(match.group(1))
    except ValueError:
        return fallback


def _events_from_run(run: dict[str, Any], limit: int = 180) -> list[dict[str, Any]]:
    lines: list[tuple[str, str]] = []
    for file in run.get("files", []):
        name = str(file.get("name", ""))
        if not name.lower().endswith((".evt", ".log", ".txt", ".csv")):
            continue
        path = Path(str(file.get("path", "")))
        if not path.exists() or path.stat().st_size == 0:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")[-120000:]
        for row in text.splitlines():
            row = row.strip()
            if row:
                lines.append((name, row))
    if not lines:
        for row in (run.get("summary") or {}).get("tail", []):
            if row:
                lines.append(("run.summary", str(row)))
    events: list[dict[str, Any]] = []
    for index, (source, line) in enumerate(lines[-limit:], start=1):
        event_type = _event_type(line)
        events.append(
            {
                "id": f"run_{run.get('run_id', 'latest')}_{index}",
                "time": _event_time(line, float(index)),
                "type": event_type,
                "severity": "error" if event_type == "error" else "warning" if event_type == "warning" else "info",
                "title": {
                    "hit": "命中事件",
                    "intercept": "拦截事件",
                    "detected": "探测/航迹事件",
                    "lost": "丢失/中断事件",
                    "warning": "运行告警",
                    "error": "运行错误",
                    "complete": "仿真完成",
                }.get(event_type, "运行日志"),
                "message": line[:480],
                "source": source,
                "run_id": run.get("run_id"),
                "layer_id": "replay.hit_events" if event_type == "hit" else "replay.event_markers",
            }
        )
    return events


def latest_replay(*, lightweight: bool = False) -> dict[str, Any]:
    if lightweight:
        replay = dict(
            build_latest_replay(
                list_runs(1),
                max_events=80,
                max_frames=0,
                max_records=500,
                max_bytes=2_000_000,
                prefer_replay_frames=False,
            )
        )
        summary = dict(replay.get("summary") or {})
        summary["lightweight"] = True
        summary["full_replay_endpoint"] = "/api/afsim/replay/latest"
        replay["summary"] = summary
        return replay
    return build_latest_replay(list_runs(100))


def replay_for_run(run_id: str) -> dict[str, Any]:
    run = next((item for item in list_runs(200) if item.get("run_id") == run_id), None)
    if not run:
        raise FileNotFoundError(f"AFSIM run not found: {run_id}")
    return build_run_replay(run)


def _chain_graph(
    platforms: list[dict[str, Any]],
    detections: list[dict[str, Any]],
    communications: list[dict[str, Any]],
) -> dict[str, Any]:
    nodes = [
        {
            "id": item["id"],
            "label": item["name"],
            "side": item["side"],
            "kind": item["kind"],
            "category": item["category"],
        }
        for item in platforms
    ]
    links = []
    for detection in detections[:120]:
        links.append(
            {
                "id": f"chain_{detection['id']}",
                "source": detection["detector_id"],
                "target": detection["target_id"],
                "type": "observation",
                "quality": detection["quality"],
            }
        )
    for comm in communications[:120]:
        links.append(
            {
                "id": f"chain_{comm['id']}",
                "source": comm["source_id"],
                "target": comm["target_id"],
                "type": comm.get("chain_type") or "communication",
                "status": comm["status"],
            }
        )
    blue_nodes = [node["id"] for node in nodes if node["side"] == "blue"]
    red_nodes = [node["id"] for node in nodes if node["side"] == "red"]
    return {
        "nodes": nodes,
        "links": links,
        "chains": [
            {
                "id": "blue_observation_chain",
                "name": "蓝方观测链",
                "node_ids": blue_nodes,
                "link_type": "observation",
            },
            {
                "id": "red_observation_chain",
                "name": "红方观测链",
                "node_ids": red_nodes,
                "link_type": "observation",
            },
            {
                "id": "command_chain",
                "name": "指挥链",
                "node_ids": [node["id"] for node in nodes if any(link.get("type") == "command" and node["id"] in {link.get("source"), link.get("target")} for link in links)],
                "link_type": "command",
            },
            {
                "id": "communication_chain",
                "name": "通信链",
                "node_ids": [node["id"] for node in nodes],
                "link_type": "communication",
            },
            {
                "id": "air_defense_chain",
                "name": "防空链",
                "node_ids": [node["id"] for node in nodes if node["kind"] in {"radar", "ground", "c2"}],
                "link_type": "air_defense",
            },
            {
                "id": "strike_chain",
                "name": "打击链",
                "node_ids": blue_nodes + red_nodes,
                "link_type": "engagement",
            },
        ],
    }


def _load_parsed_scene(
    scenario_id: str | None = None,
    demo_name: str = "simple_scenario",
    input_file: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if scenario_id:
        scenario = read_generated_scenario(scenario_id)
        parsed = parse_scenario_file(Path(str(scenario["scenario_path"])))
        source = {
            "kind": "generated",
            "scenario_id": scenario_id,
            "name": (scenario.get("design") or {}).get("name", scenario_id),
            "input_file": scenario["input_file"],
            "path": scenario["scenario_path"],
            "scenario_text": scenario.get("scenario_text", ""),
        }
        return parsed, source
    parsed = parse_demo_scenario(demo_name, input_file)
    source = {
        "kind": "demo",
        "demo_name": demo_name,
        "input_file": parsed.get("input_name", input_file),
        "path": parsed.get("input_file"),
        "scenario_text": "",
    }
    return parsed, source


def _map_resource_manifest() -> dict[str, Any]:
    return map_resource_manifest()


def _environment_state(bounds: dict[str, float] | None) -> dict[str, Any]:
    center_lat = (float(bounds["min_lat"]) + float(bounds["max_lat"])) / 2 if bounds else 0.0
    terrain = "ocean" if abs(center_lat) < 8 else "desert" if abs(center_lat) < 28 else "forest" if abs(center_lat) < 55 else "snow"
    return {
        "schema_version": "afsim-environment.v1",
        "terrain_palette": ["ocean", "forest", "gobi", "desert", "city", "farmland", "hills", "mountain", "mud", "snow", "grass"],
        "dominant_terrain": terrain,
        "weather_timeline": [
            {"time": 0.0, "weather": "clear", "visibility_km": 80, "wind_dir_deg": 270, "wind_kts": 18},
            {"time": 120.0, "weather": "cloud", "visibility_km": 55, "wind_dir_deg": 285, "wind_kts": 22},
            {"time": 300.0, "weather": "fog", "visibility_km": 18, "wind_dir_deg": 300, "wind_kts": 16},
        ],
    }


def build_workbench_state(
    scenario_id: str | None = None,
    demo_name: str = "simple_scenario",
    input_file: str | None = None,
) -> dict[str, Any]:
    parsed, source = _load_parsed_scene(scenario_id, demo_name, input_file)
    layers = load_layer_catalog()
    platforms = _platforms_from_parsed(parsed)
    tracks = _tracks_from_platforms(platforms)
    sensors = _sensors_from_platforms(platforms)
    weapons = _weapons_from_platforms(platforms)
    detections = _detections(platforms, sensors)
    communications = _communications(platforms)
    replay = latest_replay(lightweight=True)
    tracks.extend(replay.get("tracks", [])[:200])
    scene_events = _scene_events(platforms, detections)
    events = sorted(scene_events + replay["events"][:80], key=lambda item: float(item.get("time", 0.0)))
    timeline = replay.get("summary", {}).get("timeline", {}) if isinstance(replay.get("summary"), dict) else {}
    replay_end = float(timeline.get("end") or 0.0)
    merged_bounds = _merge_bounds(parsed.get("bounds"), replay.get("bounds"))
    return {
        "schema_version": "afsim-workbench.v1",
        "source": source,
        "bounds": merged_bounds,
        "platforms": platforms,
        "tracks": tracks,
        "sensors": sensors,
        "weapons": weapons,
        "detections": detections,
        "communications": communications,
        "events": events,
        "layers": layers,
        "layer_groups": LAYER_GROUPS,
        "map_resources": _map_resource_manifest(),
        "environment": _environment_state(merged_bounds),
        "afsim_definitions": {
            **(parsed.get("definitions", {}) if isinstance(parsed.get("definitions"), dict) else {}),
            "platform_types": parsed.get("platform_types", {}),
        },
        "simulation_time": {
            "current": 0.0,
            "start": 0.0,
            "end": max(600.0, replay_end),
            "speed_factor": 1.0,
            "running": False,
            "frame_id": 0,
            "fps": 0,
        },
        "domains": [
            {"id": "earth", "label": "地球地理空间"},
            {"id": "near_space", "label": "临近空间"},
            {"id": "space", "label": "太空空间"},
        ],
        "replay": replay,
        "chain_graph": _chain_graph(platforms, detections, communications),
        "geojson": parsed.get("geojson") or {"type": "FeatureCollection", "features": []},
        "limits": {"max_batches": 200, "min_targets_per_batch": 4},
        "editing_workflow": {
            "patch_strategy": "controlled_json_patch",
            "draft_path": str(DRAFT_ROOT),
            "audit_path": str(AUDIT_PATH),
            "raw_afsim_write": False,
            "supported_operations": [
                "add_platform",
                "delete_platform",
                "replace_platform_position",
                "replace_route_waypoint",
                "edit_sensor_zone",
                "edit_comm_coverage",
                "edit_alert_zone",
                "edit_layer_property",
            ],
        },
        "performance_design": {
            "target_refresh_hz": 5,
            "dynamic_3d_targets": 200,
            "dynamic_2d_targets": 300,
            "query_modes": ["point_pick", "rectangle", "polygon_export_contract"],
        },
        "aer_reader": aer_capabilities(),
        "capabilities": [
            "2d_map",
            "2_5d_map",
            "3d_globe",
            "split_view",
            "layer_persistence",
            "scenario_drafts",
            "timeline_replay",
            "local_afsim_authority",
            "map_pan_zoom",
            "distance_bearing_measure",
            "rectangle_query",
            "controlled_scene_patch",
        ],
        "stats": {
            "platform_count": len(platforms),
            "track_count": len(tracks),
            "sensor_count": len(sensors),
            "weapon_count": len(weapons),
            "detection_count": len(detections),
            "communication_count": len(communications),
            "event_count": len(events),
        },
    }


def _append_audit(action: str, payload: dict[str, Any]) -> None:
    _ensure_dirs()
    item = {"created_at": time.time(), "action": action, **payload}
    with AUDIT_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(item, ensure_ascii=False) + "\n")


def save_scene_draft(payload: dict[str, Any]) -> dict[str, Any]:
    _ensure_dirs()
    name = _safe_name(str(payload.get("name") or "scene_draft"), "scene_draft")
    draft_id = f"{int(time.time())}_{name}_{uuid.uuid4().hex[:8]}"
    path = DRAFT_ROOT / f"{draft_id}.json"
    draft = {
        "draft_id": draft_id,
        "name": payload.get("name") or name,
        "source": payload.get("source") or "web",
        "author": payload.get("author") or "web",
        "created_at": time.time(),
        "scene": payload.get("scene") or {},
        "operations": payload.get("operations") or [],
    }
    _write_json(path, draft)
    _write_json(CURRENT_DRAFT_PATH, draft)
    _append_audit("draft.save", {"draft_id": draft_id, "path": str(path), "name": draft["name"]})
    return {"draft_id": draft_id, "path": str(path), "draft": draft}


def list_scene_drafts(limit: int = 100) -> dict[str, Any]:
    _ensure_dirs()
    drafts = []
    for path in sorted(DRAFT_ROOT.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True)[:limit]:
        item = _read_json(path, {})
        if not isinstance(item, dict):
            continue
        drafts.append(
            {
                "draft_id": item.get("draft_id", path.stem),
                "name": item.get("name", path.stem),
                "source": item.get("source", ""),
                "author": item.get("author", ""),
                "created_at": item.get("created_at", path.stat().st_mtime),
                "path": str(path),
                "operation_count": len(item.get("operations", [])),
            }
        )
    audit = []
    if AUDIT_PATH.exists():
        for line in AUDIT_PATH.read_text(encoding="utf-8", errors="ignore").splitlines()[-limit:]:
            try:
                audit.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return {
        "drafts": drafts,
        "audit": list(reversed(audit)),
        "current": _read_json(CURRENT_DRAFT_PATH, None) if CURRENT_DRAFT_PATH.exists() else None,
    }


def restore_scene_draft(draft_id: str) -> dict[str, Any]:
    _ensure_dirs()
    safe_id = _safe_name(draft_id, "draft")
    if safe_id != draft_id:
        raise FileNotFoundError(f"invalid draft id: {draft_id}")
    path = DRAFT_ROOT / f"{draft_id}.json"
    if not path.exists():
        raise FileNotFoundError(f"draft not found: {draft_id}")
    draft = _read_json(path, {})
    _write_json(CURRENT_DRAFT_PATH, draft)
    _append_audit("draft.restore", {"draft_id": draft_id, "path": str(path)})
    return {"draft_id": draft_id, "restored": True, "current_path": str(CURRENT_DRAFT_PATH), "draft": draft}


def draft_path_for_debug() -> str:
    return str(WORKBENCH_ROOT)
