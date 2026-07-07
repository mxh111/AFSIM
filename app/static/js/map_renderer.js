const TILE_SIZE = 256;
const rasterTileCache = new Map();
const vectorLayerCache = new Map();
const operationalMapInstances = new WeakMap();
const MAX_VECTOR_LAYER_CACHE_ENTRIES = 32;

export function sideColor(side) {
  if (side === "blue") return "#5aa7ff";
  if (side === "red") return "#ff7373";
  if (side === "green") return "#65d18d";
  return "#c2c8ce";
}

export function disposeOperationalMap(container = null) {
  if (!container) return;
  const instance = operationalMapInstances.get(container);
  if (instance?.redrawFrame) cancelAnimationFrame(instance.redrawFrame);
  if (instance?.viewChangeFrame) cancelAnimationFrame(instance.viewChangeFrame);
  operationalMapInstances.delete(container);
  container.innerHTML = "";
}

function clamp(value, min, max) {
  return Math.max(min, Math.min(max, value));
}

function rounded(value, digits = 3) {
  const factor = 10 ** digits;
  return Math.round(Number(value) * factor) / factor;
}

function vectorBbox(view) {
  if (!view) return null;
  const latSpan = Number(view.max_lat) - Number(view.min_lat);
  const lonSpan = Number(view.max_lon) - Number(view.min_lon);
  if (!Number.isFinite(latSpan) || !Number.isFinite(lonSpan) || latSpan <= 0 || lonSpan <= 0) return null;
  const padLat = latSpan * 0.2;
  const padLon = lonSpan * 0.2;
  const minLon = clamp(Number(view.min_lon) - padLon, -180, 180);
  const maxLon = clamp(Number(view.max_lon) + padLon, -180, 180);
  const minLat = clamp(Number(view.min_lat) - padLat, -90, 90);
  const maxLat = clamp(Number(view.max_lat) + padLat, -90, 90);
  return [minLon, minLat, maxLon, maxLat].map((value) => rounded(value, 3)).join(",");
}

function trimVectorCache() {
  while (vectorLayerCache.size >= MAX_VECTOR_LAYER_CACHE_ENTRIES) {
    const oldestKey = vectorLayerCache.keys().next().value;
    if (!oldestKey) break;
    vectorLayerCache.delete(oldestKey);
  }
}

function layerMap(scene) {
  return new Map((scene?.layers || []).map((layer) => [layer.id, layer]));
}

function layerVisible(layers, id, fallback = true) {
  const layer = layers.get(id);
  return layer ? layer.visible !== false : fallback;
}

function layerOpacity(layers, id, fallback = 1) {
  const value = Number(layers.get(id)?.opacity);
  return Number.isFinite(value) ? clamp(value, 0, 1) : fallback;
}

function setupCanvas(canvas) {
  const rect = canvas.getBoundingClientRect();
  const ratio = Math.min(window.devicePixelRatio || 1, 2);
  const pixelWidth = Math.max(320, Math.floor(rect.width * ratio));
  const pixelHeight = Math.max(260, Math.floor(rect.height * ratio));
  if (canvas.width !== pixelWidth || canvas.height !== pixelHeight) {
    canvas.width = pixelWidth;
    canvas.height = pixelHeight;
  }
  const ctx = canvas.getContext("2d");
  ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
  ctx.clearRect(0, 0, canvas.width / ratio, canvas.height / ratio);
  return { ctx, width: canvas.width / ratio, height: canvas.height / ratio };
}

function positionOf(item) {
  const position = item?.position || item || {};
  return {
    lat: Number(position.lat ?? item?.lat ?? 0),
    lon: Number(position.lon ?? item?.lon ?? 0),
    alt_m: Number(position.alt_m ?? item?.alt_m ?? 0),
  };
}

function normalizeEntity(entity, fallback = {}) {
  const position = positionOf(entity);
  return {
    ...fallback,
    ...entity,
    id: String(entity?.id || fallback?.id || ""),
    name: entity?.name || fallback?.name || entity?.id || fallback?.id || "",
    side: entity?.side || fallback?.side || "neutral",
    kind: entity?.kind || fallback?.kind || entity?.category || fallback?.category || "aircraft",
    symbol: entity?.symbol || fallback?.symbol || entity?.kind || fallback?.kind || "aircraft",
    lat: position.lat,
    lon: position.lon,
    alt_m: position.alt_m,
    heading_deg: Number(entity?.heading_deg ?? fallback?.heading_deg ?? 0),
    route: entity?.route || fallback?.route || fallback?.history_route || [],
  };
}

function mergeFrameEntities(scene, frame) {
  const platforms = scene?.platforms || [];
  const frameEntities = frame?.entities || [];
  const byPlatform = new Map(platforms.map((platform) => [platform.id, normalizeEntity(platform)]));
  if (!frameEntities.length) return [...byPlatform.values()];
  const result = [];
  const seen = new Set();
  for (const frameEntity of frameEntities) {
    const base = byPlatform.get(frameEntity.id) || {};
    const merged = normalizeEntity(frameEntity, base);
    result.push(merged);
    seen.add(merged.id);
  }
  for (const platform of byPlatform.values()) {
    if (!seen.has(platform.id)) result.push(platform);
  }
  return result;
}

function boundsFor(scene, entities) {
  const lats = [];
  const lons = [];
  for (const entity of entities) {
    if (Number.isFinite(entity.lat) && Number.isFinite(entity.lon)) {
      lats.push(entity.lat);
      lons.push(entity.lon);
    }
  }
  if (scene?.bounds) {
    lats.push(Number(scene.bounds.min_lat), Number(scene.bounds.max_lat));
    lons.push(Number(scene.bounds.min_lon), Number(scene.bounds.max_lon));
  }
  if (!lats.length || !lons.length) {
    return { min_lat: -30, max_lat: 30, min_lon: -60, max_lon: 60 };
  }
  return {
    min_lat: Math.min(...lats),
    max_lat: Math.max(...lats),
    min_lon: Math.min(...lons),
    max_lon: Math.max(...lons),
  };
}

function paddedBounds(bounds) {
  const latSpan = Math.max(0.08, bounds.max_lat - bounds.min_lat);
  const lonSpan = Math.max(0.08, bounds.max_lon - bounds.min_lon);
  return {
    min_lat: clamp(bounds.min_lat - latSpan * 0.22, -90, 90),
    max_lat: clamp(bounds.max_lat + latSpan * 0.22, -90, 90),
    min_lon: clamp(bounds.min_lon - lonSpan * 0.22, -180, 180),
    max_lon: clamp(bounds.max_lon + lonSpan * 0.22, -180, 180),
  };
}

function viewFromState(bounds, viewState) {
  const fitted = paddedBounds(bounds);
  if (!viewState) return fitted;
  const latSpan = clamp(Number(viewState.latSpan || fitted.max_lat - fitted.min_lat), 0.002, 180);
  const lonSpan = clamp(Number(viewState.lonSpan || fitted.max_lon - fitted.min_lon), 0.002, 360);
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

function makeProject(view, width, height) {
  const lonSpan = Math.max(0.000001, view.max_lon - view.min_lon);
  const latSpan = Math.max(0.000001, view.max_lat - view.min_lat);
  const project = (lat, lon) => ({
    x: ((lon - view.min_lon) / lonSpan) * width,
    y: height - ((lat - view.min_lat) / latSpan) * height,
  });
  const unproject = (x, y) => ({
    lon: view.min_lon + (x / Math.max(width, 1)) * lonSpan,
    lat: view.max_lat - (y / Math.max(height, 1)) * latSpan,
  });
  return { project, unproject };
}

function rasterLayers(scene) {
  return Array.isArray(scene?.map_resources?.raster_layers) ? scene.map_resources.raster_layers : [];
}

function vectorLayers(scene) {
  return Array.isArray(scene?.map_resources?.vector_layers) ? scene.map_resources.vector_layers : [];
}

function rasterResource(scene) {
  return rasterLayers(scene).find((item) => item.id === "bluemarble" && item.exists)
    || rasterLayers(scene).find((item) => item.id === "naturalearth" && item.exists)
    || null;
}

function vectorResource(scene, id) {
  return vectorLayers(scene).find((item) => item.id === id || (item.aliases || []).includes(id)) || null;
}

function replaceTileTemplate(template, z, x, y) {
  return template.replaceAll("{z}", String(z)).replaceAll("{x}", String(x)).replaceAll("{y}", String(y));
}

function loadImage(url, redraw) {
  let cached = rasterTileCache.get(url);
  if (!cached) {
    const image = new Image();
    cached = { image, status: "loading", callbacks: new Set() };
    rasterTileCache.set(url, cached);
    image.onload = () => {
      cached.status = "ready";
      for (const callback of cached.callbacks) callback();
      cached.callbacks.clear();
    };
    image.onerror = () => {
      cached.status = "error";
      cached.callbacks.clear();
    };
    image.src = url;
  }
  if (cached.status === "loading" && redraw) cached.callbacks.add(redraw);
  return cached;
}

function chooseZoom(resource, view, width, height) {
  const minZoom = Number(resource.min_zoom ?? 0);
  const maxZoom = Number(resource.max_zoom ?? 5);
  const lonSpan = Math.max(0.000001, view.max_lon - view.min_lon);
  const latSpan = Math.max(0.000001, view.max_lat - view.min_lat);
  const pxPerDegree = Math.max(width / lonSpan, height / latSpan);
  const ideal = Math.ceil(Math.log2(Math.max(1, (pxPerDegree * 180) / TILE_SIZE)));
  return clamp(ideal, minZoom, maxZoom);
}

function drawFallbackBasemap(ctx, width, height, view) {
  const gradient = ctx.createLinearGradient(0, 0, 0, height);
  gradient.addColorStop(0, "#11191c");
  gradient.addColorStop(0.5, "#0b1418");
  gradient.addColorStop(1, "#111315");
  ctx.fillStyle = gradient;
  ctx.fillRect(0, 0, width, height);

  const { project } = makeProject(view, width, height);
  ctx.save();
  ctx.strokeStyle = "rgba(96, 138, 132, 0.24)";
  ctx.lineWidth = 1;
  const lonStep = Math.max(0.05, Math.pow(10, Math.floor(Math.log10((view.max_lon - view.min_lon) / 6))));
  const latStep = Math.max(0.05, Math.pow(10, Math.floor(Math.log10((view.max_lat - view.min_lat) / 6))));
  for (let lon = Math.ceil(view.min_lon / lonStep) * lonStep; lon <= view.max_lon; lon += lonStep) {
    const a = project(view.min_lat, lon);
    const b = project(view.max_lat, lon);
    ctx.beginPath();
    ctx.moveTo(a.x, a.y);
    ctx.lineTo(b.x, b.y);
    ctx.stroke();
  }
  for (let lat = Math.ceil(view.min_lat / latStep) * latStep; lat <= view.max_lat; lat += latStep) {
    const a = project(lat, view.min_lon);
    const b = project(lat, view.max_lon);
    ctx.beginPath();
    ctx.moveTo(a.x, a.y);
    ctx.lineTo(b.x, b.y);
    ctx.stroke();
  }
  ctx.restore();
}

function drawRaster(ctx, width, height, view, scene, redraw) {
  const resource = rasterResource(scene);
  if (!resource) return false;
  const z = chooseZoom(resource, view, width, height);
  const cols = 2 ** (z + 1);
  const rows = 2 ** z;
  const tileLonSpan = 360 / cols;
  const tileLatSpan = 180 / rows;
  const lonSpan = Math.max(0.000001, view.max_lon - view.min_lon);
  const latSpan = Math.max(0.000001, view.max_lat - view.min_lat);
  const template = resource.tile_url_template || `/api/afsim/maps/${resource.id}/{z}/{x}/{y}.png`;
  const xStart = clamp(Math.floor((view.min_lon + 180) / tileLonSpan), 0, cols - 1);
  const xEnd = clamp(Math.floor((view.max_lon + 180 - 1e-9) / tileLonSpan), 0, cols - 1);
  const yStart = clamp(Math.floor((90 - view.max_lat) / tileLatSpan), 0, rows - 1);
  const yEnd = clamp(Math.floor((90 - view.min_lat - 1e-9) / tileLatSpan), 0, rows - 1);
  let drewAny = false;

  ctx.save();
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
      const tile = loadImage(replaceTileTemplate(template, z, x, y), redraw);
      if (tile.status === "ready") {
        ctx.drawImage(tile.image, dx, dy, dw + 0.75, dh + 0.75);
        drewAny = true;
      }
    }
  }
  ctx.restore();
  return drewAny;
}

function loadVector(url, redraw) {
  let cached = vectorLayerCache.get(url);
  if (!cached) {
    trimVectorCache();
    cached = { status: "loading", data: null, callbacks: new Set() };
    vectorLayerCache.set(url, cached);
    fetch(url)
      .then((response) => {
        if (!response.ok) throw new Error(String(response.status));
        return response.json();
      })
      .then((data) => {
        cached.status = "ready";
        cached.data = data;
        for (const callback of cached.callbacks) callback();
        cached.callbacks.clear();
      })
      .catch(() => {
        cached.status = "error";
        cached.callbacks.clear();
      });
  }
  if (cached.status === "loading" && redraw) cached.callbacks.add(redraw);
  return cached;
}

function vectorUrl(scene, id, simplify, view) {
  const resource = vectorResource(scene, id);
  if (!resource?.exists) return null;
  const base = resource.geojson_url || `/api/afsim/maps/vectors/${resource.id}.geojson`;
  const join = base.includes("?") ? "&" : "?";
  const params = [`simplify=${encodeURIComponent(simplify)}`];
  const bbox = vectorBbox(view);
  if (bbox) params.push(`bbox=${encodeURIComponent(bbox)}`);
  return `${base}${join}${params.join("&")}`;
}

function drawGeoLine(ctx, project, coordinates) {
  let started = false;
  for (const point of coordinates || []) {
    const lon = Number(point?.[0]);
    const lat = Number(point?.[1]);
    if (!Number.isFinite(lat) || !Number.isFinite(lon)) {
      started = false;
      continue;
    }
    const p = project(lat, lon);
    if (!started) {
      ctx.moveTo(p.x, p.y);
      started = true;
    } else {
      ctx.lineTo(p.x, p.y);
    }
  }
}

function drawVectorLayer(ctx, project, scene, id, style, view, redraw) {
  const url = vectorUrl(scene, id, style.simplify ?? 0.02, view);
  if (!url) return;
  const layer = loadVector(url, redraw);
  if (layer.status !== "ready" || !layer.data?.features?.length) return;
  ctx.save();
  ctx.globalAlpha = style.opacity ?? 0.5;
  ctx.strokeStyle = style.color || "#d6e6de";
  ctx.lineWidth = style.width ?? 1;
  if (style.dash) ctx.setLineDash(style.dash);
  ctx.beginPath();
  for (const feature of layer.data.features) {
    const geometry = feature.geometry || {};
    const coordinates = geometry.coordinates || [];
    if (geometry.type === "LineString") drawGeoLine(ctx, project, coordinates);
    if (geometry.type === "MultiLineString") coordinates.forEach((line) => drawGeoLine(ctx, project, line));
    if (geometry.type === "Polygon") coordinates.forEach((ring) => drawGeoLine(ctx, project, ring));
    if (geometry.type === "MultiPolygon") coordinates.forEach((polygon) => polygon.forEach((ring) => drawGeoLine(ctx, project, ring)));
  }
  ctx.stroke();
  ctx.restore();
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

function drawScale(ctx, width, height, view) {
  const center = { lat: (view.min_lat + view.max_lat) / 2, lon: (view.min_lon + view.max_lon) / 2 };
  const viewWidthKm = haversineKm({ lat: center.lat, lon: view.min_lon }, { lat: center.lat, lon: view.max_lon });
  const steps = [1, 2, 5, 10, 25, 50, 100, 250, 500, 1000, 2000, 5000];
  const scaleKm = steps.find((step) => step >= viewWidthKm / 5) || 5000;
  const barWidth = clamp((scaleKm / Math.max(viewWidthKm, 1)) * width, 48, 180);
  ctx.save();
  ctx.fillStyle = "rgba(223, 231, 235, 0.72)";
  ctx.strokeStyle = "#e6b85a";
  ctx.font = "11px Consolas, monospace";
  ctx.fillText(`LAT ${view.min_lat.toFixed(3)} .. ${view.max_lat.toFixed(3)}`, 20, height - 24);
  ctx.fillText(`LON ${view.min_lon.toFixed(3)} .. ${view.max_lon.toFixed(3)}`, 20, height - 9);
  ctx.fillText(`${scaleKm} km`, width - barWidth - 34, height - 11);
  ctx.beginPath();
  ctx.moveTo(width - barWidth - 34, height - 24);
  ctx.lineTo(width - 34, height - 24);
  ctx.stroke();
  ctx.restore();
}

function drawCircleKm(ctx, project, lat, lon, rangeKm, color, alpha = 0.34) {
  const center = project(lat, lon);
  const edgeLon = lon + rangeKm / (111 * Math.max(Math.cos(lat * Math.PI / 180), 0.12));
  const edge = project(lat, edgeLon);
  const radius = Math.abs(edge.x - center.x);
  if (!Number.isFinite(radius) || radius <= 0) return;
  ctx.save();
  ctx.globalAlpha = alpha;
  ctx.strokeStyle = color;
  ctx.lineWidth = 1.3;
  ctx.beginPath();
  ctx.arc(center.x, center.y, radius, 0, Math.PI * 2);
  ctx.stroke();
  ctx.restore();
}

function drawPath(ctx, project, points, color, options = {}) {
  if (!Array.isArray(points) || points.length < 2) return;
  ctx.save();
  ctx.globalAlpha = options.alpha ?? 0.72;
  ctx.strokeStyle = color;
  ctx.lineWidth = options.width ?? 1.8;
  if (options.dash) ctx.setLineDash(options.dash);
  ctx.beginPath();
  points.forEach((point, index) => {
    const p = project(Number(point.lat), Number(point.lon));
    if (index === 0) ctx.moveTo(p.x, p.y);
    else ctx.lineTo(p.x, p.y);
  });
  ctx.stroke();
  ctx.restore();
}

function drawSymbol(ctx, entity, point, selected) {
  const color = sideColor(entity.side);
  const kind = entity.kind || entity.symbol || "aircraft";
  ctx.save();
  ctx.translate(point.x, point.y);
  ctx.rotate((Number(entity.heading_deg || 0) * Math.PI) / 180);
  ctx.fillStyle = color;
  ctx.strokeStyle = selected ? "#ffe0a3" : "#071013";
  ctx.lineWidth = selected ? 3 : 2;
  if (kind === "ship" || entity.symbol === "surface-ship") {
    ctx.beginPath();
    ctx.moveTo(0, -11);
    ctx.lineTo(13, 0);
    ctx.lineTo(0, 11);
    ctx.lineTo(-13, 0);
    ctx.closePath();
  } else if (kind === "ground" || kind === "radar" || kind === "c2") {
    ctx.beginPath();
    ctx.rect(-10, -10, 20, 20);
  } else if (kind === "missile") {
    ctx.beginPath();
    ctx.moveTo(0, -14);
    ctx.lineTo(6, 9);
    ctx.lineTo(0, 5);
    ctx.lineTo(-6, 9);
    ctx.closePath();
  } else {
    ctx.beginPath();
    ctx.moveTo(0, -14);
    ctx.lineTo(9, 10);
    ctx.lineTo(0, 6);
    ctx.lineTo(-9, 10);
    ctx.closePath();
  }
  ctx.fill();
  ctx.stroke();
  ctx.restore();
}

function drawLabel(ctx, entity, point) {
  ctx.save();
  ctx.font = "11px Consolas, Microsoft YaHei, sans-serif";
  ctx.fillStyle = "rgba(237, 241, 243, 0.9)";
  ctx.strokeStyle = "rgba(5, 8, 10, 0.92)";
  ctx.lineWidth = 3;
  const label = `${entity.id} ${Math.round(Number(entity.alt_m || 0))}m`;
  ctx.strokeText(label, point.x + 12, point.y - 10);
  ctx.fillText(label, point.x + 12, point.y - 10);
  ctx.restore();
}

function drawLinks(ctx, project, entities, scene, layers) {
  const byId = new Map(entities.map((entity) => [entity.id, entity]));
  if (layerVisible(layers, "electromagnetic.comm_links")) {
    ctx.save();
    ctx.globalAlpha = layerOpacity(layers, "electromagnetic.comm_links", 0.68);
    ctx.strokeStyle = "#61d4cf";
    ctx.lineWidth = 1.2;
    ctx.setLineDash([6, 5]);
    for (const link of scene.communications || []) {
      const source = byId.get(link.source_id);
      const target = byId.get(link.target_id);
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
  if (layerVisible(layers, "intelligence.detections")) {
    ctx.save();
    ctx.globalAlpha = layerOpacity(layers, "intelligence.detections", 0.75);
    ctx.strokeStyle = "#e6b85a";
    ctx.lineWidth = 1;
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
}

function drawRanges(ctx, project, entities, scene, layers) {
  const byId = new Map(entities.map((entity) => [entity.id, entity]));
  if (layerVisible(layers, "electromagnetic.radar_ranges")) {
    for (const sensor of scene.sensors || []) {
      const platform = byId.get(sensor.platform_id);
      const range = Number(sensor.range_km || 0);
      if (!platform || range <= 0) continue;
      drawCircleKm(ctx, project, platform.lat, platform.lon, range, sensor.type === "jammer" ? "#ff7373" : "#61d4cf", 0.28);
    }
  }
  if (layerVisible(layers, "dynamic.strike_ranges")) {
    for (const weapon of scene.weapons || []) {
      const platform = byId.get(weapon.platform_id);
      const range = Number(weapon.range_km || 0);
      if (!platform || range <= 0) continue;
      drawCircleKm(ctx, project, platform.lat, platform.lon, range, "#e6b85a", 0.22);
    }
  }
}

function drawEvents(ctx, project, entities, scene, frame, layers, selectedEventId) {
  if (!layerVisible(layers, "replay.event_markers")) return;
  const byId = new Map(entities.map((entity) => [entity.id, entity]));
  const frameEventIds = new Set(frame?.events || []);
  const currentTime = Number(frame?.sim_time ?? scene?.simulation_time?.current ?? 0);
  const events = (scene.events || [])
    .filter((event) => event.platform_id || event.target_id || event.detector_id)
    .filter((event) => {
      if (event.id === selectedEventId) return true;
      if (frameEventIds.has(event.id)) return true;
      if (!frame) return false;
      return Math.abs(Number(event.time || 0) - currentTime) <= 5;
    })
    .slice(0, 48);
  for (const event of events) {
    const entity = byId.get(event.platform_id) || byId.get(event.target_id) || byId.get(event.detector_id);
    if (!entity) continue;
    const p = project(entity.lat, entity.lon);
    const isSelected = event.id === selectedEventId;
    ctx.save();
    ctx.globalAlpha = isSelected ? 0.95 : 0.68;
    ctx.strokeStyle = event.type === "hit" ? "#ff6262" : "#e6b85a";
    ctx.lineWidth = isSelected ? 2.5 : 1.4;
    ctx.beginPath();
    ctx.arc(p.x, p.y, isSelected ? 28 : 20, 0, Math.PI * 2);
    ctx.stroke();
    ctx.restore();
  }
}

function drawScene(ctx, width, height, view, scene, frame, options, redraw) {
  const layers = layerMap(scene);
  const { project, unproject } = makeProject(view, width, height);
  drawFallbackBasemap(ctx, width, height, view);
  if (layerVisible(layers, "base.imagery")) drawRaster(ctx, width, height, view, scene, redraw);
  if (layerVisible(layers, "base.coastline")) {
    drawVectorLayer(ctx, project, scene, "coastline", {
      color: "#d6e6de",
      opacity: layerOpacity(layers, "base.coastline", 0.55),
      width: 0.9,
      simplify: 0.08,
    }, view, redraw);
  }
  if (layerVisible(layers, "base.admin_boundaries")) {
    drawVectorLayer(ctx, project, scene, "pol", {
      color: "#d0d9d6",
      opacity: layerOpacity(layers, "base.admin_boundaries", 0.42),
      width: 0.8,
      dash: [5, 4],
      simplify: 0.12,
    }, view, redraw);
  }

  const entities = mergeFrameEntities(scene, frame);
  if (layerVisible(layers, "dynamic.history_tracks")) {
    for (const track of scene.tracks || []) {
      const points = track.points || track.history || [];
      drawPath(ctx, project, points, sideColor(track.side || "neutral"), { alpha: 0.58, width: 1.5 });
    }
    for (const entity of entities) {
      drawPath(ctx, project, entity.route || [], sideColor(entity.side), { alpha: 0.52, width: 1.5, dash: frame ? null : [5, 4] });
    }
  }

  drawRanges(ctx, project, entities, scene, layers);
  drawLinks(ctx, project, entities, scene, layers);
  drawEvents(ctx, project, entities, scene, frame, layers, options.selectedEventId);

  for (const entity of entities) {
    if (!Number.isFinite(entity.lat) || !Number.isFinite(entity.lon)) continue;
    const p = project(entity.lat, entity.lon);
    if (layerVisible(layers, "dynamic.velocity_vectors")) {
      const angle = (Number(entity.heading_deg || 0) - 90) * Math.PI / 180;
      ctx.save();
      ctx.globalAlpha = layerOpacity(layers, "dynamic.velocity_vectors", 0.8);
      ctx.strokeStyle = sideColor(entity.side);
      ctx.beginPath();
      ctx.moveTo(p.x, p.y);
      ctx.lineTo(p.x + Math.cos(angle) * 28, p.y + Math.sin(angle) * 28);
      ctx.stroke();
      ctx.restore();
    }
    drawSymbol(ctx, entity, p, entity.id === options.selectedId);
    if (layerVisible(layers, "dynamic.target_labels")) drawLabel(ctx, entity, p);
  }

  drawScale(ctx, width, height, view);
  return { entities, project, unproject, view, width, height };
}

function dispatch(canvas, name, detail) {
  canvas.dispatchEvent(new CustomEvent(name, { bubbles: true, detail }));
}

function nearestEntity(drawResult, x, y, maxDistance = 28) {
  let nearest = null;
  let best = Infinity;
  for (const entity of drawResult.entities || []) {
    const p = drawResult.project(entity.lat, entity.lon);
    const distance = Math.hypot(p.x - x, p.y - y);
    if (distance < best) {
      nearest = entity;
      best = distance;
    }
  }
  return nearest && best <= maxDistance ? nearest : null;
}

function queueViewChange(canvas, instance, detail) {
  instance.pendingViewState = detail;
  if (instance.viewChangeFrame) return;
  instance.viewChangeFrame = requestAnimationFrame(() => {
    instance.viewChangeFrame = 0;
    const nextView = instance.pendingViewState;
    instance.pendingViewState = null;
    if (nextView && canvas.isConnected) dispatch(canvas, "afsim-map-view-change", nextView);
  });
}

function attachEvents(canvas, instance) {
  let dragStart = null;
  let moved = false;
  const currentDraw = () => instance.drawResult;
  const geoFromEvent = (event, drawResult = currentDraw()) => {
    if (!drawResult) return null;
    const rect = canvas.getBoundingClientRect();
    const x = event.clientX - rect.left;
    const y = event.clientY - rect.top;
    return { x, y, geo: drawResult.unproject(x, y), rect };
  };

  canvas.addEventListener("wheel", (event) => {
    event.preventDefault();
    const drawResult = currentDraw();
    const point = geoFromEvent(event, drawResult);
    if (!drawResult || !point) return;
    const { x, y, geo } = point;
    const view = drawResult.view;
    const factor = event.deltaY > 0 ? 1.18 : 0.84;
    const nextLatSpan = clamp((view.max_lat - view.min_lat) * factor, 0.002, 180);
    const nextLonSpan = clamp((view.max_lon - view.min_lon) * factor, 0.002, 360);
    const rx = x / Math.max(drawResult.width, 1);
    const ry = y / Math.max(drawResult.height, 1);
    const minLon = geo.lon - rx * nextLonSpan;
    const maxLat = geo.lat + ry * nextLatSpan;
    queueViewChange(canvas, instance, {
      centerLat: clamp(maxLat - nextLatSpan / 2, -89.999, 89.999),
      centerLon: clamp(minLon + nextLonSpan / 2, -179.999, 179.999),
      latSpan: nextLatSpan,
      lonSpan: nextLonSpan,
    });
  }, { passive: false });

  canvas.addEventListener("mousedown", (event) => {
    if (event.button !== 0) return;
    const drawResult = currentDraw();
    const point = geoFromEvent(event, drawResult);
    if (!drawResult || !point) return;
    dragStart = { ...point, view: viewStateFromView(drawResult.view) };
    moved = false;
    canvas.classList.add("dragging");
  });

  canvas.addEventListener("mousemove", (event) => {
    if (!dragStart) return;
    const point = geoFromEvent(event);
    if (!point) return;
    moved = moved || Math.hypot(point.x - dragStart.x, point.y - dragStart.y) > 4;
  });

  canvas.addEventListener("mouseup", (event) => {
    if (!dragStart) return;
    canvas.classList.remove("dragging");
    const end = geoFromEvent(event);
    const drawResult = currentDraw();
    if (!end || !drawResult) {
      dragStart = null;
      return;
    }
    if (moved) {
      const dx = end.x - dragStart.x;
      const dy = end.y - dragStart.y;
      queueViewChange(canvas, instance, {
        centerLat: clamp(dragStart.view.centerLat + (dy / Math.max(drawResult.height, 1)) * dragStart.view.latSpan, -89.999, 89.999),
        centerLon: clamp(dragStart.view.centerLon - (dx / Math.max(drawResult.width, 1)) * dragStart.view.lonSpan, -179.999, 179.999),
        latSpan: dragStart.view.latSpan,
        lonSpan: dragStart.view.lonSpan,
      });
    } else {
      const nearest = nearestEntity(drawResult, end.x, end.y);
      if (nearest) dispatch(canvas, "afsim-map-select", { id: nearest.id });
    }
    dragStart = null;
  });

  canvas.addEventListener("mouseleave", () => {
    dragStart = null;
    canvas.classList.remove("dragging");
  });
}

function createMapCanvas(container) {
  container.innerHTML = `
    <div class="map-stage">
      <canvas class="afsim-map-canvas"></canvas>
    </div>
  `;
  return container.querySelector("canvas");
}

function ensureMapInstance(container) {
  const existingCanvas = container.querySelector(".afsim-map-canvas");
  const existing = operationalMapInstances.get(container);
  if (existing && existing.canvas === existingCanvas) return existing;

  const canvas = createMapCanvas(container);
  const instance = {
    canvas,
    drawResult: null,
    renderToken: 0,
    redrawFrame: 0,
    viewChangeFrame: 0,
    pendingViewState: null,
  };
  operationalMapInstances.set(container, instance);
  attachEvents(canvas, instance);
  return instance;
}

function scheduleMapRedraw(container, scene, options, token) {
  const instance = operationalMapInstances.get(container);
  if (!instance || instance.renderToken !== token || instance.redrawFrame) return;
  instance.redrawFrame = requestAnimationFrame(() => {
    const latest = operationalMapInstances.get(container);
    if (latest) latest.redrawFrame = 0;
    if (!latest || latest.renderToken !== token || !latest.canvas.isConnected) return;
    renderOperationalMap(container, scene, options);
  });
}

export function renderOperationalMap(container, scene, options = {}) {
  if (!scene) {
    disposeOperationalMap(container);
    container.innerHTML = `<div class="map-stage"><div class="empty-row">无可显示态势。</div></div>`;
    return;
  }
  const instance = ensureMapInstance(container);
  if (instance.redrawFrame) {
    cancelAnimationFrame(instance.redrawFrame);
    instance.redrawFrame = 0;
  }
  const canvas = instance.canvas;
  const { ctx, width, height } = setupCanvas(canvas);
  const frame = options.frame || null;
  const entities = mergeFrameEntities(scene, frame);
  const view = viewFromState(boundsFor(scene, entities), options.viewState);
  const token = instance.renderToken + 1;
  instance.renderToken = token;
  const redraw = () => scheduleMapRedraw(container, scene, options, token);
  const drawResult = drawScene(ctx, width, height, view, scene, frame, options, redraw);
  instance.drawResult = drawResult;
}
