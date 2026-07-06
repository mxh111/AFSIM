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

function disposeGlobe() {
  if (globeState.animationId) cancelAnimationFrame(globeState.animationId);
  if (globeState.renderer) globeState.renderer.dispose();
  globeState.geometries.forEach((geometry) => geometry.dispose());
  globeState.materials.forEach((material) => material.dispose());
  globeState = { animationId: null, renderer: null, geometries: [], materials: [] };
}

function entitiesFor(scene, frame) {
  if (frame?.entities?.length) return frame.entities;
  return (scene.platforms || []).map((item) => ({
    id: item.id,
    side: item.side,
    category: item.category,
    type: item.type,
    lat: item.lat,
    lon: item.lon,
    alt_m: item.alt_m || 0,
    route: item.route || [],
  }));
}

function boundsFor(scene, entities) {
  if (scene.bounds) return scene.bounds;
  const lats = entities.map((item) => Number(item.lat)).filter(Number.isFinite);
  const lons = entities.map((item) => Number(item.lon)).filter(Number.isFinite);
  if (!lats.length || !lons.length) return null;
  return {
    min_lat: Math.min(...lats),
    max_lat: Math.max(...lats),
    min_lon: Math.min(...lons),
    max_lon: Math.max(...lons),
  };
}

function paddedBounds(bounds) {
  const latPad = Math.max(0.05, (bounds.max_lat - bounds.min_lat) * 0.16);
  const lonPad = Math.max(0.05, (bounds.max_lon - bounds.min_lon) * 0.16);
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

function drawDiamond(ctx, x, y, size, color) {
  ctx.beginPath();
  ctx.moveTo(x, y - size);
  ctx.lineTo(x + size, y);
  ctx.lineTo(x, y + size);
  ctx.lineTo(x - size, y);
  ctx.closePath();
  ctx.fillStyle = color;
  ctx.fill();
  ctx.strokeStyle = "#081014";
  ctx.lineWidth = 2;
  ctx.stroke();
}

function drawUnit(ctx, entity, x, y) {
  const color = sideColor(entity.side);
  ctx.save();
  if (entity.side === "red") {
    drawDiamond(ctx, x, y, 8, color);
  } else if (entity.category?.includes("radar") || entity.category?.includes("c2")) {
    ctx.fillStyle = color;
    ctx.fillRect(x - 8, y - 8, 16, 16);
    ctx.strokeStyle = "#081014";
    ctx.lineWidth = 2;
    ctx.strokeRect(x - 8, y - 8, 16, 16);
  } else {
    ctx.beginPath();
    ctx.arc(x, y, 8, 0, Math.PI * 2);
    ctx.fillStyle = color;
    ctx.fill();
    ctx.strokeStyle = "#081014";
    ctx.lineWidth = 2;
    ctx.stroke();
  }
  ctx.font = "12px 'Microsoft YaHei', 'Segoe UI', sans-serif";
  ctx.textBaseline = "bottom";
  ctx.lineWidth = 4;
  ctx.strokeStyle = "#071013";
  ctx.strokeText(entity.id, x + 12, y - 6);
  ctx.fillStyle = "#e8eef2";
  ctx.fillText(entity.id, x + 12, y - 6);
  ctx.textBaseline = "top";
  ctx.fillStyle = "#9aa7b2";
  ctx.fillText(`${entity.side || "neutral"} / ${entity.category || entity.type || "-"}`, x + 12, y + 6);
  ctx.restore();
}

function draw2D(canvas, scene, frame) {
  const entities = entitiesFor(scene, frame);
  const bounds = boundsFor(scene, entities);
  if (!bounds) return;
  const { ctx, width, height } = setupCanvas(canvas);
  const view = paddedBounds(bounds);
  const lonSpan = Math.max(0.01, view.max_lon - view.min_lon);
  const latSpan = Math.max(0.01, view.max_lat - view.min_lat);
  const project = (lat, lon) => ({
    x: ((lon - view.min_lon) / lonSpan) * width,
    y: height - ((lat - view.min_lat) / latSpan) * height,
  });

  const gradient = ctx.createLinearGradient(0, 0, 0, height);
  gradient.addColorStop(0, "#0c171a");
  gradient.addColorStop(0.55, "#101414");
  gradient.addColorStop(1, "#0b1012");
  ctx.fillStyle = gradient;
  ctx.fillRect(0, 0, width, height);

  ctx.strokeStyle = "rgba(98, 146, 139, 0.18)";
  ctx.lineWidth = 1;
  for (let i = 0; i <= 10; i += 1) {
    const x = (width / 10) * i;
    const y = (height / 10) * i;
    ctx.beginPath();
    ctx.moveTo(x, 0);
    ctx.lineTo(x, height);
    ctx.moveTo(0, y);
    ctx.lineTo(width, y);
    ctx.stroke();
  }

  ctx.strokeStyle = "rgba(240, 189, 95, 0.35)";
  ctx.strokeRect(18, 18, width - 36, height - 36);

  ctx.font = "11px Consolas, monospace";
  ctx.fillStyle = "#87979d";
  ctx.fillText(`LAT ${view.min_lat.toFixed(3)} .. ${view.max_lat.toFixed(3)}`, 26, height - 24);
  ctx.fillText(`LON ${view.min_lon.toFixed(3)} .. ${view.max_lon.toFixed(3)}`, 26, height - 10);
  ctx.fillText(`AFSIM STYLE 2D / ${entities.length} ENTITIES`, 26, 34);

  for (const entity of entities) {
    const route = entity.route || [];
    if (route.length > 1) {
      ctx.beginPath();
      route.forEach((point, index) => {
        const p = project(Number(point.lat), Number(point.lon));
        if (index === 0) ctx.moveTo(p.x, p.y);
        else ctx.lineTo(p.x, p.y);
      });
      ctx.strokeStyle = sideColor(entity.side);
      ctx.globalAlpha = 0.65;
      ctx.lineWidth = 2;
      ctx.stroke();
      ctx.globalAlpha = 1;
    }
  }

  for (const entity of entities) {
    const p = project(Number(entity.lat), Number(entity.lon));
    if (entity.category?.includes("radar") || entity.category?.includes("awacs")) {
      ctx.beginPath();
      ctx.arc(p.x, p.y, entity.category.includes("awacs") ? 90 : 64, 0, Math.PI * 2);
      ctx.strokeStyle = sideColor(entity.side);
      ctx.globalAlpha = 0.22;
      ctx.lineWidth = 1.5;
      ctx.stroke();
      ctx.globalAlpha = 1;
    }
    drawUnit(ctx, entity, p.x, p.y);
  }
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

function trackMaterial(color, opacity = 1) {
  const material = new THREE.LineBasicMaterial({ color, transparent: opacity < 1, opacity });
  globeState.materials.push(material);
  return material;
}

function render3D(container, scene, frame) {
  const entities = entitiesFor(scene, frame);
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
  camera.position.set(0, 0, 3.2);

  const group = new THREE.Group();
  threeScene.add(group);

  const earthGeometry = new THREE.SphereGeometry(1, 64, 32);
  const earthMaterial = new THREE.MeshPhongMaterial({
    color: 0x193f4a,
    emissive: 0x061316,
    shininess: 12,
    specular: 0x4a6870,
  });
  globeState.geometries.push(earthGeometry);
  globeState.materials.push(earthMaterial);
  const earth = new THREE.Mesh(earthGeometry, earthMaterial);
  group.add(earth);

  const wireGeometry = new THREE.SphereGeometry(1.004, 32, 16);
  const wireMaterial = new THREE.MeshBasicMaterial({ color: 0x76a7a0, wireframe: true, transparent: true, opacity: 0.16 });
  globeState.geometries.push(wireGeometry);
  globeState.materials.push(wireMaterial);
  group.add(new THREE.Mesh(wireGeometry, wireMaterial));

  const light = new THREE.DirectionalLight(0xffffff, 1.7);
  light.position.set(4, 3, 5);
  threeScene.add(light);
  threeScene.add(new THREE.AmbientLight(0x7d9298, 0.6));

  for (const entity of entities) {
    const color = new THREE.Color(sideColor(entity.side));
    const markerGeometry = new THREE.SphereGeometry(0.018, 16, 8);
    const markerMaterial = new THREE.MeshBasicMaterial({ color });
    globeState.geometries.push(markerGeometry);
    globeState.materials.push(markerMaterial);
    const marker = new THREE.Mesh(markerGeometry, markerMaterial);
    marker.position.copy(latLonToVector(Number(entity.lat), Number(entity.lon), 1.035 + Math.min(Number(entity.alt_m || 0) / 2000000, 0.08)));
    group.add(marker);

    const route = entity.route || [];
    if (route.length > 1) {
      const routeGeometry = new THREE.BufferGeometry().setFromPoints(
        route.map((point) => latLonToVector(Number(point.lat), Number(point.lon), 1.025 + Math.min(Number(point.alt_m || 0) / 2000000, 0.08))),
      );
      globeState.geometries.push(routeGeometry);
      group.add(new THREE.Line(routeGeometry, trackMaterial(color, 0.72)));
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
    group.rotation.y += 0.0015;
    renderer.render(threeScene, camera);
    globeState.animationId = requestAnimationFrame(animate);
  };
  animate();
}

export function renderOperationalMap(container, scene, { mode = "split", frame = null } = {}) {
  disposeGlobe();
  const entities = entitiesFor(scene, frame);
  const frameText = frame ? `T+${Number(frame.sim_time || 0).toFixed(1)}s / ${frame.source || "stream"}` : "静态解析态势";
  container.innerHTML = `
    <div class="map-stage map-mode-${mode}">
      <div class="map-pane map-pane-2d">
        <canvas class="afsim-map-canvas" data-map-2d></canvas>
      </div>
      <div class="map-pane map-pane-3d" data-map-3d></div>
      <div class="map-hud">
        <strong>${mode === "3d" ? "3D 地球" : mode === "2d" ? "2D 战术图" : "2D / 3D 同屏"}</strong>
        <span>${frameText}</span>
        <span>${entities.length} entities</span>
      </div>
    </div>
  `;
  const canvas = container.querySelector("[data-map-2d]");
  const globe = container.querySelector("[data-map-3d]");
  if (mode !== "3d") draw2D(canvas, scene, frame);
  if (mode !== "2d") render3D(globe, scene, frame);
}

export function disposeOperationalMap() {
  disposeGlobe();
}
