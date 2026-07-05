const state = {
  platforms: [
    {
      name: "Blue_1",
      type_name: "WEB_AIRCRAFT",
      side: "blue",
      category: "fighter",
      icon: "F-22",
      lat: 1.05,
      lon: 1.05,
      altitude_m: 9000,
      speed_kts: 420,
      heading_deg: 90,
      routeLat: 1.05,
      routeLon: 1.45,
    },
    {
      name: "Red_1",
      type_name: "WEB_AIRCRAFT",
      side: "red",
      category: "fighter",
      icon: "SU-27",
      lat: 1.25,
      lon: 1.85,
      altitude_m: 8800,
      speed_kts: 430,
      heading_deg: 270,
      routeLat: 1.25,
      routeLon: 1.45,
    },
  ],
  designs: [],
  activeScenarioId: null,
  activeScenarioPath: "",
  lastRunId: null,
  activeDemo: null,
  nativeStreamUrl: "",
};

const els = {
  systemStatus: document.getElementById("systemStatus"),
  missionStatus: document.getElementById("missionStatus"),
  warlockStatus: document.getElementById("warlockStatus"),
  mysticStatus: document.getElementById("mysticStatus"),
  llmStatus: document.getElementById("llmStatus"),
  designName: document.getElementById("designName"),
  designDescription: document.getElementById("designDescription"),
  endTime: document.getElementById("endTime"),
  platformEditor: document.getElementById("platformEditor"),
  addPlatform: document.getElementById("addPlatform"),
  generateScenario: document.getElementById("generateScenario"),
  runGenerated: document.getElementById("runGenerated"),
  launchGeneratedWarlock: document.getElementById("launchGeneratedWarlock"),
  launchMystic: document.getElementById("launchMystic"),
  afsimDemo: document.getElementById("afsimDemo"),
  previewDemo: document.getElementById("previewDemo"),
  runDemo: document.getElementById("runDemo"),
  launchDemoWarlock: document.getElementById("launchDemoWarlock"),
  analysisPrompt: document.getElementById("analysisPrompt"),
  analyzeAfsim: document.getElementById("analyzeAfsim"),
  nativeTool: document.getElementById("nativeTool"),
  refreshNative: document.getElementById("refreshNative"),
  nativeIframe: document.getElementById("nativeIframe"),
  nativeFrame: document.getElementById("nativeFrame"),
  nativeOverlay: document.getElementById("nativeOverlay"),
  activeScenarioLabel: document.getElementById("activeScenarioLabel"),
  scenarioPath: document.getElementById("scenarioPath"),
  scenarioText: document.getElementById("scenarioText"),
  generatedScenarios: document.getElementById("generatedScenarios"),
  scenarioPreview: document.getElementById("scenarioPreview"),
  runOutput: document.getElementById("runOutput"),
  nativeWindows: document.getElementById("nativeWindows"),
  analysisOutput: document.getElementById("analysisOutput"),
};

window.__AFSIM_LLM_BOOTED = false;
window.addEventListener("error", (event) => {
  if (els.systemStatus) els.systemStatus.textContent = `前端错误：${event.message}`;
});

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
    ...options,
  });
  if (!response.ok) {
    const text = await response.text();
    throw new Error(`${response.status} ${response.statusText}: ${text.slice(0, 240)}`);
  }
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

function formatSide(side) {
  return `<span class="side-${escapeHtml(side)}">${escapeHtml(side)}</span>`;
}

function platformTemplate(platform, index) {
  return `
    <div class="platform-row" data-platform-index="${index}">
      <div class="platform-title">
        <strong>${escapeHtml(platform.name || `Platform_${index + 1}`)}</strong>
        <button type="button" data-remove-platform="${index}">删除</button>
      </div>
      <div class="platform-grid">
        <label>名称<input data-field="name" value="${escapeHtml(platform.name)}" /></label>
        <label>阵营
          <select data-field="side">
            ${["blue", "red", "green", "neutral"].map((side) => `<option value="${side}" ${platform.side === side ? "selected" : ""}>${side}</option>`).join("")}
          </select>
        </label>
        <label>AFSIM类型
          <select data-field="type_name">
            <option value="WEB_AIRCRAFT" ${platform.type_name === "WEB_AIRCRAFT" ? "selected" : ""}>WEB_AIRCRAFT</option>
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
  `;
}

function renderPlatformEditor() {
  els.platformEditor.innerHTML = state.platforms.map(platformTemplate).join("");
}

function collectDesign() {
  const endTime = Math.max(1, Math.min(86400, readNumber(els.endTime.value, 600)));
  const platforms = state.platforms.map((platform, index) => {
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
  });
  return {
    name: els.designName.value.trim() || "web_scenario",
    description: els.designDescription.value.trim(),
    end_time_seconds: endTime,
    platforms,
  };
}

function renderParsed(parsed) {
  const platforms = parsed?.platforms || [];
  if (!platforms.length) {
    els.scenarioPreview.innerHTML = `<div class="list-item"><small>暂无平台解析结果</small></div>`;
    return;
  }
  els.scenarioPreview.innerHTML = platforms
    .map((platform) => {
      const pos = platform.position || platform.positions?.[0] || {};
      const coord =
        pos.lat == null
          ? "无坐标"
          : `${Number(pos.lat).toFixed(4)}, ${Number(pos.lon).toFixed(4)}, ${Math.round(pos.alt_m || 0)}m`;
      return `
        <div class="table-row">
          <strong>${escapeHtml(platform.id)}</strong>
          <span>${formatSide(platform.side)}</span>
          <span>${escapeHtml(platform.category || platform.icon || platform.type)}</span>
          <small>${escapeHtml(coord)}</small>
        </div>
      `;
    })
    .join("");
}

function renderGeneratedScenarios() {
  if (!state.designs.length) {
    els.generatedScenarios.innerHTML = `<div class="list-item"><small>暂无生成场景</small></div>`;
    return;
  }
  els.generatedScenarios.innerHTML = state.designs
    .map(
      (item) => `
        <div class="list-item ${item.scenario_id === state.activeScenarioId ? "active" : ""}" data-scenario-id="${escapeHtml(item.scenario_id)}">
          <strong>${escapeHtml(item.name)}</strong>
          <small>${escapeHtml(item.scenario_id)}</small>
          <small>平台 ${item.platform_count || 0} | ${item.end_time_seconds || "-"}s</small>
        </div>
      `,
    )
    .join("");
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

async function loadHealth() {
  const health = await api("/api/health");
  const afsim = health.afsim || {};
  setStatus(els.missionStatus, afsim.mission_exists, afsim.mission_exists ? "mission OK" : "mission 缺失");
  setStatus(els.warlockStatus, afsim.warlock_exists, afsim.warlock_exists ? "Warlock OK" : "Warlock 缺失");
  setStatus(els.mysticStatus, afsim.mystic_exists, afsim.mystic_exists ? "Mystic OK" : "Mystic 缺失");
  setStatus(els.llmStatus, true, "LLM 已配置");
  els.systemStatus.textContent = `AFSIM：${afsim.root || "-"}`;
}

async function loadDemos() {
  const demos = await api("/api/afsim/demos");
  els.afsimDemo.innerHTML = demos
    .map((demo) => `<option value="${escapeHtml(demo.name)}" data-input="${escapeHtml(demo.default_input)}">${escapeHtml(demo.name)} / ${escapeHtml(demo.default_input)}</option>`)
    .join("");
  state.activeDemo = demos[0] || null;
  if (demos.length) await previewDemo();
}

async function loadGeneratedScenarios() {
  state.designs = await api("/api/afsim/designs");
  renderGeneratedScenarios();
  if (!state.activeScenarioId && state.designs.length) {
    await loadGeneratedDetail(state.designs[0].scenario_id);
  }
}

async function loadGeneratedDetail(scenarioId) {
  const detail = await api(`/api/afsim/designs/${encodeURIComponent(scenarioId)}`);
  state.activeScenarioId = detail.scenario_id;
  state.activeScenarioPath = detail.scenario_path;
  els.activeScenarioLabel.textContent = `${detail.design?.name || detail.scenario_id} | ${detail.input_file}`;
  els.scenarioPath.textContent = detail.scenario_path;
  els.scenarioText.textContent = detail.scenario_text || "";
  renderParsed(detail.parsed);
  renderGeneratedScenarios();
  return detail;
}

async function createGeneratedScenario() {
  els.runOutput.textContent = "正在生成AFSIM场景...";
  const result = await api("/api/afsim/designs", {
    method: "POST",
    body: JSON.stringify(collectDesign()),
  });
  state.activeScenarioId = result.scenario_id;
  state.activeScenarioPath = result.scenario_path;
  await loadGeneratedScenarios();
  await loadGeneratedDetail(result.scenario_id);
  els.runOutput.textContent = `已生成：${result.scenario_path}`;
  return result.scenario_id;
}

async function ensureActiveScenario() {
  if (state.activeScenarioId) return state.activeScenarioId;
  return createGeneratedScenario();
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
  await refreshNativeStatus();
  return run;
}

async function launchGeneratedWarlock() {
  const scenarioId = await ensureActiveScenario();
  els.runOutput.textContent = `正在启动 Warlock：${scenarioId}`;
  const result = await api(`/api/afsim/designs/${encodeURIComponent(scenarioId)}/launch-map`, {
    method: "POST",
    body: "{}",
  });
  els.runOutput.textContent = `Warlock 已启动，PID=${result.pid}\n场景：${result.scenario_path}`;
  setTimeout(refreshNativeStatus, 1200);
  setTimeout(refreshNativeFrame, 1600);
}

async function launchMystic() {
  if (!state.lastRunId) {
    await runGeneratedScenario();
  }
  els.runOutput.textContent = "正在启动 Mystic 回放...";
  const result = await api("/api/afsim/launch-3d", {
    method: "POST",
    body: JSON.stringify({ run_id: state.lastRunId || null }),
  });
  els.runOutput.textContent = `Mystic 已启动，PID=${result.pid}\nAER=${result.aer_path}`;
  els.nativeTool.value = "Mystic";
  setTimeout(refreshNativeStatus, 1200);
  setTimeout(refreshNativeFrame, 1600);
}

async function selectedDemoPayload() {
  const selected = els.afsimDemo.options[els.afsimDemo.selectedIndex];
  if (!selected) throw new Error("没有可用AFSIM demo");
  return { demo_name: selected.value, input_file: selected.dataset.input };
}

async function previewDemo() {
  const selected = await selectedDemoPayload();
  const parsed = await api(
    `/api/afsim/scenario?demo_name=${encodeURIComponent(selected.demo_name)}&input_file=${encodeURIComponent(selected.input_file)}`,
  );
  state.activeDemo = selected;
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

async function launchDemoWarlock() {
  const selected = await selectedDemoPayload();
  els.runOutput.textContent = `正在启动 Warlock Demo：${selected.demo_name}`;
  const result = await api("/api/afsim/launch-map", {
    method: "POST",
    body: JSON.stringify(selected),
  });
  els.runOutput.textContent = `Warlock 已启动，PID=${result.pid}\nDemo：${result.demo_name}/${result.input_file}`;
  setTimeout(refreshNativeStatus, 1200);
  setTimeout(refreshNativeFrame, 1600);
}

async function analyzeAfsim() {
  els.analysisOutput.textContent = "正在调用大模型分析AFSIM运行结果...";
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

function refreshNativeFrame() {
  if (state.nativeStreamUrl) return;
  const tool = els.nativeTool.value || "Warlock";
  els.nativeFrame.src = `/api/afsim/native-frame.jpg?title=${encodeURIComponent(tool)}&t=${Date.now()}`;
}

function renderNativeWindows(status) {
  const warlock = status.windows?.warlock || [];
  const mystic = status.windows?.mystic || [];
  const rows = [
    ...warlock.map((window) => ({ ...window, tool: "Warlock" })),
    ...mystic.map((window) => ({ ...window, tool: "Mystic" })),
  ];
  if (!rows.length) {
    els.nativeWindows.innerHTML = `<div class="list-item"><small>未检测到 Warlock / Mystic 窗口</small></div>`;
    return;
  }
  els.nativeWindows.innerHTML = rows
    .map(
      (window) => `
        <div class="list-item">
          <strong>${escapeHtml(window.tool)}</strong>
          <small>${escapeHtml(window.title)}</small>
          <small>${window.width} x ${window.height}</small>
        </div>
      `,
    )
    .join("");
}

async function refreshNativeStatus() {
  const status = await api("/api/afsim/native-display");
  state.nativeStreamUrl = status.stream_url || "";
  renderNativeWindows(status);
  if (state.nativeStreamUrl) {
    els.nativeIframe.hidden = false;
    els.nativeFrame.hidden = true;
    els.nativeIframe.src = state.nativeStreamUrl;
    els.nativeOverlay.textContent = "AFSIM原生交互流";
  } else {
    els.nativeIframe.hidden = true;
    els.nativeFrame.hidden = false;
    els.nativeOverlay.textContent = status.capture_available ? "本机Warlock/Mystic窗口捕获" : "当前Python环境不支持窗口捕获";
    refreshNativeFrame();
  }
}

function addPlatform() {
  const index = state.platforms.length + 1;
  state.platforms.push({
    name: `Blue_${index}`,
    type_name: "WEB_AIRCRAFT",
    side: "blue",
    category: "fighter",
    icon: "F-22",
    lat: 1.0,
    lon: 1.0 + index * 0.2,
    altitude_m: 9000,
    speed_kts: 400,
    heading_deg: 90,
    routeLat: 1.0,
    routeLon: 1.4 + index * 0.2,
  });
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
    const index = Number(button.dataset.removePlatform);
    state.platforms.splice(index, 1);
    renderPlatformEditor();
  });
  els.generatedScenarios.addEventListener("click", async (event) => {
    const item = event.target.closest("[data-scenario-id]");
    if (!item) return;
    try {
      await loadGeneratedDetail(item.dataset.scenarioId);
    } catch (error) {
      els.runOutput.textContent = `读取生成场景失败：${error.message}`;
    }
  });
  els.addPlatform.addEventListener("click", addPlatform);
  els.generateScenario.addEventListener("click", () => createGeneratedScenario().catch((error) => (els.runOutput.textContent = `生成失败：${error.message}`)));
  els.runGenerated.addEventListener("click", () => runGeneratedScenario().catch((error) => (els.runOutput.textContent = `运行失败：${error.message}`)));
  els.launchGeneratedWarlock.addEventListener("click", () => launchGeneratedWarlock().catch((error) => (els.runOutput.textContent = `启动Warlock失败：${error.message}`)));
  els.launchMystic.addEventListener("click", () => launchMystic().catch((error) => (els.runOutput.textContent = `启动Mystic失败：${error.message}`)));
  els.previewDemo.addEventListener("click", () => previewDemo().catch((error) => (els.runOutput.textContent = `解析Demo失败：${error.message}`)));
  els.runDemo.addEventListener("click", () => runDemo().catch((error) => (els.runOutput.textContent = `运行Demo失败：${error.message}`)));
  els.launchDemoWarlock.addEventListener("click", () => launchDemoWarlock().catch((error) => (els.runOutput.textContent = `启动Demo失败：${error.message}`)));
  els.analyzeAfsim.addEventListener("click", () => analyzeAfsim().catch((error) => (els.analysisOutput.textContent = `分析失败：${error.message}`)));
  els.refreshNative.addEventListener("click", () => refreshNativeStatus().catch((error) => (els.nativeOverlay.textContent = `刷新失败：${error.message}`)));
  els.nativeTool.addEventListener("change", refreshNativeFrame);
  els.afsimDemo.addEventListener("change", () => previewDemo().catch((error) => (els.runOutput.textContent = `解析Demo失败：${error.message}`)));
}

async function boot() {
  bindEvents();
  renderPlatformEditor();
  await loadHealth();
  await Promise.all([loadDemos(), loadGeneratedScenarios(), refreshNativeStatus()]);
  setInterval(refreshNativeFrame, 2500);
  setInterval(() => refreshNativeStatus().catch(() => {}), 7000);
  window.__AFSIM_LLM_BOOTED = true;
}

boot().catch((error) => {
  els.systemStatus.textContent = `启动失败：${error.message}`;
});
