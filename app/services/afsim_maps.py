from __future__ import annotations

import json
import math
import sqlite3
import struct
import time
from dataclasses import dataclass
from functools import lru_cache
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image

from app.core.config import PROJECT_ROOT
from app.services.afsim_runner import afsim_paths


MAP_CACHE_ROOT = PROJECT_ROOT / "runtime" / "map_cache"
TILE_SIZE = 256


@dataclass(frozen=True)
class RasterMapDef:
    id: str
    name: str
    mbtiles_path: tuple[str, ...]
    earth_path: tuple[str, ...]
    default_format: str


@dataclass(frozen=True)
class VectorLayerDef:
    id: str
    name: str
    shp_path: tuple[str, ...]
    aliases: tuple[str, ...] = ()


RASTER_MAPS: dict[str, RasterMapDef] = {
    "bluemarble": RasterMapDef(
        id="bluemarble",
        name="Blue Marble",
        mbtiles_path=("bluemarble_db", "bmng.mbtiles"),
        earth_path=("bluemarble_db", "bmng.earth"),
        default_format="jpg",
    ),
    "naturalearth": RasterMapDef(
        id="naturalearth",
        name="Natural Earth",
        mbtiles_path=("naturalearth_db", "natural.mbtiles"),
        earth_path=("naturalearth_db", "natural.earth"),
        default_format="png",
    ),
    "political": RasterMapDef(
        id="political",
        name="Political Borders",
        mbtiles_path=("political_db", "border.mbtiles"),
        earth_path=("political_db", "border.earth"),
        default_format="png",
    ),
    "bald": RasterMapDef(
        id="bald",
        name="Bald Earth",
        mbtiles_path=("bald_earth_db", "bald.mbtiles"),
        earth_path=("bald_earth_db", "bald.earth"),
        default_format="png",
    ),
}

VECTOR_LAYERS: dict[str, VectorLayerDef] = {
    "coastline": VectorLayerDef(
        id="coastline",
        name="NE 50m Coastline",
        shp_path=("layers", "ne_50m_coastline.shp"),
        aliases=("ne_50m_coastline",),
    ),
    "pol": VectorLayerDef(
        id="pol",
        name="AFSIM Political Boundaries",
        shp_path=("layers", "pol.shp"),
        aliases=("political_ecef",),
    ),
    "us": VectorLayerDef(
        id="us",
        name="AFSIM US Boundaries",
        shp_path=("layers", "us.shp"),
        aliases=("us_boundaries",),
    ),
}


def _maps_root() -> Path:
    return afsim_paths().root / "resources" / "maps"


def _models_root() -> Path:
    return afsim_paths().root / "resources" / "models"


def _raster_def(map_id: str) -> RasterMapDef:
    key = map_id.lower()
    if key not in RASTER_MAPS:
        raise KeyError(f"unknown AFSIM raster map: {map_id}")
    return RASTER_MAPS[key]


def _vector_def(layer_id: str) -> VectorLayerDef:
    key = layer_id.lower()
    if key in VECTOR_LAYERS:
        return VECTOR_LAYERS[key]
    for item in VECTOR_LAYERS.values():
        if key in item.aliases:
            return item
    raise KeyError(f"unknown AFSIM vector layer: {layer_id}")


def _raster_path(map_id: str) -> Path:
    definition = _raster_def(map_id)
    return _maps_root().joinpath(*definition.mbtiles_path)


def _earth_path(map_id: str) -> Path:
    definition = _raster_def(map_id)
    return _maps_root().joinpath(*definition.earth_path)


def _vector_path(layer_id: str) -> Path:
    definition = _vector_def(layer_id)
    return _maps_root().joinpath(*definition.shp_path)


def _mime_for_format(value: str) -> str:
    fmt = value.lower().lstrip(".")
    if fmt in {"jpg", "jpeg"}:
        return "image/jpeg"
    if fmt == "png":
        return "image/png"
    if fmt == "webp":
        return "image/webp"
    return "application/octet-stream"


def _extension_for_format(value: str) -> str:
    fmt = value.lower().lstrip(".")
    return "jpg" if fmt == "jpeg" else fmt


def _json_or_none(value: str | None) -> Any:
    if not value:
        return None
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return None


def _read_metadata(conn: sqlite3.Connection) -> dict[str, str]:
    try:
        rows = conn.execute("select name, value from metadata").fetchall()
    except sqlite3.Error:
        return {}
    return {str(name): str(value) for name, value in rows}


@lru_cache(maxsize=16)
def raster_metadata(map_id: str) -> dict[str, Any]:
    definition = _raster_def(map_id)
    path = _raster_path(definition.id)
    earth = _earth_path(definition.id)
    result: dict[str, Any] = {
        "id": definition.id,
        "name": definition.name,
        "kind": "mbtiles",
        "path": str(path),
        "earth": str(earth),
        "exists": path.exists(),
        "earth_exists": earth.exists(),
        "profile": "afsim_plate_carree",
        "url_y_origin": "north",
        "mbtiles_tile_row_origin": "south",
        "tile_size": TILE_SIZE,
        "format": definition.default_format,
        "mime_type": _mime_for_format(definition.default_format),
        "min_zoom": 0,
        "max_zoom": 0,
        "bounds": [-180.0, -90.0, 180.0, 90.0],
        "num_tiles_wide_at_lod_0": 2,
        "num_tiles_high_at_lod_0": 1,
        "tile_url_template": f"/api/afsim/maps/{definition.id}/{{z}}/{{x}}/{{y}}.png",
    }
    if not path.exists():
        return result

    with sqlite3.connect(path) as conn:
        metadata = _read_metadata(conn)
        fmt = metadata.get("format") or definition.default_format
        profile = _json_or_none(metadata.get("profile"))
        row = conn.execute("select min(zoom_level), max(zoom_level), count(*) from tiles").fetchone()
    if row:
        result["min_zoom"] = int(row[0] or 0)
        result["max_zoom"] = int(row[1] or 0)
        result["tile_count"] = int(row[2] or 0)
    result["format"] = _extension_for_format(fmt)
    result["mime_type"] = _mime_for_format(fmt)
    result["metadata"] = metadata
    if isinstance(profile, dict):
        result["osgearth_profile"] = profile.get("profile", profile)
    return result


def _tile_matrix_size(z: int) -> tuple[int, int]:
    if z < 0:
        raise ValueError("zoom must be non-negative")
    return 2 ** (z + 1), 2**z


def read_raster_tile(map_id: str, z: int, x: int, y: int) -> tuple[bytes, str, dict[str, Any]]:
    meta = raster_metadata(map_id)
    if not meta.get("exists"):
        raise FileNotFoundError(str(meta["path"]))
    min_zoom = int(meta.get("min_zoom", 0))
    max_zoom = int(meta.get("max_zoom", 0))
    if z < min_zoom or z > max_zoom:
        raise KeyError(f"zoom {z} outside {min_zoom}..{max_zoom}")

    cols, rows = _tile_matrix_size(z)
    if x < 0 or x >= cols or y < 0 or y >= rows:
        raise KeyError(f"tile outside AFSIM plate-carree matrix: z={z} x={x} y={y}")

    tile_row = rows - 1 - y
    with sqlite3.connect(_raster_path(map_id)) as conn:
        row = conn.execute(
            "select tile_data from tiles where zoom_level = ? and tile_column = ? and tile_row = ?",
            (z, x, tile_row),
        ).fetchone()
    if not row:
        raise KeyError(f"tile not found: {map_id}/{z}/{x}/{y}")
    return bytes(row[0]), str(meta["mime_type"]), meta


def compose_raster_texture(map_id: str = "bluemarble", z: int = 3) -> tuple[Path, str, dict[str, Any]]:
    meta = raster_metadata(map_id)
    if not meta.get("exists"):
        raise FileNotFoundError(str(meta["path"]))
    z = max(int(meta.get("min_zoom", 0)), min(int(z), min(int(meta.get("max_zoom", 0)), 4)))
    fmt = _extension_for_format(str(meta.get("format") or "jpg"))
    save_fmt = "JPEG" if fmt in {"jpg", "jpeg"} else fmt.upper()
    out = MAP_CACHE_ROOT / f"{map_id}_platecarree_z{z}.{fmt}"
    source = Path(str(meta["path"]))
    if out.exists() and out.stat().st_mtime >= source.stat().st_mtime:
        return out, str(meta["mime_type"]), meta

    MAP_CACHE_ROOT.mkdir(parents=True, exist_ok=True)
    cols, rows = _tile_matrix_size(z)
    mode = "RGB" if save_fmt == "JPEG" else "RGBA"
    canvas = Image.new(mode, (cols * TILE_SIZE, rows * TILE_SIZE), (0, 0, 0, 0) if mode == "RGBA" else (0, 0, 0))
    with sqlite3.connect(source) as conn:
        for y in range(rows):
            tile_row = rows - 1 - y
            for x in range(cols):
                row = conn.execute(
                    "select tile_data from tiles where zoom_level = ? and tile_column = ? and tile_row = ?",
                    (z, x, tile_row),
                ).fetchone()
                if not row:
                    continue
                tile = Image.open(BytesIO(row[0]))
                if tile.mode != mode:
                    tile = tile.convert(mode)
                canvas.paste(tile, (x * TILE_SIZE, y * TILE_SIZE))
    if save_fmt == "JPEG":
        canvas.save(out, save_fmt, quality=92, optimize=True)
    else:
        canvas.save(out, save_fmt)
    return out, str(meta["mime_type"]), meta


def _read_prj(path: Path) -> str:
    prj = path.with_suffix(".prj")
    if not prj.exists():
        return ""
    return prj.read_text(encoding="utf-8", errors="ignore")


def _ecef_to_lon_lat(x: float, y: float, z: float) -> tuple[float, float] | None:
    if not all(math.isfinite(value) for value in (x, y, z)):
        return None
    if abs(x) < 1e-9 and abs(y) < 1e-9 and abs(z) < 1e-9:
        return None
    a = 6378137.0
    e2 = 6.6943799901413165e-3
    lon = math.atan2(y, x)
    p = math.hypot(x, y)
    lat = math.atan2(z, p * (1 - e2))
    for _ in range(6):
        sin_lat = math.sin(lat)
        n = a / math.sqrt(1 - e2 * sin_lat * sin_lat)
        alt = p / max(math.cos(lat), 1e-12) - n
        lat = math.atan2(z, p * (1 - e2 * n / (n + alt)))
    return math.degrees(lon), math.degrees(lat)


def _part_ranges(parts: tuple[int, ...], n_points: int) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    for index, start in enumerate(parts):
        end = parts[index + 1] if index + 1 < len(parts) else n_points
        if start < end:
            ranges.append((start, end))
    return ranges


def _rdp(points: list[list[float]], tolerance: float) -> list[list[float]]:
    if tolerance <= 0 or len(points) <= 2:
        return points

    def point_distance(point: list[float], start: list[float], end: list[float]) -> float:
        dx = end[0] - start[0]
        dy = end[1] - start[1]
        if dx == 0 and dy == 0:
            return math.hypot(point[0] - start[0], point[1] - start[1])
        ratio = ((point[0] - start[0]) * dx + (point[1] - start[1]) * dy) / (dx * dx + dy * dy)
        projected = [start[0] + ratio * dx, start[1] + ratio * dy]
        return math.hypot(point[0] - projected[0], point[1] - projected[1])

    keep = {0, len(points) - 1}
    stack = [(0, len(points) - 1)]
    while stack:
        start, end = stack.pop()
        if end <= start + 1:
            continue
        max_distance = -1.0
        max_index = start
        for index in range(start + 1, end):
            distance = point_distance(points[index], points[start], points[end])
            if distance > max_distance:
                max_distance = distance
                max_index = index
        if max_distance > tolerance:
            keep.add(max_index)
            stack.append((start, max_index))
            stack.append((max_index, end))
    return [point for index, point in enumerate(points) if index in keep]


def _line_bbox(line: list[list[float]]) -> tuple[float, float, float, float]:
    lons = [point[0] for point in line]
    lats = [point[1] for point in line]
    return min(lons), min(lats), max(lons), max(lats)


def _bbox_intersects(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> bool:
    return not (a[2] < b[0] or a[0] > b[2] or a[3] < b[1] or a[1] > b[3])


def _parse_shapefile(path: Path) -> list[dict[str, Any]]:
    data = path.read_bytes()
    if len(data) < 100:
        return []
    prj = _read_prj(path)
    geocentric = "GEOCCS" in prj.upper() or "GEOCENTRIC" in prj.upper()
    features: list[dict[str, Any]] = []
    offset = 100
    while offset + 8 <= len(data):
        record_number, content_words = struct.unpack(">2i", data[offset : offset + 8])
        offset += 8
        record_end = offset + content_words * 2
        if record_end > len(data) or offset + 4 > record_end:
            break
        shape_type = struct.unpack("<i", data[offset : offset + 4])[0]
        if shape_type == 0:
            offset = record_end
            continue
        if shape_type not in {3, 5, 13, 15} or offset + 44 > record_end:
            offset = record_end
            continue

        n_parts, n_points = struct.unpack("<2i", data[offset + 36 : offset + 44])
        parts_offset = offset + 44
        points_offset = parts_offset + 4 * n_parts
        points_end = points_offset + 16 * n_points
        if n_parts <= 0 or n_points <= 0 or points_end > record_end:
            offset = record_end
            continue
        parts = struct.unpack("<" + "i" * n_parts, data[parts_offset:points_offset])
        xy = [
            struct.unpack("<2d", data[points_offset + index * 16 : points_offset + index * 16 + 16])
            for index in range(n_points)
        ]
        z_values: list[float] = []
        if shape_type in {13, 15}:
            z_offset = points_end + 16
            z_end = z_offset + 8 * n_points
            if z_end <= record_end:
                z_values = [
                    struct.unpack("<d", data[z_offset + index * 8 : z_offset + index * 8 + 8])[0]
                    for index in range(n_points)
                ]

        lines: list[list[list[float]]] = []
        for start, end in _part_ranges(parts, n_points):
            line: list[list[float]] = []
            for point_index in range(start, end):
                x, y = xy[point_index]
                if geocentric or abs(x) > 180 or abs(y) > 90:
                    if point_index >= len(z_values):
                        continue
                    converted = _ecef_to_lon_lat(x, y, z_values[point_index])
                    if not converted:
                        continue
                    lon, lat = converted
                else:
                    lon, lat = x, y
                if not (-180.0001 <= lon <= 180.0001 and -90.0001 <= lat <= 90.0001):
                    continue
                line.append([round(lon, 7), round(lat, 7)])
            if len(line) >= 2:
                lines.append(line)

        if lines:
            geometry_type = "MultiPolygon" if shape_type in {5, 15} and len(lines) > 1 else "MultiLineString"
            coordinates: Any = lines
            if shape_type in {5, 15}:
                coordinates = [[line] for line in lines]
            features.append(
                {
                    "type": "Feature",
                    "properties": {
                        "record": record_number,
                        "shape_type": shape_type,
                        "source": path.name,
                        "crs": "WGS84 lon/lat" if not geocentric else "converted from WGS84 ECEF",
                    },
                    "geometry": {
                        "type": geometry_type,
                        "coordinates": coordinates,
                    },
                }
            )
        offset = record_end
    return features


def _simplify_feature(feature: dict[str, Any], tolerance: float) -> dict[str, Any] | None:
    geometry = feature.get("geometry") or {}
    geometry_type = geometry.get("type")
    coords = geometry.get("coordinates")
    if tolerance <= 0 or not isinstance(coords, list):
        return feature
    clone = dict(feature)
    clone["properties"] = dict(feature.get("properties") or {})
    if geometry_type == "MultiLineString":
        lines = [_rdp(line, tolerance) for line in coords if isinstance(line, list)]
        lines = [line for line in lines if len(line) >= 2]
        if not lines:
            return None
        clone["geometry"] = {"type": "MultiLineString", "coordinates": lines}
        return clone
    if geometry_type == "LineString":
        line = _rdp(coords, tolerance)
        if len(line) < 2:
            return None
        clone["geometry"] = {"type": "LineString", "coordinates": line}
        return clone
    return feature


def _feature_bbox(feature: dict[str, Any]) -> tuple[float, float, float, float] | None:
    geometry = feature.get("geometry") or {}
    coords = geometry.get("coordinates")
    geometry_type = geometry.get("type")
    lines: list[list[list[float]]] = []
    if geometry_type == "LineString" and isinstance(coords, list):
        lines = [coords]
    elif geometry_type == "MultiLineString" and isinstance(coords, list):
        lines = coords
    elif geometry_type == "MultiPolygon" and isinstance(coords, list):
        lines = [ring for polygon in coords for ring in polygon]
    if not lines:
        return None
    bboxes = [_line_bbox(line) for line in lines if len(line) >= 2]
    if not bboxes:
        return None
    return (
        min(item[0] for item in bboxes),
        min(item[1] for item in bboxes),
        max(item[2] for item in bboxes),
        max(item[3] for item in bboxes),
    )


@lru_cache(maxsize=8)
def _raw_vector_features(layer_id: str, source_mtime: float) -> tuple[dict[str, Any], ...]:
    del source_mtime
    return tuple(_parse_shapefile(_vector_path(layer_id)))


def vector_geojson(
    layer_id: str,
    bbox: tuple[float, float, float, float] | None = None,
    simplify: float = 0.0,
    max_features: int = 20000,
) -> dict[str, Any]:
    definition = _vector_def(layer_id)
    path = _vector_path(definition.id)
    if not path.exists():
        raise FileNotFoundError(str(path))
    source_mtime = path.stat().st_mtime
    features: list[dict[str, Any]] = []
    for feature in _raw_vector_features(definition.id, source_mtime):
        feature_bbox = _feature_bbox(feature)
        if bbox and feature_bbox and not _bbox_intersects(feature_bbox, bbox):
            continue
        simplified = _simplify_feature(feature, simplify)
        if not simplified:
            continue
        features.append(simplified)
        if len(features) >= max_features:
            break
    return {
        "type": "FeatureCollection",
        "name": definition.id,
        "features": features,
        "metadata": {
            "source": str(path),
            "generated_at": time.time(),
            "bbox_filter": bbox,
            "simplify_degrees": simplify,
            "feature_count": len(features),
            "aliases": list(definition.aliases),
        },
    }


def parse_bbox(value: str | None) -> tuple[float, float, float, float] | None:
    if not value:
        return None
    parts = [float(item.strip()) for item in value.split(",")]
    if len(parts) != 4:
        raise ValueError("bbox must be minLon,minLat,maxLon,maxLat")
    min_lon, min_lat, max_lon, max_lat = parts
    if min_lon > max_lon or min_lat > max_lat:
        raise ValueError("bbox min values must be <= max values")
    return min_lon, min_lat, max_lon, max_lat


def map_resource_manifest() -> dict[str, Any]:
    maps_root = _maps_root()
    models_root = _models_root()
    raster_layers = [raster_metadata(map_id) for map_id in RASTER_MAPS]
    vector_layers = []
    for layer in VECTOR_LAYERS.values():
        path = _vector_path(layer.id)
        vector_layers.append(
            {
                "id": layer.id,
                "name": layer.name,
                "kind": "shapefile",
                "path": str(path),
                "exists": path.exists(),
                "aliases": list(layer.aliases),
                "geojson_url": f"/api/afsim/maps/vectors/{layer.id}.geojson",
            }
        )
    return {
        "source": "local_afsim_resources",
        "readonly": True,
        "maps_root": str(maps_root),
        "models_root": str(models_root),
        "tile_scheme": "afsim_plate_carree",
        "tile_matrix": {
            "tile_size": TILE_SIZE,
            "lod0_width": 2,
            "lod0_height": 1,
            "url_y_origin": "north",
            "mbtiles_tile_row_origin": "south",
            "bounds": [-180.0, -90.0, 180.0, 90.0],
        },
        "raster_layers": raster_layers,
        "vector_layers": vector_layers,
        "globe_texture": {
            "id": "bluemarble",
            "url": "/api/afsim/maps/bluemarble/texture.jpg?z=3",
            "profile": "plate-carree",
        },
        "offline_maps": [
            {
                "id": layer["id"],
                "name": layer["name"],
                "kind": layer["kind"],
                "exists": layer["exists"],
                "url": layer.get("tile_url_template"),
            }
            for layer in raster_layers
        ]
        + [
            {
                "id": layer["id"],
                "name": layer["name"],
                "kind": layer["kind"],
                "exists": layer["exists"],
                "url": layer["geojson_url"],
            }
            for layer in vector_layers
        ],
        "model_catalog": {
            "milstd_mapping": str(models_root / "milStdIconMappings.csv"),
            "simple_models": str(models_root / "simple"),
            "three_d_models": str(models_root / "3d"),
            "note": "Browser rendering now consumes AFSIM raster/vector map resources; native OSGB models remain referenced for later loading.",
        },
    }
