import * as THREE from "/static/vendor/three/three.module.js";

let globeState = {
  animationId: null,
  renderer: null,
  geometries: [],
  materials: [],
  textures: [],
};

const TILE_SIZE = 256;
const rasterTileCache = new Map();
const vectorLayerCache = new Map();

export function sideColor(side) {
  if (side === "blue") return "#5aa7ff";
  if (side === "red") return "#ff7777";
  if (side === "green") return "#65d38b";
  return "#c2c8ce";
}

function layerMap(layers = []) {
  return new Map(layers.map((layer) => [layer.id, layer]));
}

function layerVisible(layers, id) {
  const item = layers.get(id);
  return item ? item.visible !== false : true;
}

function layerOpacity(layers, id, fallback = 1) {
  const item = layers.get(id);
  if (!item) return fallback;
  const value = Number(item.opacity);
  return Number.isFinite(value) ? Math.max(0, Math.min(1, value)) : fallback;
}

function disposeGlobe() {
  if (globeState.animationId) cancelAnimationFrame(globeState.animationId);
  if (globeState.renderer) globeState.renderer.dispose();
  globeState.geometries.forEach((geometry) => geometry.dispose());
  globeState.materials.forEach((material) => material.dispose());
  globeState.textures.forEach((texture) => texture.dispose());
  globeState = { animationId: null, renderer: null, geometries: [], materials: [], textures: [] };
}

function mergeFrameEntities(scene, frame) {
  const platforms = scene.platforms || [];
  const frameEntities = frame?.entities || [];
  if (!frameEntities.length) return platforms;
  const byId = new Map(frameEntities.map((entity) => [entity.id, entity]));
  if (!platforms.length) {
    return frameEntities.map((entity) => ({
      ...entity,
      name: entity.id,
      kind: entity.kind || entity.category || "aircraft",
      position: { lat: Number(entity.lat), lon: Number(entity.lon), alt_m: Number(entity.alt_m || 0) },
    }));
  }
  const platformIds = new Set(platforms.map((platform) => platform.id));
  const merged = platforms.map((platform) => {
    const frameEntity = byId.get(platform.id);
    if (!frameEntity) return platform;
    const position = {
      lat: Number(frameEntity.lat),
      lon: Number(frameEntity.lon),
      alt_m: Number(frameEntity.alt_m || 0),
    };
    return {
      ...platform,
      lat: position.lat,
      lon: position.lon,
      alt_m: position.alt_m,
      position,
      heading_deg: Number(frameEntity.heading_deg ?? platform.heading_deg ?? 0),
      route: frameEntity.route || platform.route || [],
    };
  });
  for (const entity of frameEntities) {
    if (platformIds.has(entity.id)) continue;
    merged.push({
      ...entity,
      name: entity.name || entity.id,
      side: entity.side || "neutral",
      kind: entity.kind || entity.category || "aircraft",
      position: { lat: Number(entity.lat), lon: Number(entity.lon), alt_m: Number(entity.alt_m || 0) },
      route: entity.route || [],
    });
  }
  return merged;
}

function filteredEntities(scene, frame, filters = {}) {
  const entities = mergeFrameEntities(scene, frame);
  const sides = filters.sides || new Set(["blue", "red", "green", "neutral"]);
  const kinds = filters.kinds || new Set(["air", "ground", "sea", "space", "missile", "other"]);
  return entities.filter((entity) => {
    const side = entity.side || "neutral";
    const kind = entity.kind || entity.category || "other";
    const family =
      kind === "aircraft" || kind === "jammer" ? "air" :
      kind === "ship" ? "sea" :
      kind === "satellite" ? "space" :
      kind === "missile" ? "missile" :
      ["ground", "radar", "c2"].includes(kind) ? "ground" :
      "other";
    return sides.has(side) && kinds.has(family);
  });
}

function boundsFor(scene, entities) {
  const lats = entities.map((item) => Number(item.lat ?? item.position?.lat)).filter(Number.isFinite);
  const lons = entities.map((item) => Number(item.lon ?? item.position?.lon)).filter(Number.isFinite);
  if (scene.bounds) {
    lats.push(Number(scene.bounds.min_lat), Number(scene.bounds.max_lat));
    lons.push(Number(scene.bounds.min_lon), Number(scene.bounds.max_lon));
  }
  if (!lats.length || !lons.length) return null;
  return {
    min_lat: Math.min(...lats),
    max_lat: Math.max(...lats),
    min_lon: Math.min(...lons),
    max_lon: Math.max(...lons),
  };
}

function paddedBounds(bounds, domain) {
  const padFactor = domain === "space" ? 0.7 : domain === "near_space" ? 0.35 : 0.18;
  const latPad = Math.max(0.05, (bounds.max_lat - bounds.min_lat || 0.1) * padFactor);
  const lonPad = Math.max(0.05, (bounds.max_lon - bounds.min_lon || 0.1) * padFactor);
  return {
    min_lat: Math.max(-90, bounds.min_lat - latPad),
    max_lat: Math.min(90, bounds.max_lat + latPad),
    min_lon: Math.max(-180, bounds.min_lon - lonPad),
    max_lon: Math.min(180, bounds.max_lon + lonPad),
  };
}

function viewFromState(bounds, domain, viewState) {
  const fitted = paddedBounds(bounds, domain);
  if (!viewState) return fitted;
  const baseLatSpan = Math.max(0.01, fitted.max_lat - fitted.min_lat);
  const baseLonSpan = Math.max(0.01, fitted.max_lon - fitted.min_lon);
  const latSpan = clamp(Number(viewState.latSpan || baseLatSpan), 0.002, 180);
  const lonSpan = clamp(Number(viewState.lonSpan || baseLonSpan), 0.002, 360);
  const centerLat = clamp(Number(viewState.centerLat ?? (fitted.min_lat + fitted.max_lat) / 2), -89.999, 89.999);
  const centerLon = clamp(Number(viewState.centerLon ?? (fitted.min_lon + fitted.max_lon) / 2), -179.999, 179.999);
  return {
    min_lat: clamp(centerLat - latSpan / 2, -90, 90),
    max_lat: clamp(centerLat + latSpan / 2, -90, 90),
    min_lon: clamp(centerLon - lonSpan / 2, -180, 180),
    max_lon: clamp(centerLon + lonSpan / 2, -180, 180),
  };
}

function viewStateFromView(view) {
  return {
    centerLat: (view.min_lat + view.max_lat) / 2,
    centerLon: (view.min_lon + view.max_lon) / 2,
    latSpan: Math.max(0.002, view.max_lat - view.min_lat),
    lonSpan: Math.max(0.002, view.max_lon - view.min_lon),
  };
}

function setupCanvas(canvas) {
  const rect = canvas.getBoundingClientRect();
  const ratio = Math.min(window.devicePixelRatio || 1, 2);
  canvas.width = Math.max(320, Math.floor(rect.width * ratio));
  canvas.height = Math.max(240, Math.floor(rect.height * ratio));
  const ctx = canvas.getContext("2d");
  ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
  return { ctx, width: canvas.width / ratio, height: canvas.height / ratio };
}

function positionOf(item) {
  const position = item.position || item;
  return {
    lat: Number(position.lat ?? item.lat ?? 0),
    lon: Number(position.lon ?? item.lon ?? 0),
    alt_m: Number(position.alt_m ?? item.alt_m ?? 0),
  };
}

function clamp(value, min, max) {
  return Math.max(min, Math.min(max, value));
}

function resourceList(scene, key) {
  const value = scene?.map_resources?.[key];
  return Array.isArray(value) ? value : [];
}

function rasterResource(scene, id) {
  return resourceList(scene, "raster_layers").find((item) => item.id === id) || null;
}

function vectorResource(scene, id) {
  return resourceList(scene, "vector_layers").find((item) => item.id === id || (item.aliases || []).includes(id)) || null;
}

function replaceTileTemplate(template, z, x, y) {
  return template
    .replaceAll("{z}", String(z))
    .replaceAll("{x}", String(x))
    .replaceAll("{y}", String(y));
}

function rasterTileUrl(scene, id, z, x, y) {
  const resource = rasterResource(scene, id);
  const template = resource?.tile_url_template || `/api/afsim/maps/${id}/{z}/{x}/{y}.png`;
  return replaceTileTemplate(template, z, x, y);
}

function loadRasterTile(url, redraw) {
  let item = rasterTileCache.get(url);
  if (!item) {
    const image = new Image();
    item = { image, status: "loading", callbacks: new Set() };
    rasterTileCache.set(url, item);
    image.onload = () => {
      item.status = "ready";
      for (const callback of item.callbacks) callback();
      item.callbacks.clear();
    };
    image.onerror = () => {
      item.status = "error";
      item.callbacks.clear();
    };
    image.src = url;
  }
  if (item.status === "loading" && redraw) item.callbacks.add(redraw);
  return item;
}

function choosePlateCarreeZoom(resource, view, width, height) {
  const minZoom = Number(resource?.min_zoom ?? 0);
  const maxZoom = Number(resource?.max_zoom ?? 6);
  const lonSpan = Math.max(0.000001, view.max_lon - view.min_lon);
  const latSpan = Math.max(0.000001, view.max_lat - view.min_lat);
  const pxPerDegree = Math.max(width / lonSpan, height / latSpan);
  const ideal = Math.ceil(Math.log2(Math.max(1, (pxPerDegree * 180) / TILE_SIZE)));
  return Math.max(minZoom, Math.min(maxZoom, ideal));
}

function drawPlateCarreeRaster(ctx, width, height, view, scene, mapId, opacity, redraw) {
  const resource = rasterResource(scene, mapId);
  if (!resource?.exists) return false;
  const z = choosePlateCarreeZoom(resource, view, width, height);
  const cols = 2 ** (z + 1);
  const rows = 2 ** z;
  const tileLonSpan = 360 / cols;
  const tileLatSpan = 180 / rows;
  const lonSpan = Math.max(0.000001, view.max_lon - view.min_lon);
  const latSpan = Math.max(0.000001, view.max_lat - view.min_lat);
  const xStart = clamp(Math.floor((view.min_lon + 180) / tileLonSpan), 0, cols - 1);
  const xEnd = clamp(Math.floor((view.max_lon + 180 - 1e-9) / tileLonSpan), 0, cols - 1);
  const yStart = clamp(Math.floor((90 - view.max_lat) / tileLatSpan), 0, rows - 1);
  const yEnd = clamp(Math.floor((90 - view.min_lat - 1e-9) / tileLatSpan), 0, rows - 1);

  ctx.save();
  ctx.globalAlpha = opacity;
  ctx.imageSmoothingEnabled = true;
  ctx.imageSmoothingQuality = "high";
  for (let y = yStart; y <= yEnd; y += 1) {
    const north = 90 - y * tileLatSpan;
    const south = north - tileLatSpan;
    const dy = ((view.max_lat - north) / latSpan) * height;
    const dh = (tileLatSpan / latSpan) * height;
    for (let x = xStart; x <= xEnd; x += 1) {
      const west = -180 + x * tileLonSpan;
      const dx = ((west - view.min_lon) / lonSpan) * width;
      const dw = (tileLonSpan / lonSpan) * width;
      const item = loadRasterTile(rasterTileUrl(scene, mapId, z, x, y), redraw);
      if (item.status === "ready") {
        ctx.drawImage(item.image, dx, dy, dw + 0.75, dh + 0.75);
      }
    }
  }
  ctx.restore();
  return true;
}

function loadVectorLayer(url, redraw) {
  let item = vectorLayerCache.get(url);
  if (!item) {
    item = { status: "loading", data: null, callbacks: new Set() };
    vectorLayerCache.set(url, item);
    fetch(url)
      .then((response) => {
        if (!response.ok) throw new Error(`vector layer ${response.status}`);
        return response.json();
      })
      .then((data) => {
        item.status = "ready";
        item.data = data;
        for (const callback of item.callbacks) callback();
        item.callbacks.clear();
      })
      .catch(() => {
        item.status = "error";
        item.callbacks.clear();
      });
  }
  if (item.status === "loading" && redraw) item.callbacks.add(redraw);
  return item;
}

function vectorLayerUrl(scene, id, simplify = 0.02) {
  const resource = vectorResource(scene, id);
  if (!resource?.exists) return null;
  const base = resource.geojson_url || `/api/afsim/maps/vectors/${resource.id}.geojson`;
  const join = base.includes("?") ? "&" : "?";
  return `${base}${join}simplify=${encodeURIComponent(simplify)}`;
}

function drawGeoJsonLine(ctx, project, coordinates) {
  let started = false;
  for (const point of coordinates || []) {
    const lon = Number(point?.[0]);
    const lat = Number(point?.[1]);
    if (!Number.isFinite(lat) || !Number.isFinite(lon)) {
      started = false;
      continue;
    }
    const screen = project(lat, lon);
    if (!started) {
      ctx.moveTo(screen.x, screen.y);
      started = true;
    } else {
      ctx.lineTo(screen.x, screen.y);
    }
  }
}

function drawVectorLayer(ctx, project, scene, id, style, redraw) {
  const url = vectorLayerUrl(scene, id, style.simplify ?? 0.02);
  if (!url) return;
  const item = loadVectorLayer(url, redraw);
  if (item.status !== "ready" || !item.data?.features?.length) return;
  ctx.save();
  ctx.globalAlpha = style.opacity ?? 0.5;
  ctx.strokeStyle = style.color || "#9fb6b2";
  ctx.lineWidth = style.width ?? 1;
  if (style.dash) ctx.setLineDash(style.dash);
  ctx.beginPath();
  for (const feature of item.data.features) {
    const geometry = feature.geometry || {};
    const coordinates = geometry.coordinates || [];
    if (geometry.type === "LineString") {
      drawGeoJsonLine(ctx, project, coordinates);
    } else if (geometry.type === "MultiLineString") {
      for (const line of coordinates) drawGeoJsonLine(ctx, project, line);
    } else if (geometry.type === "Polygon") {
      for (const ring of coordinates) drawGeoJsonLine(ctx, project, ring);
    } else if (geometry.type === "MultiPolygon") {
      for (const polygon of coordinates) {
        for (const ring of polygon) drawGeoJsonLine(ctx, project, ring);
      }
    }
  }
  ctx.stroke();
  ctx.restore();
}

function noise(lat, lon, seed = 0) {
  const value = Math.sin(lat * 12.9898 + lon * 78.233 + seed * 37.719) * 43758.5453;
  return value - Math.floor(value);
}

function haversineKm(a, b) {
  const lat1 = Number(a.lat) * Math.PI / 180;
  const lat2 = Number(b.lat) * Math.PI / 180;
  const dLat = lat2 - lat1;
  const dLon = (Number(b.lon) - Number(a.lon)) * Math.PI / 180;
  const value = Math.sin(dLat / 2) ** 2 + Math.cos(lat1) * Math.cos(lat2) * Math.sin(dLon / 2) ** 2;
  return 6371 * 2 * Math.atan2(Math.sqrt(value), Math.sqrt(Math.max(0, 1 - value)));
}

function bearingDeg(a, b) {
  const lat1 = Number(a.lat) * Math.PI / 180;
  const lat2 = Number(b.lat) * Math.PI / 180;
  const dLon = (Number(b.lon) - Number(a.lon)) * Math.PI / 180;
  const y = Math.sin(dLon) * Math.cos(lat2);
  const x = Math.cos(lat1) * Math.sin(lat2) - Math.sin(lat1) * Math.cos(lat2) * Math.cos(dLon);
  return ((Math.atan2(y, x) * 180 / Math.PI) + 360) % 360;
}

function niceScale(km) {
  const steps = [1, 2, 5, 10, 25, 50, 100, 250, 500, 1000, 2000];
  return steps.find((step) => step >= km) || 5000;
}

function drawGrid(ctx, width, height, view, layers, domain = "earth", scene = {}, redraw = null) {
  ctx.fillStyle = "#020507";
  ctx.fillRect(0, 0, width, height);

  const lonSpan = Math.max(0.000001, view.max_lon - view.min_lon);
  const latSpan = Math.max(0.000001, view.max_lat - view.min_lat);
  const project = (lat, lon) => ({
    x: ((lon - view.min_lon) / lonSpan) * width,
    y: height - ((lat - view.min_lat) / latSpan) * height,
  });

  let drewRaster = false;
  if (layerVisible(layers, "base.imagery")) {
    drewRaster = drawPlateCarreeRaster(
      ctx,
      width,
      height,
      view,
      scene,
      "bluemarble",
      layerOpacity(layers, "base.imagery", 1),
      redraw,
    );
  }

  if (!drewRaster && layerVisible(layers, "base.dark_basemap")) {
    ctx.fillStyle = "#071012";
    ctx.fillRect(0, 0, width, height);
  }

  if (layerVisible(layers, "base.terrain") && !drewRaster) {
    drawPlateCarreeRaster(ctx, width, height, view, scene, "naturalearth", layerOpacity(layers, "base.terrain", 0.72), redraw);
  }

  if (layerVisible(layers, "base.admin_boundaries")) {
    drawPlateCarreeRaster(ctx, width, height, view, scene, "political", layerOpacity(layers, "base.admin_boundaries", 0.42), redraw);
  }

  if (layerVisible(layers, "base.coastline")) {
    drawVectorLayer(ctx, project, scene, "coastline", {
      color: "#d6e6de",
      opacity: layerOpacity(layers, "base.coastline", 0.55),
      width: 0.9,
      simplify: 0.015,
    }, redraw);
  }

  if (layerVisible(layers, "base.admin_boundaries")) {
    drawVectorLayer(ctx, project, scene, "pol", {
      color: "#cfd9d6",
      opacity: layerOpacity(layers, "base.admin_boundaries", 0.42),
      width: 0.8,
      dash: [5, 4],
      simplify: 0.03,
    }, redraw);
    drawVectorLayer(ctx, project, scene, "us", {
      color: "#f0d58a",
      opacity: layerOpacity(layers, "base.admin_boundaries", 0.38),
      width: 0.8,
      dash: [4, 4],
      simplify: 0.005,
    }, redraw);
  }

  if (layerVisible(layers, "base.latlon_grid")) {
    ctx.save();
    ctx.globalAlpha = layerOpacity(layers, "base.latlon_grid", 0.9);
    ctx.strokeStyle = "rgba(109, 160, 151, 0.22)";
    ctx.lineWidth = 1;
    for (let i = 0; i <= 12; i += 1) {
      const x = (width / 12) * i;
      const y = (height / 12) * i;
      const lon = view.min_lon + ((view.max_lon - view.min_lon) / 12) * i;
      const lat = view.max_lat - ((view.max_lat - view.min_lat) / 12) * i;
      ctx.beginPath();
      ctx.moveTo(x, 0);
      ctx.lineTo(x, height);
      ctx.moveTo(0, y);
      ctx.lineTo(width, y);
      ctx.stroke();
      if (i > 0 && i < 12) {
        ctx.fillStyle = "rgba(169, 191, 191, 0.55)";
        ctx.font = "10px Consolas, monospace";
        ctx.fillText(`${lon.toFixed(2)}°`, x + 3, 14);
        ctx.fillText(`${lat.toFixed(2)}°`, 4, y - 3);
      }
    }
    ctx.restore();
  }

  if (layerVisible(layers, "base.airspace_boundaries")) {
    ctx.save();
    ctx.globalAlpha = layerOpacity(layers, "base.airspace_boundaries", 0.5);
    ctx.strokeStyle = "rgba(231, 184, 91, 0.45)";
    ctx.setLineDash([8, 6]);
    ctx.strokeRect(18, 18, width - 36, height - 36);
    ctx.restore();
  }

  ctx.save();
  ctx.fillStyle = "#87979d";
  ctx.font = "11px Consolas, monospace";
  const center = { lat: (view.min_lat + view.max_lat) / 2, lon: (view.min_lon + view.max_lon) / 2 };
  const viewWidthKm = haversineKm({ lat: center.lat, lon: view.min_lon }, { lat: center.lat, lon: view.max_lon });
  const scaleKm = niceScale(viewWidthKm / 5);
  const barWidth = clamp((scaleKm / Math.max(viewWidthKm, 1)) * width, 48, 180);
  ctx.fillText(`LAT ${view.min_lat.toFixed(3)} .. ${view.max_lat.toFixed(3)}`, 26, height - 25);
  ctx.fillText(`LON ${view.min_lon.toFixed(3)} .. ${view.max_lon.toFixed(3)}`, 26, height - 10);
  ctx.fillText(`SCALE ${scaleKm} km`, width - barWidth - 36, height - 13);
  ctx.strokeStyle = "#e7b85b";
  ctx.beginPath();
  ctx.moveTo(width - barWidth - 36, height - 26);
  ctx.lineTo(width - 36, height - 26);
  ctx.stroke();
  ctx.restore();
}

function drawCircleKm(ctx, project, lat, lon, rangeKm, style, alpha = 0.35) {
  const center = project(lat, lon);
  const edge = project(lat, lon + rangeKm / (111.0 * Math.max(Math.cos((lat * Math.PI) / 180), 0.12)));
  const radius = Math.abs(edge.x - center.x);
  ctx.save();
  ctx.globalAlpha = alpha;
  ctx.strokeStyle = style;
  ctx.lineWidth = 1.4;
  ctx.beginPath();
  ctx.arc(center.x, center.y, radius, 0, Math.PI * 2);
  ctx.stroke();
  ctx.restore();
}

function drawRangeHeat(ctx, project, lat, lon, rangeKm, color, alpha = 0.28) {
  const center = project(lat, lon);
  const edge = project(lat, lon + rangeKm / (111.0 * Math.max(Math.cos((lat * Math.PI) / 180), 0.12)));
  const radius = Math.abs(edge.x - center.x);
  const gradient = ctx.createRadialGradient(center.x, center.y, 0, center.x, center.y, radius);
  gradient.addColorStop(0, color.replace(")", `, ${alpha})`).replace("rgb", "rgba"));
  gradient.addColorStop(0.55, color.replace(")", `, ${alpha * 0.25})`).replace("rgb", "rgba"));
  gradient.addColorStop(1, color.replace(")", ", 0)").replace("rgb", "rgba"));
  ctx.save();
  ctx.fillStyle = gradient;
  ctx.beginPath();
  ctx.arc(center.x, center.y, radius, 0, Math.PI * 2);
  ctx.fill();
  ctx.restore();
}

function drawEnvironment(ctx, width, height, layers, frameTime = 0) {
  if (layerVisible(layers, "environment.clouds")) {
    ctx.save();
    ctx.globalAlpha = layerOpacity(layers, "environment.clouds", 0.35);
    ctx.fillStyle = "#a7b1b5";
    for (let i = 0; i < 14; i += 1) {
      const x = (noise(i, frameTime * 0.001, 6) * 1.2 - 0.1) * width;
      const y = (0.12 + noise(i, 4, 7) * 0.62) * height;
      ctx.beginPath();
      ctx.ellipse(x, y, 46 + noise(i, 1, 2) * 60, 12 + noise(i, 2, 3) * 20, 0.15, 0, Math.PI * 2);
      ctx.fill();
    }
    ctx.restore();
  }
  if (layerVisible(layers, "environment.fog")) {
    ctx.save();
    ctx.globalAlpha = layerOpacity(layers, "environment.fog", 0.35);
    ctx.fillStyle = "#a9b8bb";
    ctx.fillRect(0, height * 0.18, width, height * 0.55);
    ctx.restore();
  }
  if (layerVisible(layers, "environment.rain") || layerVisible(layers, "environment.snow")) {
    const snow = layerVisible(layers, "environment.snow");
    ctx.save();
    ctx.globalAlpha = layerOpacity(layers, snow ? "environment.snow" : "environment.rain", snow ? 0.34 : 0.38);
    ctx.strokeStyle = snow ? "#dce7ea" : "#6ea7c4";
    ctx.lineWidth = snow ? 1.4 : 1;
    for (let i = 0; i < 120; i += 1) {
      const x = noise(i, frameTime * 0.01, 9) * width;
      const y = noise(i, frameTime * 0.012, 10) * height;
      ctx.beginPath();
      ctx.moveTo(x, y);
      ctx.lineTo(x + (snow ? 2 : -7), y + (snow ? 2 : 18));
      ctx.stroke();
    }
    ctx.restore();
  }
  if (layerVisible(layers, "environment.wind")) {
    ctx.save();
    ctx.globalAlpha = layerOpacity(layers, "environment.wind", 0.4);
    ctx.strokeStyle = "#9ed0cb";
    for (let y = 60; y < height; y += 90) {
      for (let x = 60; x < width; x += 130) {
        ctx.beginPath();
        ctx.moveTo(x - 18, y);
        ctx.lineTo(x + 18, y - 8);
        ctx.lineTo(x + 10, y - 14);
        ctx.moveTo(x + 18, y - 8);
        ctx.lineTo(x + 8, y - 2);
        ctx.stroke();
      }
    }
    ctx.restore();
  }
}

function drawPath(ctx, project, points, color, options = {}) {
  if (!points || points.length < 2) return;
  ctx.save();
  ctx.globalAlpha = options.alpha ?? 0.7;
  ctx.strokeStyle = color;
  ctx.lineWidth = options.width ?? 2;
  if (options.dash) ctx.setLineDash(options.dash);
  ctx.beginPath();
  points.forEach((point, index) => {
    const p = project(Number(point.lat), Number(point.lon));
    if (index === 0) ctx.moveTo(p.x, p.y);
    else ctx.lineTo(p.x, p.y);
  });
  ctx.stroke();
  if (options.arrows) {
    ctx.fillStyle = color;
    for (let index = 1; index < points.length; index += 1) {
      const a = project(Number(points[index - 1].lat), Number(points[index - 1].lon));
      const b = project(Number(points[index].lat), Number(points[index].lon));
      const angle = Math.atan2(b.y - a.y, b.x - a.x);
      const x = a.x + (b.x - a.x) * 0.62;
      const y = a.y + (b.y - a.y) * 0.62;
      ctx.save();
      ctx.translate(x, y);
      ctx.rotate(angle);
      ctx.beginPath();
      ctx.moveTo(8, 0);
      ctx.lineTo(-4, -4);
      ctx.lineTo(-2, 0);
      ctx.lineTo(-4, 4);
      ctx.closePath();
      ctx.fill();
      ctx.restore();
    }
  }
  ctx.restore();
}

function drawWaypoints(ctx, project, points, color) {
  if (!points || points.length < 2) return;
  ctx.save();
  ctx.font = "10px Consolas, monospace";
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  for (const point of points) {
    const p = project(Number(point.lat), Number(point.lon));
    ctx.fillStyle = "#071013";
    ctx.strokeStyle = color;
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.arc(p.x, p.y, 7, 0, Math.PI * 2);
    ctx.fill();
    ctx.stroke();
    ctx.fillStyle = "#d9e5e4";
    ctx.fillText(String(point.waypoint_index || ""), p.x, p.y + 0.5);
  }
  ctx.restore();
}

function drawSymbol(ctx, entity, x, y, selected) {
  const color = sideColor(entity.side);
  const kind = entity.kind || "aircraft";
  const symbol = entity.symbol || kind;
  ctx.save();
  ctx.translate(x, y);
  ctx.rotate(((Number(entity.heading_deg) || 0) * Math.PI) / 180);
  ctx.fillStyle = color;
  ctx.strokeStyle = "#071013";
  ctx.lineWidth = selected ? 3 : 2;
  if (kind === "aircraft" || kind === "jammer" || ["awacs", "uav", "bomber"].includes(symbol)) {
    ctx.beginPath();
    ctx.moveTo(0, -12);
    ctx.lineTo(8, 10);
    ctx.lineTo(0, 6);
    ctx.lineTo(-8, 10);
    ctx.closePath();
    ctx.fill();
    ctx.stroke();
    if (symbol === "awacs") {
      ctx.beginPath();
      ctx.arc(0, -2, 5, 0, Math.PI * 2);
      ctx.stroke();
    }
    if (symbol === "bomber") {
      ctx.beginPath();
      ctx.moveTo(-13, 2);
      ctx.lineTo(13, 2);
      ctx.stroke();
    }
    if (kind === "jammer" || symbol === "jammer") {
      ctx.strokeStyle = "#e7b85b";
      ctx.beginPath();
      ctx.moveTo(-9, -8);
      ctx.lineTo(-2, -2);
      ctx.lineTo(-8, 4);
      ctx.moveTo(9, -8);
      ctx.lineTo(2, -2);
      ctx.lineTo(8, 4);
      ctx.stroke();
    }
  } else if (kind === "missile") {
    ctx.beginPath();
    ctx.moveTo(0, -13);
    ctx.lineTo(6, 8);
    ctx.lineTo(0, 4);
    ctx.lineTo(-6, 8);
    ctx.closePath();
    ctx.fill();
    ctx.stroke();
  } else if (kind === "ship") {
    ctx.beginPath();
    ctx.moveTo(0, -10);
    ctx.lineTo(11, 2);
    ctx.lineTo(6, 10);
    ctx.lineTo(-6, 10);
    ctx.lineTo(-11, 2);
    ctx.closePath();
    ctx.fill();
    ctx.stroke();
  } else if (kind === "satellite") {
    ctx.fillRect(-5, -5, 10, 10);
    ctx.fillRect(-15, -3, 8, 6);
    ctx.fillRect(7, -3, 8, 6);
    ctx.strokeRect(-5, -5, 10, 10);
  } else if (kind === "radar") {
    ctx.fillRect(-9, -9, 18, 18);
    ctx.strokeRect(-9, -9, 18, 18);
    ctx.beginPath();
    ctx.moveTo(-12, 10);
    ctx.lineTo(0, -14);
    ctx.lineTo(12, 10);
    ctx.stroke();
    ctx.beginPath();
    ctx.arc(0, -2, 14, -Math.PI * 0.8, -Math.PI * 0.2);
    ctx.stroke();
  } else if (kind === "c2") {
    ctx.fillRect(-10, -7, 20, 14);
    ctx.strokeRect(-10, -7, 20, 14);
    ctx.beginPath();
    ctx.moveTo(0, -14);
    ctx.lineTo(0, -4);
    ctx.lineTo(10, -9);
    ctx.closePath();
    ctx.fill();
    ctx.stroke();
  } else if (kind === "ground" || symbol === "command-post") {
    ctx.fillRect(-11, -7, 22, 14);
    ctx.strokeRect(-11, -7, 22, 14);
    ctx.beginPath();
    ctx.moveTo(-12, 8);
    ctx.lineTo(12, 8);
    ctx.stroke();
  } else {
    ctx.beginPath();
    ctx.arc(0, 0, 9, 0, Math.PI * 2);
    ctx.fill();
    ctx.stroke();
  }
  ctx.restore();

  if (selected) {
    ctx.save();
    ctx.strokeStyle = "#e7b85b";
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.arc(x, y, 17, 0, Math.PI * 2);
    ctx.stroke();
    ctx.restore();
  }
}

function drawLabel(ctx, entity, x, y, layers) {
  if (!layerVisible(layers, "dynamic.target_labels")) return;
  ctx.save();
  ctx.font = "12px 'Microsoft YaHei', 'Segoe UI', sans-serif";
  ctx.textBaseline = "bottom";
  ctx.lineWidth = 4;
  const title = entity.name || entity.id;
  ctx.strokeStyle = "#071013";
  ctx.strokeText(title, x + 13, y - 5);
  ctx.fillStyle = "#e8eef2";
  ctx.fillText(title, x + 13, y - 5);
  ctx.textBaseline = "top";
  ctx.fillStyle = "#9aa7b2";
  const alt = Number(entity.alt_m ?? entity.position?.alt_m ?? 0);
  const speed = Number(entity.speed_kts || 0);
  ctx.fillText(`${entity.side || "neutral"} / ${entity.category || entity.kind || "-"}`, x + 13, y + 5);
  ctx.fillStyle = "#b9c5c8";
  ctx.fillText(`${Math.round(alt)}m  ${Math.round(speed)}kt  H${Math.round(Number(entity.heading_deg || 0))}`, x + 13, y + 19);
  ctx.restore();
}

function draw2D(canvas, scene, frame, options) {
  const layers = layerMap(options.layers || scene.layers || []);
  const entities = filteredEntities(scene, frame, options.filters || {});
  const bounds = boundsFor(scene, entities);
  if (!bounds) return { entities, project: () => ({ x: 0, y: 0 }) };
  const { ctx, width, height } = setupCanvas(canvas);
  const view = viewFromState(bounds, options.domain || "earth", options.viewState);
  const lonSpan = Math.max(0.01, view.max_lon - view.min_lon);
  const latSpan = Math.max(0.01, view.max_lat - view.min_lat);
  const project = (lat, lon) => ({
    x: ((lon - view.min_lon) / lonSpan) * width,
    y: height - ((lat - view.min_lat) / latSpan) * height,
  });
  const unproject = (x, y) => ({
    lat: view.max_lat - (y / height) * latSpan,
    lon: view.min_lon + (x / width) * lonSpan,
  });
  const byId = new Map(entities.map((entity) => [entity.id, entity]));
  const redrawBasemap = () => {
    if (!canvas.isConnected || canvas._afsimRedrawPending) return;
    canvas._afsimRedrawPending = true;
    requestAnimationFrame(() => {
      canvas._afsimRedrawPending = false;
      if (canvas.isConnected) draw2D(canvas, scene, frame, options);
    });
  };

  drawGrid(ctx, width, height, view, layers, options.domain || "earth", scene, redrawBasemap);
  if (options.mode === "25d") {
    ctx.save();
    ctx.globalAlpha = 0.22;
    ctx.strokeStyle = "#e7b85b";
    ctx.setLineDash([4, 10]);
    for (let offset = -height; offset < width; offset += 54) {
      ctx.beginPath();
      ctx.moveTo(offset, height);
      ctx.lineTo(offset + height, 0);
      ctx.stroke();
    }
    ctx.restore();
  }

  if (layerVisible(layers, "dynamic.history_tracks")) {
    for (const entity of entities) {
      drawPath(ctx, project, entity.route || [], sideColor(entity.side), {
        alpha: layerOpacity(layers, "dynamic.history_tracks", 0.65),
        width: 2,
        arrows: true,
      });
      drawWaypoints(ctx, project, entity.route || [], sideColor(entity.side));
    }
  }

  if (layerVisible(layers, "dynamic.predicted_tracks")) {
    for (const track of scene.tracks || []) {
      const owner = byId.get(track.platform_id);
      if (!owner) continue;
      drawPath(ctx, project, track.predicted || [], sideColor(track.side), {
        alpha: layerOpacity(layers, "dynamic.predicted_tracks", 0.48),
        width: 1.5,
        dash: [6, 6],
      });
    }
  }

  if (layerVisible(layers, "electromagnetic.power_heatmap")) {
    for (const sensor of scene.sensors || []) {
      const position = positionOf(sensor);
      drawRangeHeat(ctx, project, position.lat, position.lon, Number(sensor.range_km || 0), sensor.type === "jammer" ? "rgb(231,184,91)" : "rgb(90,167,255)", layerOpacity(layers, "electromagnetic.power_heatmap", 0.46));
    }
  }

  if (layerVisible(layers, "electromagnetic.radar_ranges") || layerVisible(layers, "electromagnetic.jamming_zones")) {
    for (const sensor of scene.sensors || []) {
      const position = positionOf(sensor);
      const isJammer = sensor.type === "jammer";
      const layerId = isJammer ? "electromagnetic.jamming_zones" : "electromagnetic.radar_ranges";
      if (!layerVisible(layers, layerId)) continue;
      drawCircleKm(ctx, project, position.lat, position.lon, Number(sensor.range_km || 0), isJammer ? "#e7b85b" : sideColor(sensor.side), layerOpacity(layers, layerId, 0.35));
      if (layerVisible(layers, "electromagnetic.beam_direction") && Number(sensor.beam_width_deg) < 360) {
        const center = project(position.lat, position.lon);
        const heading = ((Number(sensor.azimuth_deg) || 0) - 90) * Math.PI / 180;
        ctx.save();
        ctx.globalAlpha = layerOpacity(layers, "electromagnetic.beam_direction", 0.6);
        ctx.strokeStyle = isJammer ? "#e7b85b" : sideColor(sensor.side);
        ctx.beginPath();
        ctx.moveTo(center.x, center.y);
        ctx.lineTo(center.x + Math.cos(heading) * 84, center.y + Math.sin(heading) * 84);
        ctx.stroke();
        ctx.restore();
      }
    }
  }

  if (layerVisible(layers, "deployment.air_defense")) {
    for (const weapon of scene.weapons || []) {
      if (!["sam", "ship_missile"].includes(weapon.type)) continue;
      const position = positionOf(weapon);
      drawCircleKm(ctx, project, position.lat, position.lon, Number(weapon.range_km || 0), "#ff9a78", 0.24);
    }
  }

  if (layerVisible(layers, "dynamic.strike_ranges") || layerVisible(layers, "electromagnetic.jamming_zones")) {
    for (const weapon of scene.weapons || []) {
      const position = positionOf(weapon);
      if (weapon.type === "jammer" && layerVisible(layers, "electromagnetic.jamming_zones")) {
        drawCircleKm(ctx, project, position.lat, position.lon, Number(weapon.range_km || 0), "#e7b85b", layerOpacity(layers, "electromagnetic.jamming_zones", 0.35));
      } else if (layerVisible(layers, "dynamic.strike_ranges")) {
        drawCircleKm(ctx, project, position.lat, position.lon, Number(weapon.range_km || 0), "#ff9a78", layerOpacity(layers, "dynamic.strike_ranges", 0.24));
      }
    }
  }

  if (layerVisible(layers, "electromagnetic.comm_links")) {
    ctx.save();
    ctx.globalAlpha = layerOpacity(layers, "electromagnetic.comm_links", 0.68);
    ctx.setLineDash([3, 5]);
    for (const comm of scene.communications || []) {
      const source = byId.get(comm.source_id);
      const target = byId.get(comm.target_id);
      if (!source || !target) continue;
      const a = project(source.lat, source.lon);
      const b = project(target.lat, target.lon);
      ctx.strokeStyle = comm.status === "degraded" ? "#e7b85b" : comm.status === "interrupted" ? "#ff6262" : comm.chain_type === "command" ? "#d6e87f" : "#61d4cf";
      ctx.beginPath();
      ctx.moveTo(a.x, a.y);
      ctx.lineTo(b.x, b.y);
      ctx.stroke();
    }
    ctx.restore();
  }

  if (layerVisible(layers, "intelligence.detections")) {
    ctx.save();
    ctx.globalAlpha = layerOpacity(layers, "intelligence.detections", 0.82);
    ctx.strokeStyle = "#d6e87f";
    for (const detection of scene.detections || []) {
      const source = byId.get(detection.detector_id);
      const target = byId.get(detection.target_id);
      if (!source || !target) continue;
      const a = project(source.lat, source.lon);
      const b = project(target.lat, target.lon);
      ctx.beginPath();
      ctx.moveTo(a.x, a.y);
      ctx.lineTo(b.x, b.y);
      ctx.stroke();
    }
    ctx.restore();
  }

  for (const entity of entities) {
    const p = project(Number(entity.lat), Number(entity.lon));
    if (layerVisible(layers, "dynamic.velocity_vectors")) {
      const angle = ((Number(entity.heading_deg) || 0) - 90) * Math.PI / 180;
      ctx.save();
      ctx.globalAlpha = layerOpacity(layers, "dynamic.velocity_vectors", 0.8);
      ctx.strokeStyle = sideColor(entity.side);
      ctx.beginPath();
      ctx.moveTo(p.x, p.y);
      ctx.lineTo(p.x + Math.cos(angle) * 28, p.y + Math.sin(angle) * 28);
      ctx.stroke();
      ctx.restore();
    }
    drawSymbol(ctx, entity, p.x, p.y, entity.id === options.selectedId);
    drawLabel(ctx, entity, p.x, p.y, layers);
  }

  if (layerVisible(layers, "replay.event_markers")) {
    const currentTime = Number(frame?.sim_time ?? scene.simulation_time?.current ?? 0);
    const frameEventIds = new Set(frame?.events || []);
    const activeEvents = (scene.events || [])
      .filter((event) => event.platform_id && byId.has(event.platform_id))
      .filter((event) => frameEventIds.has(event.id) || Math.abs(Number(event.time || 0) - currentTime) <= 6 || currentTime === 0)
      .slice(0, 32);
    for (const event of activeEvents) {
      const entity = byId.get(event.platform_id);
      const p = project(entity.lat, entity.lon);
      const pulse = 1 + (Math.sin((currentTime + Number(event.time || 0)) * 5) + 1) * 0.18;
      ctx.save();
      ctx.globalAlpha = layerOpacity(layers, "replay.event_markers", 0.7);
      ctx.strokeStyle = event.type === "hit" ? "#ff6262" : "#e7b85b";
      ctx.lineWidth = 1.4;
      ctx.beginPath();
      ctx.arc(p.x, p.y, 22 * pulse, 0, Math.PI * 2);
      ctx.stroke();
      ctx.restore();
    }
  }

  drawEnvironment(ctx, width, height, layers, Number(frame?.sim_time || 0));

  return { entities, project, unproject, view, width, height };
}

function latLonToVector(lat, lon, radius) {
  const phi = THREE.MathUtils.degToRad(90 - lat);
  const theta = THREE.MathUtils.degToRad(lon + 180);
  return new THREE.Vector3(
    -radius * Math.sin(phi) * Math.cos(theta),
    radius * Math.cos(phi),
    radius * Math.sin(phi) * Math.sin(theta),
  );
}

function trackMaterial(color, opacity = 1, dashed = false) {
  const material = dashed
    ? new THREE.LineDashedMaterial({ color, transparent: opacity < 1, opacity, dashSize: 0.02, gapSize: 0.015 })
    : new THREE.LineBasicMaterial({ color, transparent: opacity < 1, opacity });
  globeState.materials.push(material);
  return material;
}

function addLine(group, points, material) {
  if (!points || points.length < 2) return;
  const geometry = new THREE.BufferGeometry().setFromPoints(points);
  globeState.geometries.push(geometry);
  const line = new THREE.Line(geometry, material);
  if (line.computeLineDistances) line.computeLineDistances();
  group.add(line);
}

function trackedGeometry(geometry) {
  globeState.geometries.push(geometry);
  return geometry;
}

function trackedMaterial(material) {
  globeState.materials.push(material);
  return material;
}

function platformModel(entity, color, selected) {
  const group = new THREE.Group();
  const material = trackedMaterial(new THREE.MeshBasicMaterial({ color }));
  const darkMaterial = trackedMaterial(new THREE.MeshBasicMaterial({ color: 0x071013 }));
  const kind = entity.kind || "aircraft";
  const symbol = entity.symbol || kind;
  const scale = selected ? 1.35 : 1;

  if (kind === "aircraft" || symbol === "awacs" || symbol === "bomber" || symbol === "uav") {
    const body = new THREE.Mesh(trackedGeometry(new THREE.ConeGeometry(0.018 * scale, 0.075 * scale, 4)), material);
    body.rotation.x = Math.PI / 2;
    group.add(body);
    const wing = new THREE.Mesh(trackedGeometry(new THREE.BoxGeometry(0.075 * scale, 0.006 * scale, 0.018 * scale)), material);
    group.add(wing);
    if (symbol === "awacs") {
      const disc = new THREE.Mesh(trackedGeometry(new THREE.CylinderGeometry(0.023 * scale, 0.023 * scale, 0.004 * scale, 24)), darkMaterial);
      disc.position.y = 0.018 * scale;
      group.add(disc);
    }
  } else if (kind === "missile") {
    const missile = new THREE.Mesh(trackedGeometry(new THREE.ConeGeometry(0.012 * scale, 0.085 * scale, 12)), material);
    missile.rotation.x = Math.PI / 2;
    group.add(missile);
  } else if (kind === "ship" || kind === "submarine") {
    const hull = new THREE.Mesh(trackedGeometry(new THREE.BoxGeometry(0.075 * scale, 0.018 * scale, 0.026 * scale)), material);
    group.add(hull);
  } else if (kind === "satellite") {
    group.add(new THREE.Mesh(trackedGeometry(new THREE.BoxGeometry(0.026 * scale, 0.026 * scale, 0.026 * scale)), material));
    const panelA = new THREE.Mesh(trackedGeometry(new THREE.BoxGeometry(0.055 * scale, 0.004 * scale, 0.022 * scale)), material);
    panelA.position.x = -0.044 * scale;
    const panelB = panelA.clone();
    panelB.position.x = 0.044 * scale;
    group.add(panelA, panelB);
  } else if (kind === "radar") {
    group.add(new THREE.Mesh(trackedGeometry(new THREE.BoxGeometry(0.04 * scale, 0.018 * scale, 0.04 * scale)), material));
    const dish = new THREE.Mesh(trackedGeometry(new THREE.ConeGeometry(0.032 * scale, 0.018 * scale, 24, 1, true)), material);
    dish.position.y = 0.028 * scale;
    dish.rotation.x = Math.PI;
    group.add(dish);
  } else {
    group.add(new THREE.Mesh(trackedGeometry(new THREE.BoxGeometry(0.04 * scale, 0.025 * scale, 0.04 * scale)), material));
  }
  return group;
}

function render3D(container, scene, frame, options) {
  const layers = layerMap(options.layers || scene.layers || []);
  const entities = filteredEntities(scene, frame, options.filters || {});
  const byId = new Map(entities.map((entity) => [entity.id, entity]));
  const rect = container.getBoundingClientRect();
  const width = Math.max(320, rect.width);
  const height = Math.max(240, rect.height);
  const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  renderer.setSize(width, height);
  renderer.setClearColor(0x05080a, 1);
  if (THREE.SRGBColorSpace) renderer.outputColorSpace = THREE.SRGBColorSpace;
  container.appendChild(renderer.domElement);
  globeState.renderer = renderer;

  const threeScene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(45, width / height, 0.1, 1000);
  camera.position.set(0, 0, options.domain === "space" ? 4.1 : 3.2);

  const group = new THREE.Group();
  threeScene.add(group);

  const earthGeometry = new THREE.SphereGeometry(1, 72, 36);
  const earthMaterial = new THREE.MeshPhongMaterial({
    color: 0x14333b,
    emissive: 0x041014,
    shininess: 10,
    specular: 0x44616a,
  });
  const globeTextureUrl = scene.map_resources?.globe_texture?.url || "/api/afsim/maps/bluemarble/texture.jpg?z=3";
  if (layerVisible(layers, "base.imagery") && globeTextureUrl) {
    const texture = new THREE.TextureLoader().load(globeTextureUrl, () => {
      earthMaterial.needsUpdate = true;
    });
    if (THREE.SRGBColorSpace) texture.colorSpace = THREE.SRGBColorSpace;
    texture.anisotropy = renderer.capabilities.getMaxAnisotropy();
    earthMaterial.map = texture;
    earthMaterial.color.set(0xffffff);
    globeState.textures.push(texture);
  }
  globeState.geometries.push(earthGeometry);
  globeState.materials.push(earthMaterial);
  group.add(new THREE.Mesh(earthGeometry, earthMaterial));

  if (layerVisible(layers, "base.latlon_grid")) {
    const wireGeometry = new THREE.SphereGeometry(1.004, 36, 18);
    const wireMaterial = new THREE.MeshBasicMaterial({
      color: 0x76a7a0,
      wireframe: true,
      transparent: true,
      opacity: 0.16 * layerOpacity(layers, "base.latlon_grid", 0.9),
    });
    globeState.geometries.push(wireGeometry);
    globeState.materials.push(wireMaterial);
    group.add(new THREE.Mesh(wireGeometry, wireMaterial));
  }

  if (layerVisible(layers, "environment.clouds") || layerVisible(layers, "environment.fog")) {
    const cloudMaterial = new THREE.MeshBasicMaterial({
      color: 0xb8c4c7,
      transparent: true,
      opacity: layerVisible(layers, "environment.fog") ? 0.12 : 0.18 * layerOpacity(layers, "environment.clouds", 0.35),
      depthWrite: false,
    });
    globeState.materials.push(cloudMaterial);
    for (let i = 0; i < 28; i += 1) {
      const cloudGeometry = new THREE.SphereGeometry(0.018 + noise(i, 2, 4) * 0.022, 12, 8);
      globeState.geometries.push(cloudGeometry);
      const cloud = new THREE.Mesh(cloudGeometry, cloudMaterial);
      const lat = -55 + noise(i, 5, 6) * 110;
      const lon = -180 + noise(i, 9, 7) * 360;
      cloud.position.copy(latLonToVector(lat, lon, 1.06 + noise(i, 11, 8) * 0.04));
      group.add(cloud);
    }
  }

  const light = new THREE.DirectionalLight(0xffffff, 1.7);
  light.position.set(4, 3, 5);
  threeScene.add(light);
  threeScene.add(new THREE.AmbientLight(0x7d9298, 0.6));

  for (const entity of entities) {
    const color = new THREE.Color(sideColor(entity.side));
    const marker = platformModel(entity, color, entity.id === options.selectedId);
    const altitudeScale = options.domain === "space" ? 900000 : 2000000;
    marker.position.copy(latLonToVector(Number(entity.lat), Number(entity.lon), 1.035 + Math.min(Number(entity.alt_m || 0) / altitudeScale, 0.16)));
    marker.lookAt(0, 0, 0);
    marker.rotateZ(THREE.MathUtils.degToRad(Number(entity.heading_deg || 0)));
    group.add(marker);

    if (layerVisible(layers, "dynamic.history_tracks")) {
      addLine(
        group,
        (entity.route || []).map((point) => latLonToVector(Number(point.lat), Number(point.lon), 1.025 + Math.min(Number(point.alt_m || 0) / altitudeScale, 0.16))),
        trackMaterial(color, 0.72 * layerOpacity(layers, "dynamic.history_tracks", 0.65)),
      );
    }
  }

  if (layerVisible(layers, "electromagnetic.comm_links")) {
    for (const comm of scene.communications || []) {
      const source = byId.get(comm.source_id);
      const target = byId.get(comm.target_id);
      if (!source || !target) continue;
      addLine(
        group,
        [
          latLonToVector(source.lat, source.lon, 1.055),
          latLonToVector(target.lat, target.lon, 1.055),
        ],
        trackMaterial(0x61d4cf, 0.48 * layerOpacity(layers, "electromagnetic.comm_links", 0.68), true),
      );
    }
  }

  const bounds = boundsFor(scene, entities);
  if (bounds) {
    const centerLat = (bounds.min_lat + bounds.max_lat) / 2;
    const centerLon = (bounds.min_lon + bounds.max_lon) / 2;
    const focus = latLonToVector(centerLat, centerLon, 1);
    const axis = new THREE.Vector3(0, 0, 1).cross(focus).normalize();
    const angle = new THREE.Vector3(0, 0, 1).angleTo(focus);
    if (Number.isFinite(angle) && axis.lengthSq() > 0) group.quaternion.setFromAxisAngle(axis, -angle);
  }

  let globeDrag = null;
  renderer.domElement.addEventListener("mousedown", (event) => {
    globeDrag = { x: event.clientX, y: event.clientY };
  });
  renderer.domElement.addEventListener("mousemove", (event) => {
    if (!globeDrag) return;
    const dx = event.clientX - globeDrag.x;
    const dy = event.clientY - globeDrag.y;
    group.rotation.y += dx * 0.004;
    group.rotation.x += dy * 0.003;
    globeDrag = { x: event.clientX, y: event.clientY };
  });
  renderer.domElement.addEventListener("mouseup", () => {
    globeDrag = null;
  });
  renderer.domElement.addEventListener("mouseleave", () => {
    globeDrag = null;
  });
  renderer.domElement.addEventListener("wheel", (event) => {
    event.preventDefault();
    camera.position.z = clamp(camera.position.z + (event.deltaY > 0 ? 0.18 : -0.18), 1.75, 6.5);
  }, { passive: false });

  const animate = () => {
    if (!globeDrag) group.rotation.y += options.domain === "space" ? 0.0022 : 0.0012;
    renderer.render(threeScene, camera);
    globeState.animationId = requestAnimationFrame(animate);
  };
  animate();
}

function dispatchMapEvent(canvas, name, detail) {
  canvas.dispatchEvent(new CustomEvent(name, { bubbles: true, detail }));
}

function nearestEntity(drawResult, x, y, maxDistance = 28) {
  let nearest = null;
  let nearestDistance = Infinity;
  for (const entity of drawResult.entities || []) {
    const p = drawResult.project(Number(entity.lat), Number(entity.lon));
    const distance = Math.hypot(p.x - x, p.y - y);
    if (distance < nearestDistance) {
      nearest = entity;
      nearestDistance = distance;
    }
  }
  return nearest && nearestDistance <= maxDistance ? { entity: nearest, distance: nearestDistance } : null;
}

function attachCanvasEvents(canvas, drawResult, options = {}) {
  const readout = canvas.closest(".map-stage")?.querySelector("[data-map-readout]");
  let dragStart = null;
  let dragMoved = false;
  const startView = () => viewStateFromView(drawResult.view);
  const geoFromEvent = (event) => {
    const rect = canvas.getBoundingClientRect();
    return {
      rect,
      x: event.clientX - rect.left,
      y: event.clientY - rect.top,
      geo: drawResult.unproject(event.clientX - rect.left, event.clientY - rect.top),
    };
  };

  canvas.addEventListener("wheel", (event) => {
    if (!drawResult.unproject) return;
    event.preventDefault();
    const { x, y, geo } = geoFromEvent(event);
    const view = drawResult.view;
    const factor = event.deltaY > 0 ? 1.18 : 0.84;
    const nextLatSpan = clamp((view.max_lat - view.min_lat) * factor, 0.002, 180);
    const nextLonSpan = clamp((view.max_lon - view.min_lon) * factor, 0.002, 360);
    const rx = x / Math.max(drawResult.width, 1);
    const ry = y / Math.max(drawResult.height, 1);
    const minLon = geo.lon - rx * nextLonSpan;
    const maxLat = geo.lat + ry * nextLatSpan;
    dispatchMapEvent(canvas, "afsim-map-view-change", {
      centerLat: clamp(maxLat - nextLatSpan / 2, -89.999, 89.999),
      centerLon: clamp(minLon + nextLonSpan / 2, -179.999, 179.999),
      latSpan: nextLatSpan,
      lonSpan: nextLonSpan,
      reason: "wheel",
    });
  }, { passive: false });

  canvas.addEventListener("mousedown", (event) => {
    if (event.button !== 0) return;
    const point = geoFromEvent(event);
    dragStart = { ...point, view: startView() };
    dragMoved = false;
    canvas.classList.add("dragging");
  });

  canvas.addEventListener("mousemove", (event) => {
    if (!dragStart) return;
    const rect = canvas.getBoundingClientRect();
    const x = event.clientX - rect.left;
    const y = event.clientY - rect.top;
    const dx = x - dragStart.x;
    const dy = y - dragStart.y;
    dragMoved = dragMoved || Math.hypot(dx, dy) > 4;
  });

  canvas.addEventListener("mouseup", (event) => {
    if (!dragStart) return;
    canvas.classList.remove("dragging");
    const end = geoFromEvent(event);
    if (options.interactionMode === "box" && dragMoved) {
      const bounds = {
        min_lat: Math.min(dragStart.geo.lat, end.geo.lat),
        max_lat: Math.max(dragStart.geo.lat, end.geo.lat),
        min_lon: Math.min(dragStart.geo.lon, end.geo.lon),
        max_lon: Math.max(dragStart.geo.lon, end.geo.lon),
      };
      const ids = (drawResult.entities || [])
        .filter((entity) => Number(entity.lat) >= bounds.min_lat && Number(entity.lat) <= bounds.max_lat && Number(entity.lon) >= bounds.min_lon && Number(entity.lon) <= bounds.max_lon)
        .map((entity) => entity.id);
      dispatchMapEvent(canvas, "afsim-map-box-select", { bounds, ids });
      dragStart = null;
      return;
    }
    if (dragMoved) {
      const dx = end.x - dragStart.x;
      const dy = end.y - dragStart.y;
      dispatchMapEvent(canvas, "afsim-map-view-change", {
        centerLat: clamp(dragStart.view.centerLat + (dy / Math.max(drawResult.height, 1)) * dragStart.view.latSpan, -89.999, 89.999),
        centerLon: clamp(dragStart.view.centerLon - (dx / Math.max(drawResult.width, 1)) * dragStart.view.lonSpan, -179.999, 179.999),
        latSpan: dragStart.view.latSpan,
        lonSpan: dragStart.view.lonSpan,
        reason: "pan",
      });
      dragStart = null;
      return;
    }
    if (!dragMoved) {
      const nearest = nearestEntity(drawResult, end.x, end.y);
      if (options.interactionMode === "measure") {
        dispatchMapEvent(canvas, "afsim-map-measure-point", { point: end.geo });
      } else if (options.interactionMode === "edit") {
        dispatchMapEvent(canvas, "afsim-map-edit-point", { point: end.geo, targetId: nearest?.entity?.id || null });
      } else if (nearest) {
        dispatchMapEvent(canvas, "afsim-map-select", { id: nearest.entity.id });
      }
    }
    dragStart = null;
  });

  canvas.addEventListener("mouseleave", () => {
    dragStart = null;
    canvas.classList.remove("dragging");
  });

  canvas.addEventListener("mousemove", (event) => {
    if (!readout || !drawResult.unproject) return;
    const rect = canvas.getBoundingClientRect();
    const x = event.clientX - rect.left;
    const y = event.clientY - rect.top;
    const geo = drawResult.unproject(x, y);
    const center = {
      lat: (drawResult.view.min_lat + drawResult.view.max_lat) / 2,
      lon: (drawResult.view.min_lon + drawResult.view.max_lon) / 2,
    };
    const distance = haversineKm(center, geo);
    const bearing = bearingDeg(center, geo);
    const nearest = nearestEntity(drawResult, x, y, 22);
    const targetText = nearest ? `  TGT ${nearest.entity.id}` : "";
    readout.textContent = `LAT ${geo.lat.toFixed(5)}  LON ${geo.lon.toFixed(5)}  RNG ${distance.toFixed(1)} km  BRG ${bearing.toFixed(1)}°${targetText}`;
  });
}

export function renderOperationalMap(container, scene, options = {}) {
  const mode = options.mode || "split";
  const frame = options.frame || null;
  const domain = options.domain || "earth";
  disposeGlobe();
  const entities = filteredEntities(scene, frame, options.filters || {});
  const frameText = frame ? `T+${Number(frame.sim_time || 0).toFixed(1)}s / ${frame.source || "stream"}` : "静态解析态势";
  const domainText = domain === "space" ? "太空视角" : domain === "near_space" ? "临近空间" : "地球地理";
  const modeText = mode === "3d" ? "3D 地球" : mode === "25d" ? "2.5D 地形" : mode === "2d" ? "2D 战术图" : "2D / 3D 同屏";
  const viewState = options.viewState || null;
  const viewText = viewState ? `视域 ${Number(viewState.lonSpan || 0).toFixed(3)}° x ${Number(viewState.latSpan || 0).toFixed(3)}°` : "自动适配";
  const interactionText = {
    select: "选择",
    measure: "测量",
    box: "框选",
    edit: "编辑",
  }[options.interactionMode || "select"] || "选择";
  container.innerHTML = `
    <div class="map-stage map-mode-${mode}">
      <div class="map-pane map-pane-2d">
        <canvas class="afsim-map-canvas" data-map-2d></canvas>
      </div>
      <div class="map-pane map-pane-3d" data-map-3d></div>
      <div class="map-hud">
        <strong>${modeText}</strong>
        <span>${domainText}</span>
        <span>${frameText}</span>
        <span>${viewText}</span>
        <span>${interactionText}</span>
        <span>${entities.length} targets</span>
      </div>
      <div class="map-readout" data-map-readout>LAT --  LON --  RNG --  BRG --</div>
    </div>
  `;
  const canvas = container.querySelector("[data-map-2d]");
  const globe = container.querySelector("[data-map-3d]");
  if (mode !== "3d") {
    const drawResult = draw2D(canvas, scene, frame, { ...options, domain, mode });
    attachCanvasEvents(canvas, drawResult, options);
  }
  if (mode !== "2d" && mode !== "25d") render3D(globe, scene, frame, { ...options, domain });
}

export function disposeOperationalMap() {
  disposeGlobe();
}
