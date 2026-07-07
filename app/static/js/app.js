import { disposeOperationalMap, renderOperationalMap, sideColor } from "./map_renderer.js";

const DEFAULT_TEMPLATES = [
  { id: "blue_fighter", label: "蓝方战斗机", type_name: "WEB_AIRCRAFT", side: "blue", category: "fighter", icon: "F-22", altitude_m: 9000, speed_kts: 420, heading_deg: 90 },
  { id: "red_fighter", label: "红方战斗机", type_name: "WEB_AIRCRAFT", side: "red", category: "fighter", icon: "SU-27", altitude_m: 8500, speed_kts: 430, heading_deg: 270 },
  { id: "awacs", label: "预警机", type_name: "WEB_AWACS", side: "blue", category: "awacs", icon: "E-3", altitude_m: 9500, speed_kts: 350, heading_deg: 90 },
  { id: "ground_radar", label: "地面雷达站", type_name: "WEB_GROUND_RADAR", side: "blue", category: "radar", icon: "radar", altitude_m: 0, speed_kts: 0, heading_deg: 0 },
  { id: "sam_site", label: "防空阵地", type_name: "WEB_SAM_SITE", side: "blue", category: "sam", icon: "sam", altitude_m: 0, speed_kts: 0, heading_deg: 0 },
  { id: "surface_ship", label: "水面舰艇", type_name: "WEB_SURFACE_SHIP", side: "blue", category: "ship", icon: "ship", altitude_m: 0, speed_kts: 0, heading_deg: 0 },
];

const state = {
  templates: DEFAULT_TEMPLATES,
  editorPlatforms: [],
  designs: [],
  workbench: null,
  activeScenarioId: null,
  activeDemo: null,
  mapMode: "split",
  domain: "earth",
  selectedId: null,
  selectedIds: new Set(),
  focusLayerId: null,
  mapView: null,
  interactionMode: "select",
  measurePoints: [],
  pendingPatches: [],
  currentFrame: null,
  liveSocket: null,
  liveActive: false,
  lastRunId: null,
  playSpeed: 1,
};

const els = {
  systemStatus: document.getElementById("systemStatus"),
  missionStatus: document.getElementById("missionStatus"),
  parserStatus: document.getElementById("parserStatus"),
  llmStatus: document.getElementById("llmStatus"),
  domainSelect: document.getElementById("domainSelect"),
  layerTree: document.getElementById("layerTree"),
  persistLayers: document.getElementById("persistLayers"),
  filterBlue: document.getElementById("filterBlue"),
  filterRed: document.getElementById("filterRed"),
  filterNeutral: document.getElementById("filterNeutral"),
  filterAir: document.getElementById("filterAir"),
  filterGround: document.getElementById("filterGround"),
  filterSea: document.getElementById("filterSea"),
  platformTemplate: document.getElementById("platformTemplate"),
  addPlatform: document.getElementById("addPlatform"),
  platformEditor: document.getElementById("platformEditor"),
  saveScenario: document.getElementById("saveScenario"),
  saveDraft: document.getElementById("saveDraft"),
  designName: document.getElementById("designName"),
  designDescription: document.getElementById("designDescription"),
  endTime: document.getElementById("endTime"),
  generatedScenarios: document.getElementById("generatedScenarios"),
  runGenerated: document.getElementById("runGenerated"),
  afsimDemo: document.getElementById("afsimDemo"),
  previewDemo: document.getElementById("previewDemo"),
  runDemo: document.getElementById("runDemo"),
  draftList: document.getElementById("draftList"),
  activeScenarioTitle: document.getElementById("activeScenarioTitle"),
  activeScenarioLabel: document.getElementById("activeScenarioLabel"),
  view2d: document.getElementById("view2d"),
  view25d: document.getElementById("view25d"),
  view3d: document.getElementById("view3d"),
  viewSplit: document.getElementById("viewSplit"),
  liveScene: document.getElementById("liveScene"),
  followTarget: document.getElementById("followTarget"),
  measureTool: document.getElementById("measureTool"),
  boxSelect: document.getElementById("boxSelect"),
  editTarget: document.getElementById("editTarget"),
  fitScene: document.getElementById("fitScene"),
  sceneView: document.getElementById("sceneView"),
  simStart: document.getElementById("simStart"),
  simPause: document.getElementById("simPause"),
  simResume: document.getElementById("simResume"),
  simSlower: document.getElementById("simSlower"),
  simFaster: document.getElementById("simFaster"),
  simStep: document.getElementById("simStep"),
  simReset: document.getElementById("simReset"),
  simStop: document.getElementById("simStop"),
  timelineRange: document.getElementById("timelineRange"),
  timeLabel: document.getElementById("timeLabel"),
  playSpeedLabel: document.getElementById("playSpeedLabel"),
  fpsLabel: document.getElementById("fpsLabel"),
  targetCountLabel: document.getElementById("targetCountLabel"),
  eventCountLabel: document.getElementById("eventCountLabel"),
  eventLog: document.getElementById("eventLog"),
  targetSummary: document.getElementById("targetSummary"),
  targetList: document.getElementById("targetList"),
  targetProperties: document.getElementById("targetProperties"),
  patchSummary: document.getElementById("patchSummary"),
  targetEditPanel: document.getElementById("targetEditPanel"),
  editLat: document.getElementById("editLat"),
  editLon: document.getElementById("editLon"),
  editAlt: document.getElementById("editAlt"),
  editHeading: document.getElementById("editHeading"),
  previewEdit: document.getElementById("previewEdit"),
  commitEdit: document.getElementById("commitEdit"),
  undoEdit: document.getElementById("undoEdit"),
  clearPatches: document.getElementById("clearPatches"),
  patchPreview: document.getElementById("patchPreview"),
  loadReplay: document.getElementById("loadReplay"),
  eventList: document.getElementById("eventList"),
  chainList: document.getElementById("chainList"),
  analysisPrompt: document.getElementById("analysisPrompt"),
  analyzeAfsim: document.getElementById("analyzeAfsim"),
  analysisOutput: document.getElementById("analysisOutput"),
  agentObjective: document.getElementById("agentObjective"),
  agentStep: document.getElementById("agentStep"),
  agentReset: document.getElementById("agentReset"),
  agentOutput: document.getElementById("agentOutput"),
};

window.__AFSIM_LLM_BOOTED = false;

window.addEventListener("error", (event) => {
  if (els.systemStatus) els.systemStatus.textContent = `前端错误：${event.message}`;
});

window.addEventListener("unhandledrejection", (event) => {
  if (els.systemStatus) els.systemStatus.textContent = `前端异步错误：${event.reason?.message || event.reason}`;
});

function assertRequiredElements() {
  const missing = Object.entries(els)
    .filter(([, element]) => !element)
    .map(([key]) => key);
  if (missing.length) throw new Error(`页面结构缺少元素：${missing.join(", ")}`);
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    cache: "no-store",
    ...options,
  });
  if (!response.ok) {
    const text = await response.text();
    throw new Error(`${response.status} ${response.statusText}: ${text.slice(0, 300)}`);
  }
  if (response.status === 204) return null;
  return response.json();
}

function setStatus(el, ok, text) {
  el.textContent = text;
  el.classList.toggle("ok", Boolean(ok));
  el.classList.toggle("bad", !ok);
}

function readNumber(value, fallback = 0) {
  if (value === "" || value == null) return fallback;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function templateById(id) {
  return state.templates.find((item) => item.id === id) || state.templates[0];
}

function makePlatformFromTemplate(template, index) {
  const offset = index * 0.18;
  const isRed = template.side === "red";
  const lat = 1.0 + offset;
  const lon = isRed ? 1.8 + offset : 1.0 + offset;
  return {
    name: `${template.id}_${index + 1}`,
    type_name: template.type_name,
    side: template.side,
    category: template.category,
    icon: template.icon,
    lat,
    lon,
    altitude_m: template.altitude_m,
    speed_kts: template.speed_kts,
    heading_deg: template.heading_deg,
    routeLat: lat,
    routeLon: isRed ? lon - 0.35 : lon + 0.35,
  };
}

function renderTemplateOptions() {
  els.platformTemplate.innerHTML = state.templates
    .map((item) => `<option value="${escapeHtml(item.id)}">${escapeHtml(item.label)} / ${escapeHtml(item.type_name)}</option>`)
    .join("");
}

function renderPlatformEditor() {
  if (!state.editorPlatforms.length) {
    els.platformEditor.innerHTML = `<div class="list-item"><small>从模板添加平台后，可在这里编辑坐标、速度、航向和航路点。</small></div>`;
    return;
  }
  const typeOptions = [...new Set(state.templates.map((item) => item.type_name))]
    .map((type) => `<option value="${escapeHtml(type)}">${escapeHtml(type)}</option>`)
    .join("");
  els.platformEditor.innerHTML = state.editorPlatforms
    .map((platform, index) => `
      <div class="platform-row" data-platform-index="${index}">
        <div class="platform-title">
          <strong>${escapeHtml(platform.name || `Platform_${index + 1}`)}</strong>
          <button class="mini-button danger-button" type="button" data-remove-platform="${index}">删除</button>
        </div>
        <div class="platform-grid">
          <label>名称<input data-field="name" value="${escapeHtml(platform.name)}" /></label>
          <label>阵营
            <select data-field="side">
              ${["blue", "red", "green", "neutral"].map((side) => `<option value="${side}" ${platform.side === side ? "selected" : ""}>${side}</option>`).join("")}
            </select>
          </label>
          <label>AFSIM 类型
            <select data-field="type_name">
              ${typeOptions.replace(`value="${escapeHtml(platform.type_name)}"`, `value="${escapeHtml(platform.type_name)}" selected`)}
            </select>
          </label>
          <label>分类<input data-field="category" value="${escapeHtml(platform.category)}" /></label>
          <label>图标<input data-field="icon" value="${escapeHtml(platform.icon)}" /></label>
          <label>航向 度<input data-field="heading_deg" type="number" min="0" max="360" value="${escapeHtml(platform.heading_deg)}" /></label>
          <label>起点纬度<input data-field="lat" type="number" step="0.0001" min="-90" max="90" value="${escapeHtml(platform.lat)}" /></label>
          <label>起点经度<input data-field="lon" type="number" step="0.0001" min="-180" max="180" value="${escapeHtml(platform.lon)}" /></label>
          <label>高度 米<input data-field="altitude_m" type="number" min="0" value="${escapeHtml(platform.altitude_m)}" /></label>
          <label>速度 kts<input data-field="speed_kts" type="number" min="0" value="${escapeHtml(platform.speed_kts)}" /></label>
          <label>终点纬度<input data-field="routeLat" type="number" step="0.0001" min="-90" max="90" value="${escapeHtml(platform.routeLat ?? "")}" /></label>
          <label>终点经度<input data-field="routeLon" type="number" step="0.0001" min="-180" max="180" value="${escapeHtml(platform.routeLon ?? "")}" /></label>
        </div>
      </div>
    `)
    .join("");
}

function collectDesign() {
  return {
    name: els.designName.value.trim() || "web_scenario",
    description: els.designDescription.value.trim(),
    end_time_seconds: Math.max(1, Math.min(86400, readNumber(els.endTime.value, 600))),
    platforms: state.editorPlatforms.map((platform, index) => {
      const lat = readNumber(platform.lat);
      const lon = readNumber(platform.lon);
      const altitude = readNumber(platform.altitude_m);
      const speed = readNumber(platform.speed_kts);
      const route = [];
      if (platform.routeLat !== "" && platform.routeLat != null && platform.routeLon !== "" && platform.routeLon != null) {
        route.push({
          lat: readNumber(platform.routeLat, lat),
          lon: readNumber(platform.routeLon, lon),
          altitude_m: altitude,
          speed_kts: speed,
        });
      }
      return {
        name: platform.name || `Platform_${index + 1}`,
        type_name: platform.type_name || "WEB_AIRCRAFT",
        side: platform.side || "neutral",
        category: platform.category || "aircraft",
        icon: platform.icon || "F-22",
        lat,
        lon,
        altitude_m: altitude,
        speed_kts: speed,
        heading_deg: readNumber(platform.heading_deg),
        route,
      };
    }),
  };
}

function filters() {
  const sides = new Set();
  if (els.filterBlue.checked) sides.add("blue");
  if (els.filterRed.checked) sides.add("red");
  if (els.filterNeutral.checked) {
    sides.add("neutral");
    sides.add("green");
  }
  const kinds = new Set();
  if (els.filterAir.checked) {
    kinds.add("air");
    kinds.add("missile");
    kinds.add("space");
  }
  if (els.filterGround.checked) kinds.add("ground");
  if (els.filterSea.checked) kinds.add("sea");
  return { sides, kinds };
}

function replayFrames() {
  return state.workbench?.replay?.frames || [];
}

function nearestReplayFrame(timeValue) {
  const frames = replayFrames();
  if (!frames.length) return null;
  const time = Number(timeValue);
  let nearest = frames[0];
  let best = Math.abs(Number(nearest.sim_time || 0) - time);
  for (const frame of frames) {
    const distance = Math.abs(Number(frame.sim_time || 0) - time);
    if (distance < best) {
      nearest = frame;
      best = distance;
    }
  }
  return nearest;
}

function bearingDeg(a, b) {
  const lat1 = Number(a.lat) * Math.PI / 180;
  const lat2 = Number(b.lat) * Math.PI / 180;
  const dLon = (Number(b.lon) - Number(a.lon)) * Math.PI / 180;
  const y = Math.sin(dLon) * Math.cos(lat2);
  const x = Math.cos(lat1) * Math.sin(lat2) - Math.sin(lat1) * Math.cos(lat2) * Math.cos(dLon);
  return ((Math.atan2(y, x) * 180 / Math.PI) + 360) % 360;
}

function haversineKm(a, b) {
  const lat1 = Number(a.lat) * Math.PI / 180;
  const lat2 = Number(b.lat) * Math.PI / 180;
  const dLat = lat2 - lat1;
  const dLon = (Number(b.lon) - Number(a.lon)) * Math.PI / 180;
  const value = Math.sin(dLat / 2) ** 2 + Math.cos(lat1) * Math.cos(lat2) * Math.sin(dLon / 2) ** 2;
  return 6371 * 2 * Math.atan2(Math.sqrt(value), Math.sqrt(Math.max(0, 1 - value)));
}

function blendEntity(before, after, ratio) {
  if (!before || !after) return before || after;
  const lat = Number(before.lat) + (Number(after.lat) - Number(before.lat)) * ratio;
  const lon = Number(before.lon) + (Number(after.lon) - Number(before.lon)) * ratio;
  const alt = Number(before.alt_m || 0) + (Number(after.alt_m || 0) - Number(before.alt_m || 0)) * ratio;
  const heading = Math.abs(Number(after.lat) - Number(before.lat)) > 1e-8 || Math.abs(Number(after.lon) - Number(before.lon)) > 1e-8
    ? bearingDeg(before, after)
    : Number(before.heading_deg ?? after.heading_deg ?? 0);
  return {
    ...before,
    ...after,
    lat,
    lon,
    alt_m: alt,
    heading_deg: heading,
    route: [...(before.route || []), { lat, lon, alt_m: alt }].slice(-120),
  };
}

function replayFrameAt(timeValue) {
  const frames = replayFrames();
  if (!frames.length) return null;
  const time = Number(timeValue);
  if (!Number.isFinite(time)) return frames[0];
  if (time <= Number(frames[0].sim_time || 0)) return frames[0];
  const last = frames[frames.length - 1];
  if (time >= Number(last.sim_time || 0)) return last;
  for (let index = 1; index < frames.length; index += 1) {
    const before = frames[index - 1];
    const after = frames[index];
    const t0 = Number(before.sim_time || 0);
    const t1 = Number(after.sim_time || 0);
    if (time < t0 || time > t1) continue;
    const ratio = t1 > t0 ? (time - t0) / (t1 - t0) : 0;
    const beforeById = new Map((before.entities || []).map((entity) => [entity.id, entity]));
    const afterById = new Map((after.entities || []).map((entity) => [entity.id, entity]));
    const ids = new Set([...beforeById.keys(), ...afterById.keys()]);
    const entities = [...ids].map((id) => blendEntity(beforeById.get(id), afterById.get(id), ratio)).filter(Boolean);
    return {
      ...after,
      source: "afsim-run-replay/interpolated",
      sim_time: time,
      entity_count: entities.length,
      entities,
      events: [...new Set([...(before.events || []), ...(after.events || [])])].slice(-40),
    };
  }
  return nearestReplayFrame(time);
}

function applyReplayTime(timeValue) {
  const frame = replayFrameAt(timeValue);
  if (!frame) return false;
  state.currentFrame = frame;
  if (state.workbench?.simulation_time) {
    state.workbench.simulation_time.current = Number(frame.sim_time || 0);
  }
  return true;
}

function targetEntities() {
  const byId = new Map((state.workbench?.platforms || []).map((platform) => [platform.id, platform]));
  for (const entity of state.currentFrame?.entities || []) {
    const previous = byId.get(entity.id) || {};
    byId.set(entity.id, {
      ...previous,
      ...entity,
      name: entity.name || previous.name || entity.id,
      side: entity.side || previous.side || "neutral",
      kind: entity.kind || previous.kind || entity.category || "aircraft",
      position: { lat: Number(entity.lat), lon: Number(entity.lon), alt_m: Number(entity.alt_m || 0) },
    });
  }
  return [...byId.values()];
}

function sourceParams() {
  const params = new URLSearchParams();
  if (state.activeScenarioId) {
    params.set("scenario_id", state.activeScenarioId);
    return params;
  }
  const demo = state.activeDemo || {};
  params.set("demo_name", demo.demo_name || "simple_scenario");
  if (demo.input_file) params.set("input_file", demo.input_file);
  return params;
}

function renderMapModeButtons() {
  els.view2d.classList.toggle("active", state.mapMode === "2d");
  els.view25d.classList.toggle("active", state.mapMode === "25d");
  els.view3d.classList.toggle("active", state.mapMode === "3d");
  els.viewSplit.classList.toggle("active", state.mapMode === "split");
  els.measureTool.classList.toggle("active", state.interactionMode === "measure");
  els.boxSelect.classList.toggle("active", state.interactionMode === "box");
  els.editTarget.classList.toggle("active", state.interactionMode === "edit");
}

function renderMap() {
  renderMapModeButtons();
  if (!state.workbench?.platforms?.length || !state.workbench.bounds) {
    disposeOperationalMap();
    els.sceneView.innerHTML = `<div class="scene-empty">暂无可显示的 AFSIM 场景数据。</div>`;
    return;
  }
  renderOperationalMap(els.sceneView, state.workbench, {
    mode: state.mapMode,
    frame: state.currentFrame,
    layers: state.workbench.layers,
    filters: filters(),
    selectedId: state.selectedId,
    focusLayerId: state.focusLayerId,
    domain: state.domain,
    viewState: state.mapView,
    interactionMode: state.interactionMode,
  });
}

function renderLayerTree() {
  const wb = state.workbench;
  if (!wb?.layers?.length) {
    els.layerTree.innerHTML = `<div class="list-item"><small>等待后端图层目录。</small></div>`;
    return;
  }
  const groups = wb.layer_groups || [];
  els.layerTree.innerHTML = groups
    .map((group) => {
      const layers = wb.layers.filter((layer) => layer.group === group.id);
      return `
        <div class="layer-group">
          <div class="layer-group-title">
            <strong>${escapeHtml(group.label)}</strong>
            <small>${layers.length}</small>
          </div>
          ${layers.map((layer) => `
            <div class="layer-row ${state.focusLayerId === layer.id ? "focused" : ""}" data-layer-id="${escapeHtml(layer.id)}">
              <input data-layer-field="visible" type="checkbox" ${layer.visible ? "checked" : ""} ${layer.locked ? "disabled" : ""} />
              <strong title="${escapeHtml(layer.name)}">${escapeHtml(layer.name)}</strong>
              <input data-layer-field="opacity" type="range" min="0" max="1" step="0.05" value="${Number(layer.opacity ?? 1)}" ${layer.locked ? "disabled" : ""} />
              <input data-layer-field="locked" title="锁定" type="checkbox" ${layer.locked ? "checked" : ""} />
              <input data-layer-field="queryable" title="查询" type="checkbox" ${layer.queryable ? "checked" : ""} />
              <button data-layer-action="focus" title="聚焦图层" type="button">⌖</button>
              <button data-layer-action="export" title="导出图层" type="button">⇩</button>
            </div>
          `).join("")}
        </div>
      `;
    })
    .join("");
}

function renderTargets() {
  const platforms = targetEntities();
  els.targetSummary.textContent = String(platforms.length);
  if (!platforms.length) {
    els.targetList.innerHTML = `<div class="list-item"><small>无目标。</small></div>`;
    renderTargetProperties(null);
    return;
  }
  els.targetList.innerHTML = platforms
    .map((platform) => `
      <div class="table-row ${platform.id === state.selectedId ? "active" : ""} ${state.selectedIds.has(platform.id) ? "multi-selected" : ""}" data-target-id="${escapeHtml(platform.id)}">
        <strong>${escapeHtml(platform.name || platform.id)}</strong>
        <span style="color:${sideColor(platform.side)}">${escapeHtml(platform.side)}</span>
        <small>${escapeHtml(platform.kind)} / ${Math.round(platform.alt_m || 0)}m</small>
      </div>
    `)
    .join("");
  renderTargetProperties(platforms.find((item) => item.id === state.selectedId) || platforms[0]);
}

function renderTargetProperties(platform) {
  if (!platform) {
    els.targetProperties.innerHTML = `<span>状态</span><strong>未选择目标</strong>`;
    syncEditInputs(null);
    return;
  }
  state.selectedId = platform.id;
  syncEditInputs(platform);
  const rows = [
    ["名称", platform.name || platform.id],
    ["阵营", platform.side],
    ["类型", platform.type || platform.category || platform.kind],
    ["状态", platform.status],
    ["坐标", `${Number(platform.lat).toFixed(4)}, ${Number(platform.lon).toFixed(4)}`],
    ["高度", `${Math.round(platform.alt_m || 0)} m`],
    ["速度", `${Math.round(platform.speed_kts || 0)} kts`],
    ["航向", `${Number(platform.heading_deg || 0).toFixed(1)} deg`],
    ["传感器", `${platform.sensor_ids?.length || 0}`],
    ["武器", `${platform.weapon_ids?.length || 0}`],
    ["处理器", `${platform.processor_ids?.length || platform.afsim?.processors?.length || 0}`],
    ["通信", `${platform.communication_ids?.length || platform.afsim?.communications?.length || 0}`],
    ["批次", platform.batch_id || "-"],
    ["源文件", platform.afsim?.source_ref?.file || platform.metadata?.source || "-"],
    ["源行号", platform.afsim?.source_ref?.line || "-"],
  ];
  els.targetProperties.innerHTML = rows
    .map(([key, value]) => `<span>${escapeHtml(key)}</span><strong>${escapeHtml(value)}</strong>`)
    .join("");
}

function selectedPlatform() {
  return targetEntities().find((item) => item.id === state.selectedId) || null;
}

function syncEditInputs(platform = selectedPlatform()) {
  const disabled = !platform;
  [els.editLat, els.editLon, els.editAlt, els.editHeading, els.previewEdit, els.commitEdit, els.undoEdit].forEach((element) => {
    element.disabled = disabled;
  });
  if (!platform) {
    els.patchPreview.textContent = "选择目标后可预览受控 JSON patch。";
    return;
  }
  els.editLat.value = Number(platform.lat ?? platform.position?.lat ?? 0).toFixed(6);
  els.editLon.value = Number(platform.lon ?? platform.position?.lon ?? 0).toFixed(6);
  els.editAlt.value = String(Math.round(Number(platform.alt_m ?? platform.position?.alt_m ?? 0)));
  els.editHeading.value = Number(platform.heading_deg || 0).toFixed(1);
  renderPatchPreview();
}

function currentEditPosition() {
  return {
    lat: readNumber(els.editLat.value),
    lon: readNumber(els.editLon.value),
    alt_m: readNumber(els.editAlt.value),
    heading_deg: readNumber(els.editHeading.value),
  };
}

function platformPosition(platform) {
  return {
    lat: Number(platform.lat ?? platform.position?.lat ?? 0),
    lon: Number(platform.lon ?? platform.position?.lon ?? 0),
    alt_m: Number(platform.alt_m ?? platform.position?.alt_m ?? 0),
    heading_deg: Number(platform.heading_deg || 0),
  };
}

function applyPositionToEntity(entity, position) {
  if (!entity) return;
  entity.lat = position.lat;
  entity.lon = position.lon;
  entity.alt_m = position.alt_m;
  entity.heading_deg = position.heading_deg;
  entity.position = { lat: position.lat, lon: position.lon, alt_m: position.alt_m };
  if (Array.isArray(entity.route) && entity.route.length) {
    entity.route[0] = { ...entity.route[0], lat: position.lat, lon: position.lon, alt_m: position.alt_m, heading_deg: position.heading_deg };
  }
}

function applyPatchToWorkbench(patch, direction = "after") {
  if (!state.workbench || patch.op !== "replace_platform_position") return;
  const position = patch[direction];
  const platform = (state.workbench.platforms || []).find((item) => item.id === patch.platform_id);
  applyPositionToEntity(platform, position);
  const frameEntity = (state.currentFrame?.entities || []).find((item) => item.id === patch.platform_id);
  applyPositionToEntity(frameEntity, position);
}

function renderPatchPreview() {
  els.patchSummary.textContent = `${state.pendingPatches.length} patch`;
  if (!state.pendingPatches.length) {
    els.patchPreview.textContent = "暂无预览 patch。地图编辑会生成受控 JSON patch，并保存为中间草稿。";
    return;
  }
  els.patchPreview.textContent = JSON.stringify({
    schema_version: "afsim-controlled-patch.v1",
    raw_afsim_write: false,
    patches: state.pendingPatches,
  }, null, 2);
}

function previewSelectedEdit() {
  const platform = selectedPlatform();
  if (!platform) throw new Error("未选择目标");
  const before = platformPosition(platform);
  const after = currentEditPosition();
  const patch = {
    op: "replace_platform_position",
    platform_id: platform.id,
    before,
    after,
    source_ref: platform.afsim?.source_ref || platform.metadata?.source_ref || null,
    created_at: new Date().toISOString(),
  };
  state.pendingPatches.push(patch);
  applyPatchToWorkbench(patch, "after");
  renderPatchPreview();
  renderTargets();
  renderMap();
  els.eventLog.textContent = `已预览位置 patch：${platform.id}`;
}

async function commitPatches() {
  if (!state.pendingPatches.length) throw new Error("没有待保存 patch");
  const payload = {
    name: `${els.designName.value || "scene"}_controlled_patch`,
    source: state.workbench?.source?.path || "web",
    author: "web",
    scene: {
      workbench: state.workbench,
      selected_id: state.selectedId,
      domain: state.domain,
    },
    operations: state.pendingPatches,
  };
  const result = await api("/api/afsim/drafts", {
    method: "POST",
    body: JSON.stringify(payload),
  });
  state.pendingPatches = [];
  renderPatchPreview();
  await loadDrafts();
  els.eventLog.textContent = `受控 patch 已保存为草稿：${result.path}`;
}

function undoLastPatch() {
  const patch = state.pendingPatches.pop();
  if (!patch) return;
  applyPatchToWorkbench(patch, "before");
  renderPatchPreview();
  renderTargets();
  renderMap();
  els.eventLog.textContent = `已撤销预览 patch：${patch.platform_id}`;
}

function clearPatchPreview() {
  while (state.pendingPatches.length) {
    const patch = state.pendingPatches.pop();
    applyPatchToWorkbench(patch, "before");
  }
  renderPatchPreview();
  renderTargets();
  renderMap();
  els.eventLog.textContent = "已清空并回退全部预览 patch。";
}

function renderEvents() {
  const events = state.workbench?.events || [];
  if (!events.length) {
    els.eventList.innerHTML = `<div class="list-item"><small>暂无复盘事件。</small></div>`;
    els.eventLog.innerHTML = "";
    return;
  }
  els.eventList.innerHTML = events.slice(0, 120)
    .map((event, index) => `
      <div class="list-item" data-event-index="${index}">
        <strong>T+${Number(event.time || 0).toFixed(1)} ${escapeHtml(event.title || event.type)}</strong>
        <small>${escapeHtml(event.message || "")}</small>
      </div>
    `)
    .join("");
  els.eventLog.innerHTML = events.slice(0, 80)
    .map((event) => `
      <div class="event-log-row">
        <span>T+${Number(event.time || 0).toFixed(1)}</span>
        <span>${escapeHtml(event.type || "event")}</span>
        <span>${escapeHtml(event.message || event.title || "")}</span>
      </div>
    `)
    .join("");
}

function renderChains() {
  const graph = state.workbench?.chain_graph || { chains: [], links: [] };
  const chains = graph.chains || [];
  if (!chains.length) {
    els.chainList.innerHTML = `<div class="list-item"><small>暂无链路图。</small></div>`;
    return;
  }
  els.chainList.innerHTML = chains
    .map((chain) => `
      <div class="list-item">
        <strong>${escapeHtml(chain.name)}</strong>
        <small>节点 ${chain.node_ids?.length || 0} | 类型 ${escapeHtml(chain.link_type || "-")}</small>
      </div>
    `)
    .join("") +
    `<div class="list-item"><strong>总链路</strong><small>${(graph.links || []).length} 条观测/通信关系</small></div>`;
}

function renderTimeline() {
  const sim = state.workbench?.simulation_time || {};
  const current = Number(state.currentFrame?.sim_time ?? sim.current ?? 0);
  const replayEnd = Number(state.workbench?.replay?.summary?.timeline?.end || 0);
  els.timelineRange.max = String(Math.max(Number(sim.end || 600), replayEnd));
  els.timelineRange.value = String(Math.min(Number(els.timelineRange.max), current));
  els.timeLabel.textContent = `T+${current.toFixed(1)}s`;
  els.playSpeedLabel.textContent = `${state.playSpeed.toFixed(1)}x`;
  els.fpsLabel.textContent = `FPS ${Math.round(sim.fps || 0)}`;
  els.targetCountLabel.textContent = `目标 ${state.currentFrame?.entity_count ?? state.workbench?.stats?.platform_count ?? 0}`;
  const frameCount = state.workbench?.replay?.summary?.frame_count || 0;
  els.eventCountLabel.textContent = `事件 ${state.workbench?.stats?.event_count ?? 0} / 帧 ${frameCount}`;
}

function renderGeneratedScenarios() {
  if (!state.designs.length) {
    els.generatedScenarios.innerHTML = `<div class="list-item"><small>场景库为空。</small></div>`;
    return;
  }
  els.generatedScenarios.innerHTML = state.designs
    .map((item) => `
      <div class="list-item ${item.scenario_id === state.activeScenarioId ? "active" : ""}" data-scenario-id="${escapeHtml(item.scenario_id)}">
        <strong>${escapeHtml(item.name)}</strong>
        <small>${escapeHtml(item.scenario_id)} | 平台 ${item.platform_count || 0}</small>
      </div>
    `)
    .join("");
}

function renderDrafts(data) {
  const drafts = data?.drafts || [];
  if (!drafts.length) {
    els.draftList.innerHTML = `<div class="list-item"><small>暂无草稿审计记录。</small></div>`;
    return;
  }
  els.draftList.innerHTML = drafts.slice(0, 20)
    .map((draft) => `
      <div class="list-item" data-restore-draft="${escapeHtml(draft.draft_id)}">
        <strong>${escapeHtml(draft.name)}</strong>
        <small>${escapeHtml(draft.draft_id)} | 操作 ${draft.operation_count || 0}</small>
      </div>
    `)
    .join("");
}

function renderAll() {
  renderLayerTree();
  renderTargets();
  renderEvents();
  renderChains();
  renderTimeline();
  renderPatchPreview();
  renderMap();
}

async function loadHealth() {
  const health = await api("/api/health");
  const afsim = health.afsim || {};
  setStatus(els.missionStatus, afsim.mission_exists, afsim.mission_exists ? "mission OK" : "mission 缺失");
  setStatus(els.parserStatus, true, "parser OK");
  setStatus(els.llmStatus, true, "LLM ready");
  els.systemStatus.textContent = `AFSIM：${afsim.root || "-"}`;
}

async function loadTemplates() {
  try {
    state.templates = await api("/api/afsim/platform-templates");
  } catch {
    state.templates = DEFAULT_TEMPLATES;
  }
  renderTemplateOptions();
  if (!state.editorPlatforms.length) {
    state.editorPlatforms = [
      makePlatformFromTemplate(templateById("blue_fighter"), 0),
      makePlatformFromTemplate(templateById("red_fighter"), 1),
      makePlatformFromTemplate(templateById("awacs"), 2),
      makePlatformFromTemplate(templateById("ground_radar"), 3),
    ];
  }
  renderPlatformEditor();
}

async function loadDemos() {
  const demos = await api("/api/afsim/demos");
  els.afsimDemo.innerHTML = demos
    .map((demo) => `<option value="${escapeHtml(demo.name)}" data-input="${escapeHtml(demo.default_input)}">${escapeHtml(demo.name)} / ${escapeHtml(demo.default_input)}</option>`)
    .join("");
  if (demos.length && !state.activeDemo) {
    state.activeDemo = { demo_name: demos[0].name, input_file: demos[0].default_input };
  }
}

async function loadGeneratedScenarios() {
  state.designs = await api("/api/afsim/designs");
  renderGeneratedScenarios();
}

async function loadDrafts() {
  const drafts = await api("/api/afsim/drafts");
  renderDrafts(drafts);
}

async function loadWorkbench(params = sourceParams()) {
  const wb = await api(`/api/afsim/workbench?${params.toString()}`);
  state.workbench = wb;
  state.currentFrame = null;
  state.selectedId = wb.platforms?.[0]?.id || null;
  state.selectedIds = new Set();
  state.mapView = null;
  state.measurePoints = [];
  state.pendingPatches = [];
  const source = wb.source || {};
  els.activeScenarioTitle.textContent = source.name || source.scenario_id || source.demo_name || "AFSIM 作战态势";
  els.activeScenarioLabel.textContent = `${source.kind || "-"} | ${source.input_file || source.path || "-"}`;
  renderAll();
}

async function loadScenarioWorkbench(scenarioId) {
  stopRealtimeStream();
  state.activeScenarioId = scenarioId;
  state.activeDemo = null;
  await loadWorkbench(new URLSearchParams({ scenario_id: scenarioId }));
  renderGeneratedScenarios();
}

async function loadDemoWorkbench() {
  stopRealtimeStream();
  const selected = els.afsimDemo.options[els.afsimDemo.selectedIndex];
  if (!selected) throw new Error("没有可用 AFSIM demo");
  state.activeScenarioId = null;
  state.activeDemo = { demo_name: selected.value, input_file: selected.dataset.input };
  const params = new URLSearchParams({ demo_name: state.activeDemo.demo_name });
  if (state.activeDemo.input_file) params.set("input_file", state.activeDemo.input_file);
  await loadWorkbench(params);
  renderGeneratedScenarios();
}

async function persistLayers() {
  if (!state.workbench?.layers) return;
  const result = await api("/api/afsim/layers/state", {
    method: "POST",
    body: JSON.stringify({ layers: state.workbench.layers }),
  });
  state.workbench.layers = result.layers;
  renderLayerTree();
}

function layerExportPayload(layer) {
  const wb = state.workbench || {};
  const hasLayer = (item) => item?.layer_id === layer.id || (item?.layer_ids || []).includes(layer.id);
  return {
    schema_version: "afsim-layer-export.v1",
    exported_at: new Date().toISOString(),
    layer,
    source: wb.source || {},
    platforms: (wb.platforms || []).filter((item) => {
      if (layer.id === "deployment.blue_platforms") return item.side === "blue";
      if (layer.id === "deployment.red_platforms") return item.side === "red";
      if (layer.id === "deployment.neutral_platforms") return !["blue", "red"].includes(item.side);
      return hasLayer(item);
    }),
    tracks: (wb.tracks || []).filter(hasLayer),
    sensors: (wb.sensors || []).filter(hasLayer),
    weapons: (wb.weapons || []).filter(hasLayer),
    detections: (wb.detections || []).filter(hasLayer),
    communications: (wb.communications || []).filter(hasLayer),
    events: (wb.events || []).filter(hasLayer),
  };
}

function exportLayer(layer) {
  const blob = new Blob([JSON.stringify(layerExportPayload(layer), null, 2)], { type: "application/json;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = `${layer.id.replaceAll(".", "_")}.json`;
  anchor.click();
  URL.revokeObjectURL(url);
  els.eventLog.textContent = `已导出图层：${layer.name}`;
}

function focusLayer(layer) {
  state.focusLayerId = state.focusLayerId === layer.id ? null : layer.id;
  layer.visible = true;
  els.eventLog.textContent = state.focusLayerId ? `已聚焦图层：${layer.name}` : "已取消图层聚焦。";
  renderLayerTree();
  renderMap();
}

async function saveScenario() {
  if (!state.editorPlatforms.length) throw new Error("至少需要一个平台");
  els.eventLog.textContent = "正在生成 AFSIM 场景...";
  const result = await api("/api/afsim/designs", {
    method: "POST",
    body: JSON.stringify(collectDesign()),
  });
  await loadGeneratedScenarios();
  await loadScenarioWorkbench(result.scenario_id);
  els.eventLog.textContent = `已生成：${result.scenario_path}`;
  return result.scenario_id;
}

async function ensureActiveScenario() {
  if (state.activeScenarioId) return state.activeScenarioId;
  return saveScenario();
}

async function runGeneratedScenario() {
  const scenarioId = await ensureActiveScenario();
  els.eventLog.textContent = `正在运行生成场景：${scenarioId}`;
  const run = await api(`/api/afsim/designs/${encodeURIComponent(scenarioId)}/run`, {
    method: "POST",
    body: JSON.stringify({ timeout_seconds: 180 }),
  });
  state.lastRunId = run.run_id;
  els.eventLog.textContent = summarizeRun(run);
  await refreshReplay();
}

async function runDemo() {
  const selected = els.afsimDemo.options[els.afsimDemo.selectedIndex];
  if (!selected) throw new Error("没有可用 AFSIM demo");
  els.eventLog.textContent = `正在运行 Demo：${selected.value}`;
  const run = await api("/api/afsim/run", {
    method: "POST",
    body: JSON.stringify({ demo_name: selected.value, input_file: selected.dataset.input, timeout_seconds: 180 }),
  });
  state.lastRunId = run.run_id;
  els.eventLog.textContent = summarizeRun(run);
  await refreshReplay();
}

function summarizeRun(run) {
  const files = (run.files || []).map((file) => file.name).join(", ") || "无";
  const fatal = run.summary?.fatal?.length ? `\nFATAL:\n${run.summary.fatal.join("\n")}` : "";
  const warnings = run.summary?.warnings?.length ? `\nWARN:\n${run.summary.warnings.join("\n")}` : "";
  return [
    `来源：${run.source || "demo"}`,
    `场景：${run.demo_name || run.scenario_id || "-"} / ${run.input_file}`,
    `返回码：${run.returncode}`,
    `耗时：${run.duration_seconds}s`,
    `输出目录：${run.run_dir}`,
    `输出文件：${files}`,
    `完成标记：${run.summary?.completed ? "complete" : "unknown"}`,
    fatal,
    warnings,
  ].filter(Boolean).join("\n");
}

async function saveDraft() {
  const payload = {
    name: `${els.designName.value || "scene"}_draft`,
    source: state.workbench?.source?.path || "web",
    author: "web",
    scene: {
      workbench: state.workbench,
      design: collectDesign(),
      selected_id: state.selectedId,
      domain: state.domain,
    },
    operations: [
      { action: "save_intermediate_json", at: new Date().toISOString() },
      ...state.pendingPatches,
    ],
  };
  const result = await api("/api/afsim/drafts", {
    method: "POST",
    body: JSON.stringify(payload),
  });
  await loadDrafts();
  els.eventLog.textContent = `草稿已保存：${result.path}`;
}

async function restoreDraft(draftId) {
  const result = await api(`/api/afsim/drafts/${encodeURIComponent(draftId)}/restore`, { method: "POST" });
  await loadDrafts();
  els.eventLog.textContent = `已回退到草稿：${result.current_path}`;
}

async function refreshReplay() {
  const replay = await api("/api/afsim/replay/latest");
  if (state.workbench) {
    state.workbench.replay = replay;
    state.workbench.tracks = [
      ...(state.workbench.tracks || []).filter((track) => !String(track.id || "").startsWith("replay_trk_")),
      ...(replay.tracks || []),
    ];
    if (replay.bounds) state.workbench.bounds = replay.bounds;
    if (state.workbench.simulation_time && replay.summary?.timeline?.end) {
      state.workbench.simulation_time.end = Math.max(Number(state.workbench.simulation_time.end || 0), Number(replay.summary.timeline.end));
    }
    const runId = replay.summary?.run_id;
    const existingSceneEvents = (state.workbench.events || []).filter((event) => !event.run_id || event.run_id !== runId);
    state.workbench.events = [...existingSceneEvents, ...(replay.events || [])].sort((a, b) => Number(a.time || 0) - Number(b.time || 0));
    state.workbench.stats.event_count = state.workbench.events.length;
    if (replay.frames?.length) {
      state.currentFrame = replay.frames[0];
      state.selectedId = replay.frames[0].entities?.[0]?.id || state.selectedId;
    }
    renderTargets();
    renderEvents();
    renderTimeline();
    renderMap();
    els.eventLog.textContent = `已加载复盘：${replay.summary?.run_id || "-"}，事件 ${replay.summary?.event_count || 0}，帧 ${replay.summary?.frame_count || 0}`;
  }
}

function realtimeParams() {
  const params = sourceParams();
  params.set("interval_seconds", String(0.75 / state.playSpeed));
  params.set("loop_seconds", "120");
  return params;
}

function stopRealtimeStream() {
  if (state.liveSocket) state.liveSocket.close();
  state.liveSocket = null;
  state.liveActive = false;
  els.liveScene.classList.remove("active");
  els.liveScene.textContent = "实时";
}

function startRealtimeStream() {
  stopRealtimeStream();
  const params = realtimeParams();
  const protocol = window.location.protocol === "https:" ? "wss" : "ws";
  const socket = new WebSocket(`${protocol}://${window.location.host}/ws/afsim/realtime?${params.toString()}`);
  state.liveSocket = socket;
  state.liveActive = true;
  els.liveScene.classList.add("active");
  els.liveScene.textContent = "停止";
  socket.addEventListener("message", (event) => {
    const frame = JSON.parse(event.data);
    if (frame.error) {
      els.eventLog.textContent = `实时态势失败：${frame.error}`;
      stopRealtimeStream();
      return;
    }
    state.currentFrame = frame;
    renderTimeline();
    renderMap();
  });
  socket.addEventListener("close", () => {
    if (state.liveSocket === socket) stopRealtimeStream();
  });
  socket.addEventListener("error", () => {
    els.eventLog.textContent = "实时态势连接失败。";
  });
}

function toggleRealtimeStream() {
  if (state.liveActive) stopRealtimeStream();
  else startRealtimeStream();
}

async function simControl(action) {
  const result = await api("/api/control", {
    method: "POST",
    body: JSON.stringify({ action, step_seconds: action === "step" ? 1 : null }),
  });
  if (action === "start" || action === "resume") startRealtimeStream();
  if (action === "stop" || action === "reset" || action === "pause") stopRealtimeStream();
  if (action === "faster") state.playSpeed = Math.min(16, state.playSpeed * 2);
  if (action === "slower") state.playSpeed = Math.max(0.25, state.playSpeed / 2);
  if (state.workbench?.simulation_time) {
    state.workbench.simulation_time.current = Number(result.sim_time ?? state.workbench.simulation_time.current ?? 0);
    state.workbench.simulation_time.speed_factor = Number(result.speed_factor ?? state.playSpeed);
    state.workbench.simulation_time.running = Boolean(result.running);
  }
  renderTimeline();
}

async function analyzeAfsim() {
  els.analysisOutput.textContent = "正在分析最近一次 AFSIM 运行输出...";
  const result = await api("/api/afsim/analyze", {
    method: "POST",
    body: JSON.stringify({ run_id: state.lastRunId || state.workbench?.replay?.summary?.run_id || null, prompt: els.analysisPrompt.value }),
  });
  const analysis = result.analysis || {};
  els.analysisOutput.textContent = [
    `${analysis.source || "analysis"}：${analysis.summary || ""}`,
    `风险等级：${analysis.risk_level || "-"}`,
    `发现：${(analysis.findings || []).join("；") || "-"}`,
    `下一步：${(analysis.next_steps || []).join("；") || "-"}`,
  ].join("\n");
}

async function agentTick() {
  els.agentOutput.textContent = "指挥 Agent 正在评估当前态势...";
  const result = await api("/api/agent/tick", {
    method: "POST",
    body: JSON.stringify({
      objective: els.agentObjective.value,
      side: "blue",
      autonomy: "manual_review",
      step_seconds: 5,
    }),
  });
  const advice = result.advice || {};
  els.agentOutput.textContent = [
    `${advice.source || "agent"}：${advice.summary || ""}`,
    `仿真时间：T+${result.state?.sim_time ?? "-"}s`,
    ...(advice.commands || []).map((cmd) => `- ${cmd.action} ${cmd.unit_id || ""} ${cmd.value ?? ""} | ${cmd.reason || ""}`),
  ].join("\n");
}

async function resetAgentSandbox() {
  await api("/api/control", { method: "POST", body: JSON.stringify({ action: "reset" }) });
  els.agentOutput.textContent = "指挥沙盘已重置。";
}

function setTab(prefix, id) {
  document.querySelectorAll(`[data-${prefix}-tab]`).forEach((button) => {
    button.classList.toggle("active", button.dataset[`${prefix}Tab`] === id);
  });
  document.querySelectorAll(`[data-${prefix}-panel]`).forEach((panel) => {
    panel.classList.toggle("active", panel.dataset[`${prefix}Panel`] === id);
  });
}

function bindEvents() {
  document.querySelectorAll("[data-left-tab]").forEach((button) => {
    button.addEventListener("click", () => setTab("left", button.dataset.leftTab));
  });
  document.querySelectorAll("[data-right-tab]").forEach((button) => {
    button.addEventListener("click", () => setTab("right", button.dataset.rightTab));
  });
  els.domainSelect.addEventListener("change", () => {
    state.domain = els.domainSelect.value;
    renderMap();
  });
  [els.filterBlue, els.filterRed, els.filterNeutral, els.filterAir, els.filterGround, els.filterSea].forEach((input) => {
    input.addEventListener("change", renderMap);
  });
  els.layerTree.addEventListener("input", (event) => {
    const row = event.target.closest("[data-layer-id]");
    if (!row || !state.workbench) return;
    const layer = state.workbench.layers.find((item) => item.id === row.dataset.layerId);
    if (!layer) return;
    const field = event.target.dataset.layerField;
    if (field === "opacity") layer.opacity = Number(event.target.value);
    if (field === "visible" || field === "locked" || field === "queryable") layer[field] = event.target.checked;
    renderMap();
  });
  els.layerTree.addEventListener("click", (event) => {
    const button = event.target.closest("[data-layer-action]");
    const row = event.target.closest("[data-layer-id]");
    if (!button || !row || !state.workbench) return;
    const layer = state.workbench.layers.find((item) => item.id === row.dataset.layerId);
    if (!layer) return;
    if (button.dataset.layerAction === "focus") focusLayer(layer);
    if (button.dataset.layerAction === "export") exportLayer(layer);
  });
  els.persistLayers.addEventListener("click", () => persistLayers().catch((error) => (els.eventLog.textContent = `图层保存失败：${error.message}`)));
  els.platformEditor.addEventListener("input", (event) => {
    const target = event.target;
    const row = target.closest("[data-platform-index]");
    if (!row || !target.dataset.field) return;
    const field = target.dataset.field;
    const numericFields = new Set(["lat", "lon", "altitude_m", "speed_kts", "heading_deg", "routeLat", "routeLon"]);
    state.editorPlatforms[Number(row.dataset.platformIndex)][field] = numericFields.has(field) ? (target.value === "" ? "" : Number(target.value)) : target.value;
    const title = row.querySelector(".platform-title strong");
    if (title && field === "name") title.textContent = target.value || `Platform_${Number(row.dataset.platformIndex) + 1}`;
  });
  els.platformEditor.addEventListener("click", (event) => {
    const button = event.target.closest("[data-remove-platform]");
    if (!button) return;
    state.editorPlatforms.splice(Number(button.dataset.removePlatform), 1);
    renderPlatformEditor();
  });
  els.addPlatform.addEventListener("click", () => {
    state.editorPlatforms.push(makePlatformFromTemplate(templateById(els.platformTemplate.value), state.editorPlatforms.length));
    renderPlatformEditor();
  });
  els.saveScenario.addEventListener("click", () => saveScenario().catch((error) => (els.eventLog.textContent = `生成失败：${error.message}`)));
  els.saveDraft.addEventListener("click", () => saveDraft().catch((error) => (els.eventLog.textContent = `草稿保存失败：${error.message}`)));
  els.generatedScenarios.addEventListener("click", (event) => {
    const item = event.target.closest("[data-scenario-id]");
    if (!item) return;
    loadScenarioWorkbench(item.dataset.scenarioId).catch((error) => (els.eventLog.textContent = `载入失败：${error.message}`));
  });
  els.draftList.addEventListener("click", (event) => {
    const item = event.target.closest("[data-restore-draft]");
    if (!item) return;
    restoreDraft(item.dataset.restoreDraft).catch((error) => (els.eventLog.textContent = `回退失败：${error.message}`));
  });
  els.runGenerated.addEventListener("click", () => runGeneratedScenario().catch((error) => (els.eventLog.textContent = `运行失败：${error.message}`)));
  els.previewDemo.addEventListener("click", () => loadDemoWorkbench().catch((error) => (els.eventLog.textContent = `Demo 载入失败：${error.message}`)));
  els.runDemo.addEventListener("click", () => runDemo().catch((error) => (els.eventLog.textContent = `Demo 运行失败：${error.message}`)));
  els.afsimDemo.addEventListener("change", () => loadDemoWorkbench().catch((error) => (els.eventLog.textContent = `Demo 载入失败：${error.message}`)));
  els.view2d.addEventListener("click", () => { state.mapMode = "2d"; renderMap(); });
  els.view25d.addEventListener("click", () => { state.mapMode = "25d"; renderMap(); });
  els.view3d.addEventListener("click", () => { state.mapMode = "3d"; renderMap(); });
  els.viewSplit.addEventListener("click", () => { state.mapMode = "split"; renderMap(); });
  els.liveScene.addEventListener("click", toggleRealtimeStream);
  els.fitScene.addEventListener("click", () => {
    state.mapView = null;
    renderMap();
  });
  els.followTarget.addEventListener("click", () => {
    els.followTarget.classList.toggle("active");
    renderMap();
  });
  els.measureTool.addEventListener("click", () => {
    state.interactionMode = state.interactionMode === "measure" ? "select" : "measure";
    state.measurePoints = [];
    els.eventLog.textContent = state.interactionMode === "measure" ? "测量模式：在地图上点击两个点。" : "已退出测量模式。";
    renderMap();
  });
  els.boxSelect.addEventListener("click", () => {
    state.interactionMode = state.interactionMode === "box" ? "select" : "box";
    els.eventLog.textContent = state.interactionMode === "box" ? "框选模式：在 2D 地图上拖拽一个矩形。" : "框选模式已关闭。";
    renderMap();
  });
  els.editTarget.addEventListener("click", () => {
    state.interactionMode = state.interactionMode === "edit" ? "select" : "edit";
    els.eventLog.textContent = state.interactionMode === "edit" ? "编辑模式：点击地图写入目标新坐标，先预览 patch 再保存草稿。" : "已退出编辑模式。";
    renderMap();
  });
  els.sceneView.addEventListener("afsim-map-select", (event) => {
    state.selectedId = event.detail.id;
    state.selectedIds = new Set([event.detail.id]);
    renderTargets();
    renderMap();
  });
  els.sceneView.addEventListener("afsim-map-view-change", (event) => {
    state.mapView = event.detail;
    renderMap();
  });
  els.sceneView.addEventListener("afsim-map-box-select", (event) => {
    state.selectedIds = new Set(event.detail.ids || []);
    state.selectedId = event.detail.ids?.[0] || state.selectedId;
    const bounds = event.detail.bounds || {};
    els.eventLog.textContent = `框选命中 ${state.selectedIds.size} 个目标：LAT ${Number(bounds.min_lat || 0).toFixed(4)}..${Number(bounds.max_lat || 0).toFixed(4)} / LON ${Number(bounds.min_lon || 0).toFixed(4)}..${Number(bounds.max_lon || 0).toFixed(4)}`;
    renderTargets();
    renderMap();
  });
  els.sceneView.addEventListener("afsim-map-measure-point", (event) => {
    state.measurePoints.push(event.detail.point);
    if (state.measurePoints.length >= 2) {
      const [a, b] = state.measurePoints.slice(-2);
      const distance = haversineKm(a, b);
      const bearing = bearingDeg(a, b);
      els.eventLog.textContent = `测量：${distance.toFixed(2)} km，方位 ${bearing.toFixed(1)} deg；A(${a.lat.toFixed(5)}, ${a.lon.toFixed(5)}) -> B(${b.lat.toFixed(5)}, ${b.lon.toFixed(5)})`;
      state.measurePoints = [];
    } else {
      els.eventLog.textContent = `测量起点：${event.detail.point.lat.toFixed(5)}, ${event.detail.point.lon.toFixed(5)}`;
    }
  });
  els.sceneView.addEventListener("afsim-map-edit-point", (event) => {
    if (event.detail.targetId) state.selectedId = event.detail.targetId;
    const point = event.detail.point;
    renderTargets();
    els.editLat.value = point.lat.toFixed(6);
    els.editLon.value = point.lon.toFixed(6);
    els.eventLog.textContent = `已写入编辑坐标：${point.lat.toFixed(6)}, ${point.lon.toFixed(6)}。点击“预览 patch”生效。`;
  });
  els.targetList.addEventListener("click", (event) => {
    const row = event.target.closest("[data-target-id]");
    if (!row) return;
    state.selectedId = row.dataset.targetId;
    state.selectedIds = new Set([state.selectedId]);
    renderTargets();
    renderMap();
  });
  els.eventList.addEventListener("click", (event) => {
    const row = event.target.closest("[data-event-index]");
    if (!row) return;
    const eventData = state.workbench?.events?.[Number(row.dataset.eventIndex)];
    if (eventData?.platform_id) state.selectedId = eventData.platform_id;
    if (eventData?.time != null) {
      els.timelineRange.value = String(eventData.time);
      applyReplayTime(eventData.time);
    }
    renderTargets();
    renderTimeline();
    renderMap();
  });
  els.loadReplay.addEventListener("click", () => refreshReplay().catch((error) => (els.eventLog.textContent = `复盘刷新失败：${error.message}`)));
  els.simStart.addEventListener("click", () => simControl("start").catch((error) => (els.eventLog.textContent = `启动失败：${error.message}`)));
  els.simPause.addEventListener("click", () => simControl("pause").catch((error) => (els.eventLog.textContent = `暂停失败：${error.message}`)));
  els.simResume.addEventListener("click", () => simControl("resume").catch((error) => (els.eventLog.textContent = `继续失败：${error.message}`)));
  els.simSlower.addEventListener("click", () => simControl("slower").catch((error) => (els.eventLog.textContent = `减速失败：${error.message}`)));
  els.simFaster.addEventListener("click", () => simControl("faster").catch((error) => (els.eventLog.textContent = `加速失败：${error.message}`)));
  els.simStep.addEventListener("click", () => simControl("step").catch((error) => (els.eventLog.textContent = `单步失败：${error.message}`)));
  els.simReset.addEventListener("click", () => simControl("reset").catch((error) => (els.eventLog.textContent = `复位失败：${error.message}`)));
  els.simStop.addEventListener("click", () => simControl("stop").catch((error) => (els.eventLog.textContent = `终止失败：${error.message}`)));
  els.timelineRange.addEventListener("input", () => {
    if (state.workbench?.simulation_time) state.workbench.simulation_time.current = Number(els.timelineRange.value);
    if (!applyReplayTime(els.timelineRange.value)) state.currentFrame = null;
    renderTargets();
    renderTimeline();
    renderMap();
  });
  els.previewEdit.addEventListener("click", () => {
    try {
      previewSelectedEdit();
    } catch (error) {
      els.eventLog.textContent = `预览失败：${error.message}`;
    }
  });
  els.commitEdit.addEventListener("click", () => commitPatches().catch((error) => (els.eventLog.textContent = `保存 patch 失败：${error.message}`)));
  els.undoEdit.addEventListener("click", undoLastPatch);
  els.clearPatches.addEventListener("click", clearPatchPreview);
  els.analyzeAfsim.addEventListener("click", () => analyzeAfsim().catch((error) => (els.analysisOutput.textContent = `分析失败：${error.message}`)));
  els.agentStep.addEventListener("click", () => agentTick().catch((error) => (els.agentOutput.textContent = `指挥失败：${error.message}`)));
  els.agentReset.addEventListener("click", () => resetAgentSandbox().catch((error) => (els.agentOutput.textContent = `重置失败：${error.message}`)));
}

async function boot() {
  assertRequiredElements();
  bindEvents();
  renderMapModeButtons();
  disposeOperationalMap();
  els.sceneView.innerHTML = `<div class="scene-empty">正在加载 AFSIM 场景...</div>`;
  await loadTemplates();
  await loadHealth().catch((error) => {
    els.systemStatus.textContent = `AFSIM 状态读取失败：${error.message}`;
  });
  const bootResults = await Promise.allSettled([loadDemos(), loadGeneratedScenarios(), loadDrafts()]);
  const failures = bootResults.filter((item) => item.status === "rejected");
  if (failures.length) {
    els.eventLog.textContent = failures.map((item) => `初始化部分失败：${item.reason?.message || item.reason}`).join("\n");
  }
  if (state.designs.length) await loadScenarioWorkbench(state.designs[0].scenario_id);
  else await loadDemoWorkbench();
  window.__AFSIM_LLM_BOOTED = true;
  document.documentElement.dataset.afsimBooted = "true";
}

boot().catch((error) => {
  els.systemStatus.textContent = `启动失败：${error.message}`;
});
