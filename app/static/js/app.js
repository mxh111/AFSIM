import { disposeOperationalMap, renderOperationalMap, sideColor } from "./map_renderer.js";

const DEFAULT_TEMPLATES = [
  { id: "blue_fighter", label: "蓝方战斗机", type_name: "WEB_AIRCRAFT", side: "blue", category: "fighter", icon: "F-22", altitude_m: 9000, speed_kts: 420, heading_deg: 90 },
  { id: "red_fighter", label: "红方战斗机", type_name: "WEB_AIRCRAFT", side: "red", category: "fighter", icon: "SU-27", altitude_m: 8500, speed_kts: 430, heading_deg: 270 },
  { id: "awacs", label: "预警机", type_name: "WEB_AWACS", side: "blue", category: "awacs", icon: "E-3", altitude_m: 9500, speed_kts: 350, heading_deg: 90 },
  { id: "ground_radar", label: "地面雷达站", type_name: "WEB_GROUND_RADAR", side: "blue", category: "radar", icon: "radar", altitude_m: 0, speed_kts: 0, heading_deg: 0 },
  { id: "red_ground_radar", label: "红方地面雷达站", type_name: "WEB_GROUND_RADAR", side: "red", category: "radar", icon: "radar", altitude_m: 0, speed_kts: 0, heading_deg: 0 },
  { id: "sam_site", label: "防空阵地", type_name: "WEB_SAM_SITE", side: "blue", category: "sam", icon: "sam", altitude_m: 0, speed_kts: 0, heading_deg: 0 },
  { id: "surface_ship", label: "水面舰艇", type_name: "WEB_SURFACE_SHIP", side: "blue", category: "ship", icon: "ship", altitude_m: 0, speed_kts: 0, heading_deg: 0 },
];

const state = {
  templates: DEFAULT_TEMPLATES,
  platforms: [],
  designs: [],
  activeScenarioId: null,
  activeScene: null,
  mapMode: "split",
  currentFrame: null,
  liveSocket: null,
  liveActive: false,
  lastRunId: null,
  pendingCommands: [],
  agentTimer: null,
};

const els = {
  systemStatus: document.getElementById("systemStatus"),
  missionStatus: document.getElementById("missionStatus"),
  parserStatus: document.getElementById("parserStatus"),
  llmStatus: document.getElementById("llmStatus"),
  designName: document.getElementById("designName"),
  designDescription: document.getElementById("designDescription"),
  endTime: document.getElementById("endTime"),
  platformTemplate: document.getElementById("platformTemplate"),
  platformEditor: document.getElementById("platformEditor"),
  addPlatform: document.getElementById("addPlatform"),
  saveScenario: document.getElementById("saveScenario"),
  runGenerated: document.getElementById("runGenerated"),
  refreshScene: document.getElementById("refreshScene"),
  view2d: document.getElementById("view2d"),
  view3d: document.getElementById("view3d"),
  viewSplit: document.getElementById("viewSplit"),
  liveScene: document.getElementById("liveScene"),
  fitScene: document.getElementById("fitScene"),
  agentObjective: document.getElementById("agentObjective"),
  agentSide: document.getElementById("agentSide"),
  agentAutonomy: document.getElementById("agentAutonomy"),
  agentStepSeconds: document.getElementById("agentStepSeconds"),
  agentStep: document.getElementById("agentStep"),
  agentStart: document.getElementById("agentStart"),
  agentStop: document.getElementById("agentStop"),
  agentApply: document.getElementById("agentApply"),
  agentReset: document.getElementById("agentReset"),
  afsimDemo: document.getElementById("afsimDemo"),
  previewDemo: document.getElementById("previewDemo"),
  runDemo: document.getElementById("runDemo"),
  analysisPrompt: document.getElementById("analysisPrompt"),
  analyzeAfsim: document.getElementById("analyzeAfsim"),
  sceneView: document.getElementById("sceneView"),
  activeScenarioLabel: document.getElementById("activeScenarioLabel"),
  scenarioPath: document.getElementById("scenarioPath"),
  scenarioText: document.getElementById("scenarioText"),
  generatedScenarios: document.getElementById("generatedScenarios"),
  scenarioPreview: document.getElementById("scenarioPreview"),
  parseSummary: document.getElementById("parseSummary"),
  agentOutput: document.getElementById("agentOutput"),
  runOutput: document.getElementById("runOutput"),
  analysisOutput: document.getElementById("analysisOutput"),
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
  if (missing.length) {
    throw new Error(`页面结构与脚本不匹配，缺少元素：${missing.join(", ")}`);
  }
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
    throw new Error(`${response.status} ${response.statusText}: ${text.slice(0, 260)}`);
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
  const routeLon = isRed ? lon - 0.35 : lon + 0.35;
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
    routeLon,
  };
}

function renderTemplateOptions() {
  els.platformTemplate.innerHTML = state.templates
    .map((item) => `<option value="${escapeHtml(item.id)}">${escapeHtml(item.label)} / ${escapeHtml(item.type_name)}</option>`)
    .join("");
}

function renderPlatformEditor() {
  if (!state.platforms.length) {
    els.platformEditor.innerHTML = `<div class="list-item"><small>请先从模板添加平台</small></div>`;
    return;
  }
  const typeOptions = [...new Set(state.templates.map((item) => item.type_name))]
    .map((type) => `<option value="${escapeHtml(type)}"> ${escapeHtml(type)} </option>`)
    .join("");
  els.platformEditor.innerHTML = state.platforms
    .map(
      (platform, index) => `
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
      `,
    )
    .join("");
}

function collectDesign() {
  return {
    name: els.designName.value.trim() || "web_scenario",
    description: els.designDescription.value.trim(),
    end_time_seconds: Math.max(1, Math.min(86400, readNumber(els.endTime.value, 600))),
    platforms: state.platforms.map((platform, index) => {
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

function sceneFromParsed(parsed) {
  const platforms = (parsed?.platforms || [])
    .map((item) => {
      const position = item.position || item.positions?.[0];
      if (!position || position.lat == null || position.lon == null) return null;
      return {
        id: item.id,
        side: item.side || "neutral",
        category: item.category || item.icon || item.type || "",
        type: item.type || "",
        lat: Number(position.lat),
        lon: Number(position.lon),
        alt_m: Number(position.alt_m || 0),
        route: (item.positions || []).map((point) => ({ lat: Number(point.lat), lon: Number(point.lon), alt_m: Number(point.alt_m || 0) })),
      };
    })
    .filter(Boolean);
  return {
    platforms,
    bounds: parsed?.bounds || null,
    geojson: parsed?.geojson || { type: "FeatureCollection", features: [] },
    included_files: parsed?.included_files || [],
    platform_count: parsed?.platform_count || platforms.length,
    route_count: parsed?.route_count || platforms.filter((item) => item.route?.length > 1).length,
  };
}

function renderScene(scene) {
  state.activeScene = scene;
  if (!scene?.platforms?.length || !scene.bounds) {
    disposeOperationalMap();
    els.sceneView.innerHTML = `<div class="scene-empty">暂无可显示的 AFSIM 场景数据。保存或选择一个场景后会在这里显示平台部署。</div>`;
    els.parseSummary.textContent = "等待解析 AFSIM 输入文件。";
    return;
  }
  renderOperationalMap(els.sceneView, scene, { mode: state.mapMode, frame: state.currentFrame });
  els.parseSummary.textContent = [
    `平台数量：${scene.platform_count}`,
    `航路数量：${scene.route_count}`,
    `GeoJSON 要素：${scene.geojson?.features?.length || 0}`,
    `递归解析文件：${scene.included_files.length}`,
    state.currentFrame ? `实时帧：T+${Number(state.currentFrame.sim_time || 0).toFixed(1)}s / ${state.currentFrame.source}` : "实时帧：未连接",
    ...scene.included_files.slice(0, 6).map((file) => `- ${file}`),
  ].join("\n");
}

function renderParsed(parsed) {
  const platforms = parsed?.platforms || [];
  if (!platforms.length) {
    els.scenarioPreview.innerHTML = `<div class="list-item"><small>暂无平台解析结果</small></div>`;
    renderScene({ platforms: [], bounds: null });
    return;
  }
  els.scenarioPreview.innerHTML = platforms
    .map((platform) => {
      const pos = platform.position || platform.positions?.[0] || {};
      const coord = pos.lat == null ? "无坐标" : `${Number(pos.lat).toFixed(4)}, ${Number(pos.lon).toFixed(4)}, ${Math.round(pos.alt_m || 0)}m`;
      return `
        <div class="table-row">
          <strong>${escapeHtml(platform.id)}</strong>
          <span style="color:${sideColor(platform.side)}">${escapeHtml(platform.side)}</span>
          <span>${escapeHtml(platform.category || platform.icon || platform.type)}</span>
          <small>${escapeHtml(coord)}</small>
        </div>
      `;
    })
    .join("");
  renderScene(sceneFromParsed(parsed));
}

function renderGeneratedScenarios() {
  if (!state.designs.length) {
    els.generatedScenarios.innerHTML = `<div class="list-item"><small>场景库为空，保存一个场景后会出现在这里。</small></div>`;
    return;
  }
  els.generatedScenarios.innerHTML = state.designs
    .map(
      (item) => `
        <div class="list-item with-actions ${item.scenario_id === state.activeScenarioId ? "active" : ""}">
          <div class="list-item-main" data-scenario-id="${escapeHtml(item.scenario_id)}">
            <strong>${escapeHtml(item.name)}</strong>
            <small>${escapeHtml(item.scenario_id)}</small>
            <small>平台 ${item.platform_count || 0} | ${item.end_time_seconds || "-"}s</small>
          </div>
          <div class="list-item-actions">
            <button class="mini-button" type="button" data-scenario-id="${escapeHtml(item.scenario_id)}">选择</button>
            <button class="mini-button danger-button" type="button" data-delete-scenario="${escapeHtml(item.scenario_id)}">删除</button>
          </div>
        </div>
      `,
    )
    .join("");
}

function renderMapModeButtons() {
  els.view2d.classList.toggle("active", state.mapMode === "2d");
  els.view3d.classList.toggle("active", state.mapMode === "3d");
  els.viewSplit.classList.toggle("active", state.mapMode === "split");
}

function setMapMode(mode) {
  state.mapMode = mode;
  renderMapModeButtons();
  renderScene(state.activeScene || { platforms: [], bounds: null });
}

function stopRealtimeStream() {
  if (state.liveSocket) {
    state.liveSocket.close();
  }
  state.liveSocket = null;
  state.liveActive = false;
  els.liveScene.classList.remove("active");
  els.liveScene.textContent = "实时";
}

async function realtimeParams() {
  const params = new URLSearchParams({ interval_seconds: "0.75", loop_seconds: "120" });
  if (state.activeScenarioId) {
    params.set("scenario_id", state.activeScenarioId);
    return params;
  }
  const selected = await selectedDemoPayload();
  params.set("demo_name", selected.demo_name);
  if (selected.input_file) params.set("input_file", selected.input_file);
  return params;
}

async function startRealtimeStream() {
  stopRealtimeStream();
  const params = await realtimeParams();
  const protocol = window.location.protocol === "https:" ? "wss" : "ws";
  const socket = new WebSocket(`${protocol}://${window.location.host}/ws/afsim/realtime?${params.toString()}`);
  state.liveSocket = socket;
  state.liveActive = true;
  els.liveScene.classList.add("active");
  els.liveScene.textContent = "停止";
  socket.addEventListener("message", (event) => {
    const frame = JSON.parse(event.data);
    if (frame.error) {
      els.runOutput.textContent = `实时态势失败：${frame.error}`;
      stopRealtimeStream();
      return;
    }
    state.currentFrame = frame;
    if (state.activeScene) renderScene(state.activeScene);
  });
  socket.addEventListener("close", () => {
    if (state.liveSocket === socket) {
      state.liveSocket = null;
      state.liveActive = false;
      els.liveScene.classList.remove("active");
      els.liveScene.textContent = "实时";
    }
  });
  socket.addEventListener("error", () => {
    els.runOutput.textContent = "实时态势连接失败。";
  });
}

function toggleRealtimeStream() {
  if (state.liveActive) {
    stopRealtimeStream();
    return;
  }
  startRealtimeStream().catch((error) => {
    stopRealtimeStream();
    els.runOutput.textContent = `实时态势启动失败：${error.message}`;
  });
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
  ]
    .filter(Boolean)
    .join("\n");
}

function summarizeAgent(result) {
  const advice = result.advice || {};
  const commands = advice.commands || [];
  const applied = result.applied || [];
  state.pendingCommands = commands;
  return [
    `${advice.source || "agent"}：${advice.summary || ""}`,
    `仿真时间：T+${result.state?.sim_time ?? "-"}s`,
    `探测链路：${result.state?.detections?.length ?? 0}`,
    `建议：`,
    ...commands.map((cmd) => `- ${cmd.action} ${cmd.unit_id || ""} ${cmd.value ?? ""} | ${cmd.reason || ""}`),
    applied.length ? `应用：${applied.map((item) => `${item.action}:${item.status}`).join("，")}` : "应用：等待人工确认",
  ].join("\n");
}

async function loadHealth() {
  const health = await api("/api/health");
  const afsim = health.afsim || {};
  setStatus(els.missionStatus, afsim.mission_exists, afsim.mission_exists ? "mission OK" : "mission 缺失");
  setStatus(els.parserStatus, true, "parser OK");
  setStatus(els.llmStatus, true, "LLM 已配置");
  els.systemStatus.textContent = `AFSIM：${afsim.root || "-"}`;
}

async function loadTemplates() {
  try {
    state.templates = await api("/api/afsim/platform-templates");
  } catch {
    state.templates = DEFAULT_TEMPLATES;
  }
  renderTemplateOptions();
  if (!state.platforms.length) {
    state.platforms = [
      makePlatformFromTemplate(templateById("blue_fighter"), 0),
      makePlatformFromTemplate(templateById("red_fighter"), 1),
      makePlatformFromTemplate(templateById("awacs"), 2),
      makePlatformFromTemplate(templateById("ground_radar"), 3),
    ];
    renderPlatformEditor();
  }
}

async function loadDemos() {
  const demos = await api("/api/afsim/demos");
  els.afsimDemo.innerHTML = demos
    .map((demo) => `<option value="${escapeHtml(demo.name)}" data-input="${escapeHtml(demo.default_input)}">${escapeHtml(demo.name)} / ${escapeHtml(demo.default_input)}</option>`)
    .join("");
}

async function loadGeneratedScenarios() {
  state.designs = await api("/api/afsim/designs");
  renderGeneratedScenarios();
  if (!state.activeScenarioId && state.designs.length) {
    await loadGeneratedDetail(state.designs[0].scenario_id);
  }
}

async function loadGeneratedDetail(scenarioId) {
  stopRealtimeStream();
  state.currentFrame = null;
  const detail = await api(`/api/afsim/designs/${encodeURIComponent(scenarioId)}`);
  state.activeScenarioId = detail.scenario_id;
  els.activeScenarioLabel.textContent = `${detail.design?.name || detail.scenario_id} | ${detail.input_file}`;
  els.scenarioPath.textContent = detail.scenario_path;
  els.scenarioText.textContent = detail.scenario_text || "";
  if (detail.design) {
    els.designName.value = detail.design.name || "web_scenario";
    els.designDescription.value = detail.design.description || "";
    els.endTime.value = detail.design.end_time_seconds || 600;
    state.platforms = (detail.design.platforms || []).map((platform) => ({
      ...platform,
      routeLat: platform.route?.[0]?.lat ?? platform.lat,
      routeLon: platform.route?.[0]?.lon ?? platform.lon,
    }));
    renderPlatformEditor();
  }
  renderParsed(detail.parsed);
  renderGeneratedScenarios();
  return detail;
}

async function saveScenario() {
  if (!state.platforms.length) {
    throw new Error("至少需要一个平台");
  }
  els.runOutput.textContent = "正在保存 AFSIM 场景...";
  const result = await api("/api/afsim/designs", {
    method: "POST",
    body: JSON.stringify(collectDesign()),
  });
  state.activeScenarioId = result.scenario_id;
  await loadGeneratedScenarios();
  await loadGeneratedDetail(result.scenario_id);
  els.runOutput.textContent = `已保存：${result.scenario_path}`;
  return result.scenario_id;
}

async function ensureActiveScenario() {
  if (state.activeScenarioId) return state.activeScenarioId;
  return saveScenario();
}

async function deleteScenario(scenarioId) {
  if (!scenarioId) return;
  await api(`/api/afsim/designs/${encodeURIComponent(scenarioId)}`, { method: "DELETE" });
  if (state.activeScenarioId === scenarioId) {
    stopRealtimeStream();
    state.currentFrame = null;
    state.activeScenarioId = null;
    els.scenarioText.textContent = "";
    els.scenarioPath.textContent = "scenario.txt";
    renderScene({ platforms: [], bounds: null });
  }
  await loadGeneratedScenarios();
  els.runOutput.textContent = `已删除场景：${scenarioId}`;
}

async function runGeneratedScenario() {
  const scenarioId = await ensureActiveScenario();
  els.runOutput.textContent = `正在运行生成场景：${scenarioId}`;
  const run = await api(`/api/afsim/designs/${encodeURIComponent(scenarioId)}/run`, {
    method: "POST",
    body: JSON.stringify({ timeout_seconds: 180 }),
  });
  state.lastRunId = run.run_id;
  els.runOutput.textContent = summarizeRun(run);
  return run;
}

async function selectedDemoPayload() {
  const selected = els.afsimDemo.options[els.afsimDemo.selectedIndex];
  if (!selected) throw new Error("没有可用 AFSIM demo");
  return { demo_name: selected.value, input_file: selected.dataset.input };
}

async function previewDemo() {
  stopRealtimeStream();
  state.currentFrame = null;
  const selected = await selectedDemoPayload();
  const parsed = await api(`/api/afsim/scenario?demo_name=${encodeURIComponent(selected.demo_name)}&input_file=${encodeURIComponent(selected.input_file)}`);
  state.activeScenarioId = null;
  els.activeScenarioLabel.textContent = `${selected.demo_name} | ${selected.input_file}`;
  els.scenarioPath.textContent = `${parsed.demo_dir}\\${parsed.input_name}`;
  els.scenarioText.textContent = "";
  renderParsed(parsed);
  renderGeneratedScenarios();
}

async function runDemo() {
  const selected = await selectedDemoPayload();
  els.runOutput.textContent = `正在运行 Demo：${selected.demo_name}`;
  const run = await api("/api/afsim/run", {
    method: "POST",
    body: JSON.stringify({ ...selected, timeout_seconds: 180 }),
  });
  state.lastRunId = run.run_id;
  els.runOutput.textContent = summarizeRun(run);
}

async function agentTick() {
  els.agentOutput.textContent = "实时指挥 Agent 正在评估态势...";
  const result = await api("/api/agent/tick", {
    method: "POST",
    body: JSON.stringify({
      objective: els.agentObjective.value,
      side: els.agentSide.value,
      autonomy: els.agentAutonomy.value,
      step_seconds: Math.max(0.1, Math.min(300, readNumber(els.agentStepSeconds.value, 5))),
    }),
  });
  els.agentOutput.textContent = summarizeAgent(result);
}

async function applyPendingAgentCommands() {
  if (!state.pendingCommands.length) {
    els.agentOutput.textContent = "暂无可应用的指挥建议。";
    return;
  }
  const result = await api("/api/commands/apply", {
    method: "POST",
    body: JSON.stringify({ commands: state.pendingCommands }),
  });
  els.agentOutput.textContent += `\n\n人工确认后已应用：${result.applied.map((item) => `${item.action}:${item.status}`).join("，")}`;
}

function startAgentLoop() {
  stopAgentLoop();
  agentTick().catch((error) => (els.agentOutput.textContent = `实时指挥失败：${error.message}`));
  state.agentTimer = setInterval(() => {
    agentTick().catch((error) => {
      els.agentOutput.textContent = `实时指挥失败：${error.message}`;
      stopAgentLoop();
    });
  }, 5000);
  els.agentStart.disabled = true;
  els.agentStop.disabled = false;
}

function stopAgentLoop() {
  if (state.agentTimer) clearInterval(state.agentTimer);
  state.agentTimer = null;
  els.agentStart.disabled = false;
  els.agentStop.disabled = true;
}

async function resetAgentSandbox() {
  await api("/api/control", {
    method: "POST",
    body: JSON.stringify({ action: "reset" }),
  });
  state.pendingCommands = [];
  els.agentOutput.textContent = "实时指挥沙盘已重置。";
}

async function analyzeAfsim() {
  els.analysisOutput.textContent = "正在调用大模型分析 AFSIM 运行结果...";
  const result = await api("/api/afsim/analyze", {
    method: "POST",
    body: JSON.stringify({ run_id: state.lastRunId || null, prompt: els.analysisPrompt.value }),
  });
  const analysis = result.analysis || {};
  els.analysisOutput.textContent = [
    `${analysis.source || "analysis"}：${analysis.summary || ""}`,
    `风险等级：${analysis.risk_level || "-"}`,
    `发现：${(analysis.findings || []).join("；") || "-"}`,
    `下一步：${(analysis.next_steps || []).join("；") || "-"}`,
  ].join("\n");
}

function addPlatform() {
  const template = templateById(els.platformTemplate.value);
  state.platforms.push(makePlatformFromTemplate(template, state.platforms.length));
  renderPlatformEditor();
}

function bindEvents() {
  els.platformEditor.addEventListener("input", (event) => {
    const target = event.target;
    const row = target.closest("[data-platform-index]");
    if (!row || !target.dataset.field) return;
    const index = Number(row.dataset.platformIndex);
    const field = target.dataset.field;
    const numericFields = new Set(["lat", "lon", "altitude_m", "speed_kts", "heading_deg", "routeLat", "routeLon"]);
    state.platforms[index][field] = numericFields.has(field) ? (target.value === "" ? "" : Number(target.value)) : target.value;
    const title = row.querySelector(".platform-title strong");
    if (title && field === "name") title.textContent = target.value || `Platform_${index + 1}`;
  });
  els.platformEditor.addEventListener("change", (event) => {
    const target = event.target;
    const row = target.closest("[data-platform-index]");
    if (!row || !target.dataset.field) return;
    state.platforms[Number(row.dataset.platformIndex)][target.dataset.field] = target.value;
  });
  els.platformEditor.addEventListener("click", (event) => {
    const button = event.target.closest("[data-remove-platform]");
    if (!button) return;
    state.platforms.splice(Number(button.dataset.removePlatform), 1);
    renderPlatformEditor();
  });
  els.generatedScenarios.addEventListener("click", (event) => {
    const deleteButton = event.target.closest("[data-delete-scenario]");
    if (deleteButton) {
      deleteScenario(deleteButton.dataset.deleteScenario).catch((error) => (els.runOutput.textContent = `删除失败：${error.message}`));
      return;
    }
    const item = event.target.closest("[data-scenario-id]");
    if (!item) return;
    loadGeneratedDetail(item.dataset.scenarioId).catch((error) => (els.runOutput.textContent = `读取失败：${error.message}`));
  });
  els.addPlatform.addEventListener("click", addPlatform);
  els.saveScenario.addEventListener("click", () => saveScenario().catch((error) => (els.runOutput.textContent = `保存失败：${error.message}`)));
  els.runGenerated.addEventListener("click", () => runGeneratedScenario().catch((error) => (els.runOutput.textContent = `运行失败：${error.message}`)));
  els.refreshScene.addEventListener("click", () => (state.activeScene ? renderScene(state.activeScene) : renderScene({ platforms: [], bounds: null })));
  els.fitScene.addEventListener("click", () => (state.activeScene ? renderScene(state.activeScene) : renderScene({ platforms: [], bounds: null })));
  els.view2d.addEventListener("click", () => setMapMode("2d"));
  els.view3d.addEventListener("click", () => setMapMode("3d"));
  els.viewSplit.addEventListener("click", () => setMapMode("split"));
  els.liveScene.addEventListener("click", toggleRealtimeStream);
  els.agentStep.addEventListener("click", () => agentTick().catch((error) => (els.agentOutput.textContent = `实时指挥失败：${error.message}`)));
  els.agentStart.addEventListener("click", startAgentLoop);
  els.agentStop.addEventListener("click", stopAgentLoop);
  els.agentApply.addEventListener("click", () => applyPendingAgentCommands().catch((error) => (els.agentOutput.textContent = `应用失败：${error.message}`)));
  els.agentReset.addEventListener("click", () => resetAgentSandbox().catch((error) => (els.agentOutput.textContent = `重置失败：${error.message}`)));
  els.previewDemo.addEventListener("click", () => previewDemo().catch((error) => (els.runOutput.textContent = `解析 Demo 失败：${error.message}`)));
  els.runDemo.addEventListener("click", () => runDemo().catch((error) => (els.runOutput.textContent = `运行 Demo 失败：${error.message}`)));
  els.analyzeAfsim.addEventListener("click", () => analyzeAfsim().catch((error) => (els.analysisOutput.textContent = `分析失败：${error.message}`)));
  els.afsimDemo.addEventListener("change", () => previewDemo().catch((error) => (els.runOutput.textContent = `解析 Demo 失败：${error.message}`)));
}

async function boot() {
  assertRequiredElements();
  bindEvents();
  stopAgentLoop();
  renderMapModeButtons();
  renderScene({ platforms: [], bounds: null });
  await loadTemplates();
  await loadHealth().catch((error) => {
    els.systemStatus.textContent = `AFSIM 状态读取失败：${error.message}`;
  });
  const bootResults = await Promise.allSettled([loadDemos(), loadGeneratedScenarios()]);
  const failures = bootResults.filter((item) => item.status === "rejected");
  if (failures.length) {
    els.runOutput.textContent = failures.map((item) => `初始化部分失败：${item.reason?.message || item.reason}`).join("\n");
  }
  window.__AFSIM_LLM_BOOTED = true;
  document.documentElement.dataset.afsimBooted = "true";
  if (!state.activeScenarioId && els.afsimDemo.options.length) {
    previewDemo().catch((error) => {
      els.runOutput.textContent = `初始 Demo 解析失败：${error.message}`;
    });
  }
}

boot().catch((error) => {
  els.systemStatus.textContent = `启动失败：${error.message}`;
});
