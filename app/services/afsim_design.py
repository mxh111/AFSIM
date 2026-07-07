from __future__ import annotations

import json
import math
import re
import shutil
import time
import uuid
from pathlib import Path
from typing import Any

from app.core.config import PROJECT_ROOT
from app.models import AFSimRoutePoint, AFSimScenarioDesign


GENERATED_ROOT = PROJECT_ROOT / "generated_scenarios"
DEFAULT_INPUT_FILE = "scenario.txt"

MOBILE_TYPES = {
    "WEB_AIRCRAFT",
    "WEB_BOMBER",
    "WEB_TANKER",
    "WEB_AWACS",
    "WEB_JAMMER",
    "WEB_UAV",
    "WEB_RECON_UAV",
}

PLATFORM_TEMPLATES: list[dict[str, Any]] = [
    {
        "id": "blue_fighter",
        "label": "蓝方战斗机",
        "type_name": "WEB_AIRCRAFT",
        "side": "blue",
        "category": "fighter",
        "icon": "F-22",
        "altitude_m": 9000,
        "speed_kts": 420,
        "heading_deg": 90,
    },
    {
        "id": "red_fighter",
        "label": "红方战斗机",
        "type_name": "WEB_AIRCRAFT",
        "side": "red",
        "category": "fighter",
        "icon": "SU-27",
        "altitude_m": 8500,
        "speed_kts": 430,
        "heading_deg": 270,
    },
    {
        "id": "blue_bomber",
        "label": "蓝方轰炸机",
        "type_name": "WEB_BOMBER",
        "side": "blue",
        "category": "bomber",
        "icon": "B-52",
        "altitude_m": 10000,
        "speed_kts": 380,
        "heading_deg": 90,
    },
    {
        "id": "red_bomber",
        "label": "红方轰炸机",
        "type_name": "WEB_BOMBER",
        "side": "red",
        "category": "bomber",
        "icon": "B-52",
        "altitude_m": 9800,
        "speed_kts": 380,
        "heading_deg": 270,
    },
    {
        "id": "awacs",
        "label": "预警机",
        "type_name": "WEB_AWACS",
        "side": "blue",
        "category": "awacs",
        "icon": "E-3",
        "altitude_m": 9500,
        "speed_kts": 350,
        "heading_deg": 90,
    },
    {
        "id": "tanker",
        "label": "空中加油机",
        "type_name": "WEB_TANKER",
        "side": "blue",
        "category": "tanker",
        "icon": "KC-135",
        "altitude_m": 8500,
        "speed_kts": 330,
        "heading_deg": 90,
    },
    {
        "id": "jammer",
        "label": "电子干扰机",
        "type_name": "WEB_JAMMER",
        "side": "blue",
        "category": "jammer",
        "icon": "EA-18G",
        "altitude_m": 8800,
        "speed_kts": 360,
        "heading_deg": 90,
    },
    {
        "id": "uav",
        "label": "无人机",
        "type_name": "WEB_UAV",
        "side": "blue",
        "category": "uav",
        "icon": "MQ-9",
        "altitude_m": 6000,
        "speed_kts": 210,
        "heading_deg": 90,
    },
    {
        "id": "red_uav",
        "label": "红方无人机",
        "type_name": "WEB_UAV",
        "side": "red",
        "category": "uav",
        "icon": "MQ-9",
        "altitude_m": 5800,
        "speed_kts": 210,
        "heading_deg": 270,
    },
    {
        "id": "recon_uav",
        "label": "侦察无人机",
        "type_name": "WEB_RECON_UAV",
        "side": "blue",
        "category": "recon",
        "icon": "MQ-9",
        "altitude_m": 7200,
        "speed_kts": 220,
        "heading_deg": 90,
    },
    {
        "id": "ground_radar",
        "label": "地面雷达站",
        "type_name": "WEB_GROUND_RADAR",
        "side": "blue",
        "category": "radar",
        "icon": "radar",
        "altitude_m": 0,
        "speed_kts": 0,
        "heading_deg": 0,
    },
    {
        "id": "red_ground_radar",
        "label": "红方地面雷达站",
        "type_name": "WEB_GROUND_RADAR",
        "side": "red",
        "category": "radar",
        "icon": "radar",
        "altitude_m": 0,
        "speed_kts": 0,
        "heading_deg": 0,
    },
    {
        "id": "c2_node",
        "label": "指控节点",
        "type_name": "WEB_C2_NODE",
        "side": "blue",
        "category": "c2",
        "icon": "command-post",
        "altitude_m": 0,
        "speed_kts": 0,
        "heading_deg": 0,
    },
    {
        "id": "red_c2_node",
        "label": "红方指控节点",
        "type_name": "WEB_C2_NODE",
        "side": "red",
        "category": "c2",
        "icon": "command-post",
        "altitude_m": 0,
        "speed_kts": 0,
        "heading_deg": 0,
    },
    {
        "id": "sam_site",
        "label": "防空阵地",
        "type_name": "WEB_SAM_SITE",
        "side": "blue",
        "category": "sam",
        "icon": "sam",
        "altitude_m": 0,
        "speed_kts": 0,
        "heading_deg": 0,
    },
    {
        "id": "red_sam_site",
        "label": "红方防空阵地",
        "type_name": "WEB_SAM_SITE",
        "side": "red",
        "category": "sam",
        "icon": "sam",
        "altitude_m": 0,
        "speed_kts": 0,
        "heading_deg": 0,
    },
    {
        "id": "surface_ship",
        "label": "水面舰艇",
        "type_name": "WEB_SURFACE_SHIP",
        "side": "blue",
        "category": "ship",
        "icon": "ship",
        "altitude_m": 0,
        "speed_kts": 0,
        "heading_deg": 0,
    },
    {
        "id": "red_surface_ship",
        "label": "红方水面舰艇",
        "type_name": "WEB_SURFACE_SHIP",
        "side": "red",
        "category": "ship",
        "icon": "ship",
        "altitude_m": 0,
        "speed_kts": 0,
        "heading_deg": 0,
    },
    {
        "id": "logistics_site",
        "label": "后勤保障点",
        "type_name": "WEB_GROUND_SITE",
        "side": "blue",
        "category": "logistics",
        "icon": "base",
        "altitude_m": 0,
        "speed_kts": 0,
        "heading_deg": 0,
    },
]


def _safe_name(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_\-]+", "_", name.strip())
    return cleaned[:80] or "web_scenario"


def _format_coord(value: float, positive: str, negative: str) -> str:
    hemi = positive if value >= 0 else negative
    return f"{abs(value):.6f}{hemi}"


def _platform_type_block() -> list[str]:
    return [
        "platform_type WEB_AIRCRAFT WSF_PLATFORM",
        "   icon F-22",
        "   category aircraft",
        "   mover WSF_AIR_MOVER",
        "      roll_rate_limit 1 rad/sec",
        "      default_linear_acceleration 1.0 g",
        "      default_radial_acceleration 4.0 g",
        "      default_climb_rate 300 fps",
        "      maximum_speed 900 knots",
        "      maximum_altitude 60000 ft",
        "      minimum_altitude 0 ft",
        "      at_end_of_path extrapolate",
        "   end_mover",
        "end_platform_type",
        "",
        "platform_type WEB_BOMBER WEB_AIRCRAFT",
        "   icon B-52",
        "   category bomber",
        "end_platform_type",
        "",
        "platform_type WEB_TANKER WEB_AIRCRAFT",
        "   icon KC-135",
        "   category tanker",
        "end_platform_type",
        "",
        "platform_type WEB_AWACS WEB_AIRCRAFT",
        "   icon E-3",
        "   category awacs",
        "   sensor geo_sensor WSF_GEOMETRIC_SENSOR",
        "      azimuth_field_of_view -180.0 degrees 180.0 degrees",
        "      elevation_field_of_view -90.0 degrees 90.0 degrees",
        "      minimum_range 0 m",
        "      maximum_range 550000 m",
        "      on",
        "      frame_time 1 sec",
        "      reports_location",
        "      reports_velocity",
        "      reports_iff",
        "   end_sensor",
        "end_platform_type",
        "",
        "platform_type WEB_JAMMER WEB_AIRCRAFT",
        "   icon EA-18G",
        "   category jammer",
        "end_platform_type",
        "",
        "platform_type WEB_UAV WEB_AIRCRAFT",
        "   icon MQ-9",
        "   category uav",
        "end_platform_type",
        "",
        "platform_type WEB_RECON_UAV WEB_UAV",
        "   icon MQ-9",
        "   category recon",
        "   sensor geo_sensor WSF_GEOMETRIC_SENSOR",
        "      azimuth_field_of_view -180.0 degrees 180.0 degrees",
        "      elevation_field_of_view -90.0 degrees 90.0 degrees",
        "      minimum_range 0 m",
        "      maximum_range 220000 m",
        "      on",
        "      frame_time 2 sec",
        "      reports_location",
        "      reports_velocity",
        "      reports_iff",
        "   end_sensor",
        "end_platform_type",
        "",
        "platform_type WEB_GROUND_RADAR WSF_PLATFORM",
        "   icon radar",
        "   category radar",
        "   sensor geo_sensor WSF_GEOMETRIC_SENSOR",
        "      azimuth_field_of_view -180.0 degrees 180.0 degrees",
        "      elevation_field_of_view -10.0 degrees 90.0 degrees",
        "      minimum_range 0 m",
        "      maximum_range 300000 m",
        "      on",
        "      frame_time 1 sec",
        "      reports_location",
        "      reports_velocity",
        "      reports_iff",
        "   end_sensor",
        "end_platform_type",
        "",
        "platform_type WEB_C2_NODE WSF_PLATFORM",
        "   icon command-post",
        "   category c2",
        "   processor track_manager WSF_TRACK_PROCESSOR",
        "      purge_interval 60 sec",
        "      update_interval 5 sec",
        "   end_processor",
        "end_platform_type",
        "",
        "platform_type WEB_SAM_SITE WSF_PLATFORM",
        "   icon sam",
        "   category sam",
        "end_platform_type",
        "",
        "platform_type WEB_GROUND_SITE WSF_PLATFORM",
        "   icon base",
        "   category ground",
        "end_platform_type",
        "",
        "platform_type WEB_SURFACE_SHIP WSF_PLATFORM",
        "   icon ship",
        "   category ship",
        "end_platform_type",
        "",
    ]


def _auto_end_point(platform: Any, end_time_seconds: int) -> AFSimRoutePoint:
    seconds = min(max(end_time_seconds, 60), 900)
    distance_nm = platform.speed_kts * seconds / 3600.0
    heading_rad = math.radians(platform.heading_deg)
    delta_lat = math.cos(heading_rad) * distance_nm / 60.0
    cos_lat = max(math.cos(math.radians(platform.lat)), 0.05)
    delta_lon = math.sin(heading_rad) * distance_nm / (60.0 * cos_lat)
    return AFSimRoutePoint(
        lat=max(min(platform.lat + delta_lat, 89.9), -89.9),
        lon=max(min(platform.lon + delta_lon, 179.9), -179.9),
        altitude_m=platform.altitude_m,
        speed_kts=platform.speed_kts,
    )


def _route_points(platform: Any, end_time_seconds: int) -> list[AFSimRoutePoint]:
    start = AFSimRoutePoint(
        lat=platform.lat,
        lon=platform.lon,
        altitude_m=platform.altitude_m,
        speed_kts=platform.speed_kts,
    )
    points = [start]
    for point in platform.route:
        if abs(point.lat - start.lat) < 0.000001 and abs(point.lon - start.lon) < 0.000001:
            continue
        points.append(point)
    if len(points) < 2:
        points.append(_auto_end_point(platform, end_time_seconds))
    return points


def platform_templates() -> list[dict[str, Any]]:
    return PLATFORM_TEMPLATES


def is_mobile_type(type_name: str) -> bool:
    return _safe_name(type_name).upper() in MOBILE_TYPES


def _safe_generated_dir(scenario_id: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9_\-]+", scenario_id):
        raise ValueError(f"invalid scenario_id: {scenario_id}")
    scenario_dir = (GENERATED_ROOT / scenario_id).resolve()
    root = GENERATED_ROOT.resolve()
    if root not in scenario_dir.parents and scenario_dir != root:
        raise ValueError("scenario path escaped generated root")
    return scenario_dir


def _new_generated_dir(name: str) -> tuple[str, Path]:
    GENERATED_ROOT.mkdir(parents=True, exist_ok=True)
    safe_name = _safe_name(name)
    for _ in range(20):
        scenario_id = f"{int(time.time())}_{safe_name}_{uuid.uuid4().hex[:8]}"
        scenario_dir = _safe_generated_dir(scenario_id)
        try:
            scenario_dir.mkdir(parents=True, exist_ok=False)
            return scenario_id, scenario_dir
        except FileExistsError:
            continue
    raise FileExistsError("failed to allocate unique generated scenario directory")


def generate_scenario(design: AFSimScenarioDesign) -> dict[str, Any]:
    if not design.platforms:
        raise ValueError("scenario design must contain at least one platform")
    scenario_id, scenario_dir = _new_generated_dir(design.name)
    output_dir = scenario_dir / "output"
    output_dir.mkdir(exist_ok=True)

    lines = [
        "# Generated by AFSIM_LLM Web Scenario Designer",
        f"# Scenario: {design.name}",
        f"# Description: {design.description}",
        "file_path .",
        "",
        f"log_file output/{scenario_id}.log",
        f"event_output file output/{scenario_id}.evt end_event_output",
        f"event_pipe file output/{scenario_id}.aer end_event_pipe",
        "",
    ]
    lines.extend(_platform_type_block())

    used_names: set[str] = set()
    for platform in design.platforms:
        platform_name = _safe_name(platform.name)
        while platform_name in used_names:
            platform_name = f"{platform_name}_1"
        used_names.add(platform_name)
        type_name = _safe_name(platform.type_name).upper()
        if type_name in {"WSF_PLATFORM", "WEB", "WEB_PLATFORM", "WEB_AIRCRAFT", "AIRCRAFT"}:
            type_name = "WEB_AIRCRAFT"
        first_point = AFSimRoutePoint(
            lat=platform.lat,
            lon=platform.lon,
            altitude_m=platform.altitude_m,
            speed_kts=platform.speed_kts,
        )
        first_lat = _format_coord(first_point.lat, "n", "s")
        first_lon = _format_coord(first_point.lon, "e", "w")
        lines.extend(
            [
                f"platform {platform_name} {type_name}",
                f"   side {platform.side}",
                f"   category {platform.category}",
                f"   icon {platform.icon}",
                f"   position {first_lat} {first_lon} altitude {first_point.altitude_m:.1f} m msl",
            ]
        )
        if is_mobile_type(type_name):
            lines.append("   route")
            for point in _route_points(platform, design.end_time_seconds):
                lat = _format_coord(point.lat, "n", "s")
                lon = _format_coord(point.lon, "e", "w")
                lines.extend(
                    [
                        f"      position {lat} {lon} altitude {point.altitude_m:.1f} m msl",
                        f"         speed {point.speed_kts:.1f} kts",
                    ]
                )
            lines.append("   end_route")
        lines.extend([f"   heading {platform.heading_deg:.1f} deg", "end_platform", ""])
    lines.append(f"end_time {design.end_time_seconds} sec")
    scenario_path = scenario_dir / DEFAULT_INPUT_FILE
    scenario_path.write_text("\n".join(lines), encoding="utf-8")
    (scenario_dir / "design.json").write_text(design.model_dump_json(indent=2), encoding="utf-8")
    return {
        "scenario_id": scenario_id,
        "scenario_dir": str(scenario_dir),
        "input_file": DEFAULT_INPUT_FILE,
        "scenario_path": str(scenario_path),
        "design": json.loads(design.model_dump_json()),
    }


def list_generated_scenarios(limit: int = 100) -> list[dict[str, Any]]:
    if not GENERATED_ROOT.exists():
        return []
    rows: list[dict[str, Any]] = []
    for design_path in sorted(GENERATED_ROOT.glob("*/design.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:limit]:
        scenario_dir = design_path.parent
        scenario_path = scenario_dir / DEFAULT_INPUT_FILE
        try:
            design = json.loads(design_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        rows.append(
            {
                "scenario_id": scenario_dir.name,
                "name": design.get("name", scenario_dir.name),
                "description": design.get("description", ""),
                "platform_count": len(design.get("platforms", [])),
                "end_time_seconds": design.get("end_time_seconds"),
                "scenario_dir": str(scenario_dir),
                "scenario_path": str(scenario_path),
                "input_file": DEFAULT_INPUT_FILE,
                "modified": design_path.stat().st_mtime,
                "exists": scenario_path.exists(),
            }
        )
    return rows


def read_generated_scenario(scenario_id: str) -> dict[str, Any]:
    scenario_dir = _safe_generated_dir(scenario_id)
    design_path = scenario_dir / "design.json"
    scenario_path = scenario_dir / DEFAULT_INPUT_FILE
    if not design_path.exists() or not scenario_path.exists():
        raise FileNotFoundError(f"generated scenario not found: {scenario_id}")
    return {
        "scenario_id": scenario_id,
        "scenario_dir": str(scenario_dir),
        "scenario_path": str(scenario_path),
        "input_file": DEFAULT_INPUT_FILE,
        "design": json.loads(design_path.read_text(encoding="utf-8")),
        "scenario_text": scenario_path.read_text(encoding="utf-8", errors="ignore"),
    }


def delete_generated_scenario(scenario_id: str) -> dict[str, Any]:
    scenario_dir = _safe_generated_dir(scenario_id)
    if not scenario_dir.exists():
        raise FileNotFoundError(f"generated scenario not found: {scenario_id}")
    shutil.rmtree(scenario_dir)
    return {"scenario_id": scenario_id, "deleted": True, "scenario_dir": str(scenario_dir)}


def generated_input_path(scenario_id: str) -> Path:
    scenario_dir = _safe_generated_dir(scenario_id)
    scenario_path = scenario_dir / DEFAULT_INPUT_FILE
    if not scenario_path.exists():
        raise FileNotFoundError(f"generated scenario input not found: {scenario_id}")
    return scenario_path


def scene_overview(parsed: dict[str, Any]) -> dict[str, Any]:
    platforms = []
    for item in parsed.get("platforms", []):
        position = item.get("position") or (item.get("positions") or [{}])[0]
        if not position or position.get("lat") is None or position.get("lon") is None:
            continue
        platforms.append(
            {
                "id": item.get("id", ""),
                "side": item.get("side", "neutral"),
                "category": item.get("category") or item.get("icon") or item.get("type", ""),
                "type": item.get("type", ""),
                "lat": float(position.get("lat", 0.0)),
                "lon": float(position.get("lon", 0.0)),
                "alt_m": float(position.get("alt_m", 0.0)),
                "route": [
                    {"lat": float(point["lat"]), "lon": float(point["lon"]), "alt_m": float(point.get("alt_m", 0.0))}
                    for point in item.get("positions", [])
                    if point.get("lat") is not None and point.get("lon") is not None
                ],
            }
        )
    if not platforms:
        return {"platforms": [], "bounds": None}
    lats = [item["lat"] for item in platforms]
    lons = [item["lon"] for item in platforms]
    return {
        "platforms": platforms,
        "bounds": {
            "min_lat": min(lats),
            "max_lat": max(lats),
            "min_lon": min(lons),
            "max_lon": max(lons),
        },
    }
