import * as THREE from "/static/vendor/three/three.module.js";

let globeState = {
  animationId: null,
  renderer: null,
  geometries: [],
  materials: [],
};

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
  globeState = { animationId: null, renderer: null, geometries: [], materials: [] };
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
  return platforms.map((platform) => {
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
  if (scene.bounds) return scene.bounds;
  const lats = entities.map((item) => Number(item.lat ?? item.position?.lat)).filter(Number.isFinite);
  const lons = entities.map((item) => Number(item.lon ?? item.position?.lon)).filter(Number.isFinite);
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

function drawGrid(ctx, width, height, view, layers) {
  if (layerVisible(layers, "base.dark_basemap")) {
    ctx.fillStyle = "#071012";
    ctx.fillRect(0, 0, width, height);
    ctx.fillStyle = "#0d171a";
    ctx.fillRect(0, height * 0.58, width, height * 0.42);
  }

  if (layerVisible(layers, "base.coastline")) {
    ctx.save();
    ctx.globalAlpha = layerOpacity(layers, "base.coastline", 0.55);
    ctx.strokeStyle = "#5d756f";
    ctx.lineWidth = 1;
    const coastlines = [
      [[0.12, 0.2], [0.22, 0.28], [0.28, 0.42], [0.2, 0.58], [0.31, 0.77]],
      [[0.68, 0.18], [0.76, 0.34], [0.72, 0.51], [0.83, 0.67], [0.78, 0.86]],
      [[0.42, 0.12], [0.5, 0.22], [0.54, 0.38], [0.48, 0.55], [0.58, 0.76]],
    ];
    for (const line of coastlines) {
      ctx.beginPath();
      line.forEach(([x, y], index) => {
        const px = x * width;
        const py = y * height;
        if (index === 0) ctx.moveTo(px, py);
        else ctx.lineTo(px, py);
      });
      ctx.stroke();
    }
    ctx.restore();
  }

  if (layerVisible(layers, "base.latlon_grid")) {
    ctx.save();
    ctx.globalAlpha = layerOpacity(layers, "base.latlon_grid", 0.9);
    ctx.strokeStyle = "rgba(109, 160, 151, 0.22)";
    ctx.lineWidth = 1;
    for (let i = 0; i <= 12; i += 1) {
      const x = (width / 12) * i;
      const y = (height / 12) * i;
      ctx.beginPath();
      ctx.moveTo(x, 0);
      ctx.lineTo(x, height);
      ctx.moveTo(0, y);
      ctx.lineTo(width, y);
      ctx.stroke();
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
  ctx.fillText(`LAT ${view.min_lat.toFixed(3)} .. ${view.max_lat.toFixed(3)}`, 26, height - 25);
  ctx.fillText(`LON ${view.min_lon.toFixed(3)} .. ${view.max_lon.toFixed(3)}`, 26, height - 10);
  ctx.fillText("SCALE 100 km", width - 116, height - 13);
  ctx.strokeStyle = "#e7b85b";
  ctx.beginPath();
  ctx.moveTo(width - 116, height - 26);
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
  ctx.restore();
}

function drawSymbol(ctx, entity, x, y, selected) {
  const color = sideColor(entity.side);
  const kind = entity.kind || "aircraft";
  ctx.save();
  ctx.translate(x, y);
  ctx.rotate(((Number(entity.heading_deg) || 0) * Math.PI) / 180);
  ctx.fillStyle = color;
  ctx.strokeStyle = "#071013";
  ctx.lineWidth = selected ? 3 : 2;
  if (kind === "aircraft" || kind === "jammer") {
    ctx.beginPath();
    ctx.moveTo(0, -12);
    ctx.lineTo(8, 10);
    ctx.lineTo(0, 6);
    ctx.lineTo(-8, 10);
    ctx.closePath();
    ctx.fill();
    ctx.stroke();
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
  } else if (kind === "radar" || kind === "c2") {
    ctx.fillRect(-9, -9, 18, 18);
    ctx.strokeRect(-9, -9, 18, 18);
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
  ctx.fillText(`${entity.side || "neutral"} / ${entity.category || entity.kind || "-"}`, x + 13, y + 5);
  ctx.restore();
}

function draw2D(canvas, scene, frame, options) {
  const layers = layerMap(options.layers || scene.layers || []);
  const entities = filteredEntities(scene, frame, options.filters || {});
  const bounds = boundsFor(scene, entities);
  if (!bounds) return { entities, project: () => ({ x: 0, y: 0 }) };
  const { ctx, width, height } = setupCanvas(canvas);
  const view = paddedBounds(bounds, options.domain || "earth");
  const lonSpan = Math.max(0.01, view.max_lon - view.min_lon);
  const latSpan = Math.max(0.01, view.max_lat - view.min_lat);
  const project = (lat, lon) => ({
    x: ((lon - view.min_lon) / lonSpan) * width,
    y: height - ((lat - view.min_lat) / latSpan) * height,
  });
  const byId = new Map(entities.map((entity) => [entity.id, entity]));

  drawGrid(ctx, width, height, view, layers);

  if (layerVisible(layers, "dynamic.history_tracks")) {
    for (const entity of entities) {
      drawPath(ctx, project, entity.route || [], sideColor(entity.side), {
        alpha: layerOpacity(layers, "dynamic.history_tracks", 0.65),
        width: 2,
      });
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

  if (layerVisible(layers, "electromagnetic.comm_links")) {
    ctx.save();
    ctx.globalAlpha = layerOpacity(layers, "electromagnetic.comm_links", 0.68);
    ctx.strokeStyle = "#61d4cf";
    ctx.setLineDash([3, 5]);
    for (const comm of scene.communications || []) {
      const source = byId.get(comm.source_id);
      const target = byId.get(comm.target_id);
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
    const activeEvents = (scene.events || []).filter((event) => event.platform_id && byId.has(event.platform_id)).slice(0, 24);
    for (const event of activeEvents) {
      const entity = byId.get(event.platform_id);
      const p = project(entity.lat, entity.lon);
      ctx.save();
      ctx.globalAlpha = layerOpacity(layers, "replay.event_markers", 0.7);
      ctx.strokeStyle = event.type === "hit" ? "#ff6262" : "#e7b85b";
      ctx.lineWidth = 1.4;
      ctx.beginPath();
      ctx.arc(p.x, p.y, 22, 0, Math.PI * 2);
      ctx.stroke();
      ctx.restore();
    }
  }

  return { entities, project };
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

  const light = new THREE.DirectionalLight(0xffffff, 1.7);
  light.position.set(4, 3, 5);
  threeScene.add(light);
  threeScene.add(new THREE.AmbientLight(0x7d9298, 0.6));

  for (const entity of entities) {
    const color = new THREE.Color(sideColor(entity.side));
    const markerGeometry = new THREE.SphereGeometry(entity.id === options.selectedId ? 0.028 : 0.019, 16, 8);
    const markerMaterial = new THREE.MeshBasicMaterial({ color });
    globeState.geometries.push(markerGeometry);
    globeState.materials.push(markerMaterial);
    const marker = new THREE.Mesh(markerGeometry, markerMaterial);
    const altitudeScale = options.domain === "space" ? 900000 : 2000000;
    marker.position.copy(latLonToVector(Number(entity.lat), Number(entity.lon), 1.035 + Math.min(Number(entity.alt_m || 0) / altitudeScale, 0.16)));
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

  const animate = () => {
    group.rotation.y += options.domain === "space" ? 0.0022 : 0.0012;
    renderer.render(threeScene, camera);
    globeState.animationId = requestAnimationFrame(animate);
  };
  animate();
}

function attachCanvasEvents(canvas, drawResult) {
  canvas.addEventListener("click", (event) => {
    const rect = canvas.getBoundingClientRect();
    const x = event.clientX - rect.left;
    const y = event.clientY - rect.top;
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
    if (nearest && nearestDistance < 28) {
      canvas.dispatchEvent(new CustomEvent("afsim-map-select", { bubbles: true, detail: { id: nearest.id } }));
    }
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
  container.innerHTML = `
    <div class="map-stage map-mode-${mode}">
      <div class="map-pane map-pane-2d">
        <canvas class="afsim-map-canvas" data-map-2d></canvas>
      </div>
      <div class="map-pane map-pane-3d" data-map-3d></div>
      <div class="map-hud">
        <strong>${mode === "3d" ? "3D 地球" : mode === "2d" ? "2D 战术图" : "2D / 3D 同屏"}</strong>
        <span>${domainText}</span>
        <span>${frameText}</span>
        <span>${entities.length} targets</span>
      </div>
    </div>
  `;
  const canvas = container.querySelector("[data-map-2d]");
  const globe = container.querySelector("[data-map-3d]");
  if (mode !== "3d") {
    const drawResult = draw2D(canvas, scene, frame, { ...options, domain });
    attachCanvasEvents(canvas, drawResult);
  }
  if (mode !== "2d") render3D(globe, scene, frame, { ...options, domain });
}

export function disposeOperationalMap() {
  disposeGlobe();
}
