import { disposeOperationalMap, renderOperationalMap, sideColor } from "./map_renderer.js";

const MODE_LABELS = {
  design_preview: "设计预览",
  afsim_running: "AFSIM 运行中",
  replay: "AFSIM 复盘",
  no_frames: "无可用帧",
};

const state = {
  health: null,
  demos: [],
  designs: [],
  workbench: null,
  activeDemo: null,
  activeScenarioId: null,
  dataMode: "design_preview",
  currentFrame: null,
  selectedId: null,
  selectedEventId: null,
  mapView: null,
  previewSocket: null,
  jobSocket: null,
  pollTimer: null,
  replayTimer: null,
  activeJobId: null,
  activeJob: null,
  replayLoadedForJob: null,
  lastRun: null,
  outputFiles: [],
  logLines: [],
  diagnostics: [],
  playbackSpeed: 1,
};

const els = {
  systemStatus: document.getElementById("systemStatus"),
  currentScenario: document.getElementById("currentScenario"),
  afsimRoot: document.getElementById("afsimRoot"),
  missionExeStatus: document.getElementById("missionExeStatus"),
  runStatus: document.getElementById("runStatus"),
  runId: document.getElementById("runId"),
  returnCode: document.getElementById("returnCode"),
  sourceMode: document.getElementById("sourceMode"),
  simTime: document.getElementById("simTime"),
  frameCount: document.getElementById("frameCount"),
  eventCount: document.getElementById("eventCount"),
  basemapStatus: document.getElementById("basemapStatus"),
  demoSelect: document.getElementById("demoSelect"),
  generatedSelect: document.getElementById("generatedSelect"),
  timeoutInput: document.getElementById("timeoutInput"),
  reloadWorkbenchBtn: document.getElementById("reloadWorkbenchBtn"),
  runDemoBtn: document.getElementById("runDemoBtn"),
  runGeneratedBtn: document.getElementById("runGeneratedBtn"),
  refreshJobBtn: document.getElementById("refreshJobBtn"),
  previewStreamBtn: document.getElementById("previewStreamBtn"),
  playReplayBtn: document.getElementById("playReplayBtn"),
  stopPlaybackBtn: document.getElementById("stopPlaybackBtn"),
  layerList: document.getElementById("layerList"),
  mapTitle: document.getElementById("mapTitle"),
  mapSubtitle: document.getElementById("mapSubtitle"),
  fitMapBtn: document.getElementById("fitMapBtn"),
  refreshReplayBtn: document.getElementById("refreshReplayBtn"),
  sceneView: document.getElementById("sceneView"),
  mapHud: document.getElementById("mapHud"),
  hudSource: document.getElementById("hudSource"),
  hudTime: document.getElementById("hudTime"),
  hudEntities: document.getElementById("hudEntities"),
  hudEvents: document.getElementById("hudEvents"),
  noFramesNotice: document.getElementById("noFramesNotice"),
  timelineRange: document.getElementById("timelineRange"),
  timeLabel: document.getElementById("timeLabel"),
  timelineEnd: document.getElementById("timelineEnd"),
  targetCount: document.getElementById("targetCount"),
  targetList: document.getElementById("targetList"),
  targetDetails: document.getElementById("targetDetails"),
  eventSummary: document.getElementById("eventSummary"),
  eventList: document.getElementById("eventList"),
  chainList: document.getElementById("chainList"),
  missionLog: document.getElementById("missionLog"),
  outputFiles: document.getElementById("outputFiles"),
  diagnostics: document.getElementById("diagnostics"),
};

function assertElements() {
  const missing = Object.entries(els).filter(([, element]) => !element).map(([key]) => key);
  if (missing.length) throw new Error(`页面缺少元素: ${missing.join(", ")}`);
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
    cache: "no-store",
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  if (!response.ok) {
    const text = await response.text();
    throw new Error(`${response.status} ${response.statusText}: ${text.slice(0, 400)}`);
  }
  if (response.status === 204) return null;
  return response.json();
}

function timeoutSeconds() {
  const value = Number(els.timeoutInput.value || 180);
  return Math.max(5, Math.min(1800, Math.round(Number.isFinite(value) ? value : 180)));
}

function clamp(value, min, max) {
  return Math.max(min, Math.min(max, value));
}

function formatSeconds(value) {
  const seconds = Number(value || 0);
  if (!Number.isFinite(seconds)) return "0.0s";
  if (seconds < 60) return `${seconds.toFixed(1)}s`;
  const minutes = Math.floor(seconds / 60);
  return `${minutes}m ${(seconds % 60).toFixed(0)}s`;
}

function scenarioLabel() {
  const source = state.workbench?.source || {};
  if (source.kind === "generated") return `${source.name || source.scenario_id} (${source.input_file || "scenario"})`;
  if (source.kind === "demo") return `${source.demo_name || "demo"} / ${source.input_file || ""}`;
  return "未加载";
}

function replayFrames() {
  const frames = state.workbench?.replay?.frames || [];
  return Array.isArray(frames) ? frames : [];
}

function replayEvents() {
  const events = state.workbench?.events || state.workbench?.replay?.events || [];
  return Array.isArray(events) ? events : [];
}

function timelineEnd() {
  const summaryEnd = Number(state.workbench?.replay?.summary?.timeline?.end || 0);
  const simEnd = Number(state.workbench?.simulation_time?.end || 0);
  const eventEnd = Math.max(0, ...replayEvents().map((event) => Number(event.time || 0)).filter(Number.isFinite));
  return Math.max(1, summaryEnd, simEnd, eventEnd, 600);
}

function currentSimTime() {
  return Number(state.currentFrame?.sim_time ?? state.workbench?.simulation_time?.current ?? els.timelineRange.value ?? 0);
}

function setDataMode(mode) {
  state.dataMode = mode;
  renderStatus();
}

function selectedDemoOption() {
  return els.demoSelect.options[els.demoSelect.selectedIndex] || null;
}

function selectedGeneratedId() {
  return els.generatedSelect.value || "";
}

function mapResourceState() {
  const rasters = state.workbench?.map_resources?.raster_layers || [];
  const vectors = state.workbench?.map_resources?.vector_layers || [];
  const rasterReady = rasters.some((item) => ["bluemarble", "naturalearth"].includes(item.id) && item.exists);
  const vectorReady = vectors.some((item) => ["coastline", "pol"].includes(item.id) && item.exists);
  if (rasterReady && vectorReady) return { label: "AFSIM 底图", kind: "good" };
  if (rasterReady) return { label: "AFSIM 栅格 / 矢量 fallback", kind: "warn" };
  return { label: "底图 fallback", kind: "bad" };
}

function setCardState(id, kind) {
  const card = document.getElementById(id)?.closest(".status-card");
  if (!card) return;
  card.classList.toggle("good", kind === "good");
  card.classList.toggle("warn", kind === "warn");
  card.classList.toggle("bad", kind === "bad");
  card.classList.toggle("running", kind === "running");
}

function renderStatus() {
  const afsim = state.health?.afsim || {};
  const replay = state.workbench?.replay || {};
  const frameCount = Number(replay.summary?.frame_count ?? replayFrames().length ?? 0);
  const eventCount = replayEvents().length || Number(replay.summary?.event_count || 0);
  const run = state.activeJob?.run || state.lastRun || replay.run || {};
  const runId = state.activeJobId || run.run_id || replay.summary?.run_id || "-";
  const returnCode = run.returncode ?? "-";
  const basemap = mapResourceState();
  const missionOk = Boolean(afsim.mission_exists);
  const status = state.activeJob?.status || (run.run_id ? (run.returncode === 0 ? "finished" : "failed") : "idle");

  els.currentScenario.textContent = scenarioLabel();
  els.afsimRoot.textContent = afsim.root || "-";
  els.missionExeStatus.textContent = missionOk ? "可用" : "缺失";
  els.runStatus.textContent = status;
  els.runId.textContent = runId;
  els.returnCode.textContent = String(returnCode);
  els.sourceMode.textContent = MODE_LABELS[state.dataMode] || state.dataMode;
  els.simTime.textContent = `T+${currentSimTime().toFixed(1)}s`;
  els.frameCount.textContent = String(frameCount);
  els.eventCount.textContent = String(eventCount);
  els.basemapStatus.textContent = basemap.label;

  setCardState("missionExeStatus", missionOk ? "good" : "bad");
  setCardState("runStatus", status === "running" || status === "queued" ? "running" : status === "failed" ? "bad" : "good");
  setCardState("returnCode", returnCode === "-" ? "" : Number(returnCode) === 0 ? "good" : "bad");
  setCardState("sourceMode", state.dataMode === "afsim_running" ? "running" : state.dataMode === "no_frames" ? "warn" : "good");
  setCardState("basemapStatus", basemap.kind);

  els.systemStatus.textContent = missionOk
    ? "mission.exe 为权威仿真源；页面显示 job 输出和 replay 帧。"
    : "未找到 mission.exe；只能进行设计预览和已有 replay 查看。";
}

function renderHud() {
  const frame = state.currentFrame;
  const eventCount = replayEvents().length;
  els.hudSource.textContent = `source: ${frame?.source || MODE_LABELS[state.dataMode] || "none"}`;
  els.hudTime.textContent = `sim_time: ${currentSimTime().toFixed(1)}`;
  els.hudEntities.textContent = `entity_count: ${frame?.entity_count ?? targetEntities().length}`;
  els.hudEvents.textContent = `event_count: ${eventCount}`;
}

function targetEntities() {
  const base = new Map((state.workbench?.platforms || []).map((platform) => [platform.id, normalizeEntity(platform)]));
  for (const entity of state.currentFrame?.entities || []) {
    const previous = base.get(entity.id) || {};
    base.set(entity.id, normalizeEntity(entity, previous));
  }
  return [...base.values()];
}

function normalizeEntity(entity, fallback = {}) {
  const position = entity.position || entity;
  return {
    ...fallback,
    ...entity,
    id: String(entity.id || fallback.id || ""),
    name: entity.name || fallback.name || entity.id || fallback.id || "",
    side: entity.side || fallback.side || "neutral",
    kind: entity.kind || fallback.kind || entity.category || fallback.category || "aircraft",
    lat: Number(position.lat ?? entity.lat ?? fallback.lat ?? 0),
    lon: Number(position.lon ?? entity.lon ?? fallback.lon ?? 0),
    alt_m: Number(position.alt_m ?? entity.alt_m ?? fallback.alt_m ?? 0),
    heading_deg: Number(entity.heading_deg ?? fallback.heading_deg ?? 0),
    speed_kts: Number(entity.speed_kts ?? fallback.speed_kts ?? 0),
    route: entity.route || fallback.route || [],
  };
}

function renderMap() {
  if (!state.workbench) {
    disposeOperationalMap();
    els.sceneView.innerHTML = `<div class="empty-row">等待 workbench 数据...</div>`;
    return;
  }
  renderOperationalMap(els.sceneView, state.workbench, {
    frame: state.currentFrame,
    selectedId: state.selectedId,
    selectedEventId: state.selectedEventId,
    viewState: state.mapView,
  });
  renderHud();
}

function renderLayers() {
  const preferred = [
    "base.imagery",
    "base.coastline",
    "base.admin_boundaries",
    "base.latlon_grid",
    "dynamic.history_tracks",
    "dynamic.velocity_vectors",
    "dynamic.target_labels",
    "electromagnetic.radar_ranges",
    "electromagnetic.comm_links",
    "dynamic.strike_ranges",
    "intelligence.detections",
    "replay.event_markers",
  ];
  const layers = state.workbench?.layers || [];
  const shown = preferred.map((id) => layers.find((layer) => layer.id === id)).filter(Boolean);
  if (!shown.length) {
    els.layerList.innerHTML = `<div class="empty-row">等待图层目录...</div>`;
    return;
  }
  els.layerList.innerHTML = shown.map((layer) => `
    <label class="layer-row" data-layer-id="${escapeHtml(layer.id)}">
      <input type="checkbox" ${layer.visible !== false ? "checked" : ""} />
      <strong title="${escapeHtml(layer.id)}">${escapeHtml(layer.name || layer.id)}</strong>
    </label>
  `).join("");
}

function renderTargets() {
  const targets = targetEntities();
  els.targetCount.textContent = String(targets.length);
  if (!targets.length) {
    els.targetList.innerHTML = `<div class="empty-row">无目标。</div>`;
    renderTargetDetails(null);
    return;
  }
  if (!state.selectedId || !targets.some((item) => item.id === state.selectedId)) {
    state.selectedId = targets[0].id;
  }
  els.targetList.innerHTML = targets.slice(0, 160).map((target) => `
    <div class="target-row ${target.id === state.selectedId ? "active" : ""}" data-target-id="${escapeHtml(target.id)}">
      <strong>${escapeHtml(target.name || target.id)}</strong>
      <small>
        <span style="color:${sideColor(target.side)}">${escapeHtml(target.side)}</span>
        ${escapeHtml(target.kind)} | ${target.lat.toFixed(4)}, ${target.lon.toFixed(4)} | ${Math.round(target.alt_m)}m
      </small>
    </div>
  `).join("");
  renderTargetDetails(targets.find((item) => item.id === state.selectedId) || targets[0]);
}

function renderTargetDetails(target) {
  if (!target) {
    els.targetDetails.innerHTML = `<span>状态</span><strong>未选择</strong>`;
    return;
  }
  const rows = [
    ["ID", target.id],
    ["名称", target.name || target.id],
    ["阵营", target.side],
    ["类型", `${target.kind || "-"} / ${target.category || target.type || "-"}`],
    ["位置", `${target.lat.toFixed(5)}, ${target.lon.toFixed(5)}`],
    ["高度", `${Math.round(target.alt_m || 0)} m`],
    ["航向", `${Number(target.heading_deg || 0).toFixed(1)} deg`],
    ["速度", `${Number(target.speed_kts || 0).toFixed(1)} kts`],
    ["来源", target.source || target.afsim?.source_ref?.file || "-"],
  ];
  els.targetDetails.innerHTML = rows.map(([key, value]) => `<span>${escapeHtml(key)}</span><strong title="${escapeHtml(value)}">${escapeHtml(value)}</strong>`).join("");
}

function renderEvents() {
  const events = replayEvents().slice().sort((a, b) => Number(a.time || 0) - Number(b.time || 0));
  els.eventSummary.textContent = String(events.length);
  if (!events.length) {
    els.eventList.innerHTML = `<div class="empty-row">暂无 AFSIM 事件。</div>`;
    return;
  }
  els.eventList.innerHTML = events.slice(0, 240).map((event, index) => `
    <div class="event-row ${event.id === state.selectedEventId ? "active" : ""}" data-event-index="${index}">
      <strong>T+${Number(event.time || 0).toFixed(2)} ${escapeHtml(event.title || event.type || "event")}</strong>
      <small>${escapeHtml(event.source || event.kind || "")} | ${escapeHtml(event.platform_id || event.target_id || event.detector_id || "-")}</small>
    </div>
  `).join("");
}

function renderChains() {
  const detections = state.workbench?.detections || [];
  const communications = state.workbench?.communications || [];
  const weapons = state.workbench?.weapons || [];
  const rows = [
    ...detections.slice(0, 30).map((item) => ({
      title: `探测 ${item.detector_id} -> ${item.target_id}`,
      detail: `${item.range_km} km | q=${item.quality ?? "-"}`,
    })),
    ...communications.slice(0, 30).map((item) => ({
      title: `通信 ${item.source_id} -> ${item.target_id}`,
      detail: `${item.status || "-"} | ${item.range_km ?? "-"} km`,
    })),
    ...weapons.slice(0, 30).map((item) => ({
      title: `武器 ${item.platform_id} / ${item.type}`,
      detail: `${item.name || item.id} | ${item.range_km ?? "-"} km`,
    })),
  ];
  els.chainList.innerHTML = rows.length
    ? rows.map((row) => `<div class="chain-row"><strong>${escapeHtml(row.title)}</strong><small>${escapeHtml(row.detail)}</small></div>`).join("")
    : `<div class="empty-row">暂无链路数据。</div>`;
}

function renderTimeline() {
  const end = timelineEnd();
  const current = currentSimTime();
  els.timelineRange.max = String(end);
  els.timelineRange.value = String(clamp(current, 0, end));
  els.timelineEnd.textContent = `${end.toFixed(1)}s`;
  els.timeLabel.textContent = `T+${current.toFixed(1)}s`;
}

function renderNoFramesNotice() {
  const replay = state.workbench?.replay;
  const frames = replayFrames();
  if (state.dataMode !== "no_frames" || frames.length) {
    els.noFramesNotice.classList.add("hidden");
    els.noFramesNotice.textContent = "";
    return;
  }
  const files = (state.outputFiles.length ? state.outputFiles : replay?.run?.files || []).map((file) => file.name || file.path).filter(Boolean);
  const sourceFiles = (replay?.source_files || []).map((file) => `${file.name}: ${file.format || "unknown"} ${file.parsed_records || 0} records`);
  const reason = [
    "本次 AFSIM 输出未产生可回放坐标帧。",
    "原因：replay 解析器目前只能从 event_output 文本或 CSV 中提取坐标帧；AER/DIS 二进制会作为源文件索引，但不会伪造动态。",
    files.length ? `输出文件：${files.slice(0, 12).join(", ")}` : "输出文件：无",
    sourceFiles.length ? `已解析源：${sourceFiles.slice(0, 8).join("; ")}` : "已解析源：无可提取坐标的 evt/csv",
  ].join("\n");
  els.noFramesNotice.textContent = reason;
  els.noFramesNotice.classList.remove("hidden");
}

function renderLogs() {
  els.missionLog.textContent = state.logLines.length ? state.logLines.slice(-80).join("\n") : "等待 AFSIM 作业...";
}

function renderFiles() {
  const files = state.outputFiles || [];
  if (!files.length) {
    els.outputFiles.textContent = "等待输出文件...";
    return;
  }
  els.outputFiles.innerHTML = files.slice(-80).map((file) => {
    const name = file.name || file.path || file.source || "output";
    const sizeKb = Math.round(Number(file.size || 0) / 1024);
    return `<div class="file-row"><span title="${escapeHtml(file.path || file.source || "")}">${escapeHtml(name)}</span><span>${sizeKb} KB</span></div>`;
  }).join("");
}

function diagnosticsFromRun(run) {
  const summary = run?.summary || {};
  const diagnostics = [];
  if (run?.returncode != null && run.returncode !== 0) diagnostics.push(`RETURNCODE mission.exe 返回 ${run.returncode}`);
  if (run?.timed_out) diagnostics.push("TIMEOUT mission.exe 超时终止");
  for (const line of summary.fatal || []) diagnostics.push(`FATAL ${line}`);
  for (const line of summary.warnings || []) diagnostics.push(`WARN ${line}`);
  const stderr = String(run?.stderr || "").trim();
  if (stderr) diagnostics.push(`STDERR ${stderr.slice(-1500)}`);
  return diagnostics;
}

function renderDiagnostics() {
  const diagnostics = [
    ...state.diagnostics,
    ...diagnosticsFromRun(state.lastRun || state.activeJob?.run || state.workbench?.replay?.run),
  ];
  els.diagnostics.textContent = diagnostics.length ? [...new Set(diagnostics)].slice(0, 40).join("\n") : "暂无诊断。";
}

function renderAll() {
  renderStatus();
  renderHud();
  renderLayers();
  renderTargets();
  renderEvents();
  renderChains();
  renderTimeline();
  renderNoFramesNotice();
  renderLogs();
  renderFiles();
  renderDiagnostics();
  renderMap();
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

async function loadHealth() {
  state.health = await api("/api/health");
  renderStatus();
}

async function loadDemos() {
  state.demos = await api("/api/afsim/demos");
  els.demoSelect.innerHTML = state.demos.map((demo) => `
    <option value="${escapeHtml(demo.name)}" data-input="${escapeHtml(demo.default_input || "")}">
      ${escapeHtml(demo.name)} / ${escapeHtml(demo.default_input || "")}
    </option>
  `).join("");
  const simple = [...els.demoSelect.options].find((option) => option.value === "simple_scenario");
  if (simple) els.demoSelect.value = simple.value;
}

async function loadDesigns() {
  state.designs = await api("/api/afsim/designs");
  els.generatedSelect.innerHTML = `<option value="">未选择</option>` + state.designs.map((item) => `
    <option value="${escapeHtml(item.scenario_id)}">${escapeHtml(item.name || item.scenario_id)}</option>
  `).join("");
}

async function loadWorkbench(params = sourceParams()) {
  stopPreviewStream();
  stopReplayPlayback();
  state.currentFrame = null;
  state.selectedEventId = null;
  state.mapView = null;
  state.workbench = await api(`/api/afsim/workbench?${params.toString()}`);
  state.outputFiles = state.workbench?.replay?.run?.files || state.outputFiles || [];
  state.lastRun = state.workbench?.replay?.run || state.lastRun;
  state.logLines = [];
  state.diagnostics = [];
  setDataMode("design_preview");
  if (state.workbench?.platforms?.[0]) state.selectedId = state.workbench.platforms[0].id;
  els.mapTitle.textContent = "主态势地图";
  els.mapSubtitle.textContent = scenarioLabel();
  renderAll();
}

async function loadDemoWorkbench() {
  const option = selectedDemoOption();
  if (!option) return;
  state.activeScenarioId = null;
  state.activeDemo = { demo_name: option.value, input_file: option.dataset.input || null };
  const params = new URLSearchParams({ demo_name: option.value });
  if (option.dataset.input) params.set("input_file", option.dataset.input);
  await loadWorkbench(params);
}

async function loadGeneratedWorkbench() {
  const scenarioId = selectedGeneratedId();
  if (!scenarioId) return;
  state.activeScenarioId = scenarioId;
  state.activeDemo = null;
  await loadWorkbench(new URLSearchParams({ scenario_id: scenarioId }));
}

function blendEntity(before, after, ratio) {
  if (!before) return normalizeEntity(after);
  if (!after) return normalizeEntity(before);
  const lat = Number(before.lat) + (Number(after.lat) - Number(before.lat)) * ratio;
  const lon = Number(before.lon) + (Number(after.lon) - Number(before.lon)) * ratio;
  const alt = Number(before.alt_m || 0) + (Number(after.alt_m || 0) - Number(before.alt_m || 0)) * ratio;
  return normalizeEntity({
    ...before,
    ...after,
    lat,
    lon,
    alt_m: alt,
    heading_deg: Math.abs(lat - Number(before.lat)) > 1e-8 || Math.abs(lon - Number(before.lon)) > 1e-8
      ? bearingDeg(before, { lat, lon })
      : Number(after.heading_deg ?? before.heading_deg ?? 0),
  });
}

function bearingDeg(a, b) {
  const lat1 = Number(a.lat) * Math.PI / 180;
  const lat2 = Number(b.lat) * Math.PI / 180;
  const dLon = (Number(b.lon) - Number(a.lon)) * Math.PI / 180;
  const y = Math.sin(dLon) * Math.cos(lat2);
  const x = Math.cos(lat1) * Math.sin(lat2) - Math.sin(lat1) * Math.cos(lat2) * Math.cos(dLon);
  return ((Math.atan2(y, x) * 180 / Math.PI) + 360) % 360;
}

function replayFrameAt(timeValue) {
  const frames = replayFrames().slice().sort((a, b) => Number(a.sim_time || 0) - Number(b.sim_time || 0));
  if (!frames.length) return null;
  const time = Number(timeValue);
  if (!Number.isFinite(time) || time <= Number(frames[0].sim_time || 0)) return frames[0];
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
      source: `${after.source || "afsim-run-replay"}/interpolated`,
      sim_time: time,
      entity_count: entities.length,
      entities,
      events: [...new Set([...(before.events || []), ...(after.events || [])])].slice(-60),
    };
  }
  return last;
}

function applyReplayTime(timeValue) {
  if (!state.workbench?.simulation_time) return false;
  state.workbench.simulation_time.current = Number(timeValue || 0);
  const frame = replayFrameAt(timeValue);
  if (!frame) {
    state.currentFrame = null;
    if (state.dataMode !== "afsim_running") setDataMode("no_frames");
    renderAll();
    return false;
  }
  state.currentFrame = frame;
  if (state.dataMode !== "afsim_running") setDataMode("replay");
  renderAll();
  return true;
}

function applyReplay(replay, options = {}) {
  if (!state.workbench) return;
  state.workbench.replay = replay;
  state.lastRun = replay.run || state.lastRun;
  state.outputFiles = replay.run?.files || state.outputFiles || [];
  const runId = replay.summary?.run_id;
  const baseEvents = (state.workbench.events || []).filter((event) => !event.run_id || event.run_id !== runId);
  state.workbench.events = [...baseEvents, ...(replay.events || [])].sort((a, b) => Number(a.time || 0) - Number(b.time || 0));
  state.workbench.tracks = [
    ...(state.workbench.tracks || []).filter((track) => !String(track.id || "").startsWith("replay_trk_")),
    ...(replay.tracks || []),
  ];
  if (replay.bounds) state.workbench.bounds = replay.bounds;
  if (state.workbench.simulation_time) {
    state.workbench.simulation_time.current = 0;
    state.workbench.simulation_time.end = timelineEnd();
  }
  const frames = replay.frames || [];
  if (frames.length) {
    state.currentFrame = frames[0];
    state.selectedId = frames[0].entities?.[0]?.id || state.selectedId;
    setDataMode("replay");
  } else {
    state.currentFrame = null;
    setDataMode(options.strict ? "no_frames" : "design_preview");
  }
  state.diagnostics = [];
  if (!frames.length && options.strict) {
    state.diagnostics.push("本次 AFSIM 输出没有可回放坐标帧；地图保持静态解析态势，不生成假动态。");
  }
  renderAll();
}

function summarizeProgress(job, event) {
  const progress = event?.progress || job?.progress || {};
  const lines = [];
  lines.push(`status=${job?.status || "-"} phase=${event?.phase || job?.phase || "-"}`);
  if (progress.working_dir) lines.push(`working_dir=${progress.working_dir}`);
  if (progress.run_dir) lines.push(`run_dir=${progress.run_dir}`);
  if (progress.output_dir) lines.push(`output_dir=${progress.output_dir}`);
  if (progress.command) lines.push(`command=${Array.isArray(progress.command) ? progress.command.join(" ") : progress.command}`);
  if (job?.error) lines.push(`error=${job.error}`);
  return lines;
}

function updateJobState(job, event = null) {
  if (!job) return;
  state.activeJob = job;
  state.activeJobId = job.job_id || state.activeJobId;
  const progress = event?.progress || job.progress || {};
  const run = event?.run || job.run || null;
  if (run) state.lastRun = run;
  const files = run?.files || progress.files;
  if (Array.isArray(files)) state.outputFiles = files;
  const tail = progress.tail || run?.summary?.tail || [];
  if (Array.isArray(tail) && tail.length) state.logLines = tail;
  if (job.status === "queued" || job.status === "running") setDataMode("afsim_running");
  if (job.status === "failed") {
    setDataMode("no_frames");
    state.diagnostics.push(job.error || event?.error || "AFSIM job failed");
  }
  const progressLines = summarizeProgress(job, event);
  if (progressLines.length && !tail.length) state.logLines = progressLines;
  renderAll();
}

async function loadJobReplay(jobId) {
  if (!jobId || state.replayLoadedForJob === jobId) return;
  try {
    const replay = await api(`/api/afsim/jobs/${encodeURIComponent(jobId)}/replay`);
    state.replayLoadedForJob = jobId;
    applyReplay(replay, { strict: true });
  } catch (error) {
    state.diagnostics.push(`replay not ready: ${error.message}`);
    renderDiagnostics();
  }
}

function stopJobWatch() {
  if (state.jobSocket) state.jobSocket.close();
  state.jobSocket = null;
  if (state.pollTimer) clearInterval(state.pollTimer);
  state.pollTimer = null;
}

function startJobPolling(jobId) {
  if (state.pollTimer) clearInterval(state.pollTimer);
  state.pollTimer = setInterval(async () => {
    try {
      const job = await api(`/api/afsim/jobs/${encodeURIComponent(jobId)}`);
      updateJobState(job);
      if (job.status === "finished") {
        clearInterval(state.pollTimer);
        state.pollTimer = null;
        await loadJobReplay(jobId);
      }
      if (job.status === "failed") {
        clearInterval(state.pollTimer);
        state.pollTimer = null;
      }
    } catch (error) {
      state.diagnostics.push(`job polling failed: ${error.message}`);
      renderDiagnostics();
    }
  }, 1200);
}

function watchJob(job) {
  stopJobWatch();
  state.activeJobId = job.job_id;
  state.activeJob = job;
  state.replayLoadedForJob = null;
  updateJobState(job);
  const protocol = window.location.protocol === "https:" ? "wss" : "ws";
  const socket = new WebSocket(`${protocol}://${window.location.host}/ws/afsim/jobs/${encodeURIComponent(job.job_id)}`);
  state.jobSocket = socket;
  startJobPolling(job.job_id);

  socket.addEventListener("message", async (message) => {
    const payload = JSON.parse(message.data);
    if (payload.type === "error" || payload.error) {
      state.diagnostics.push(payload.error || "AFSIM job WebSocket error");
      renderDiagnostics();
      return;
    }
    updateJobState(payload.job || state.activeJob, payload.event || null);
    const currentJob = payload.job || state.activeJob;
    if (currentJob?.status === "finished") await loadJobReplay(currentJob.job_id);
  });
  socket.addEventListener("error", () => {
    state.diagnostics.push("AFSIM job WebSocket 连接失败，已保留轮询刷新。");
    renderDiagnostics();
  });
}

async function runDemo() {
  const option = selectedDemoOption();
  if (!option) throw new Error("没有可用 Demo");
  stopPreviewStream();
  stopReplayPlayback();
  state.activeScenarioId = null;
  state.activeDemo = { demo_name: option.value, input_file: option.dataset.input || null };
  setDataMode("afsim_running");
  state.currentFrame = null;
  state.activeJob = null;
  state.activeJobId = null;
  state.lastRun = null;
  state.outputFiles = [];
  state.logLines = ["提交 AFSIM Demo 作业..."];
  state.diagnostics = [];
  renderAll();
  const job = await api("/api/afsim/run/jobs", {
    method: "POST",
    body: JSON.stringify({
      demo_name: option.value,
      input_file: option.dataset.input || null,
      timeout_seconds: timeoutSeconds(),
    }),
  });
  watchJob(job);
}

async function runGenerated() {
  const scenarioId = selectedGeneratedId();
  if (!scenarioId) throw new Error("请先选择生成场景");
  stopPreviewStream();
  stopReplayPlayback();
  state.activeScenarioId = scenarioId;
  state.activeDemo = null;
  setDataMode("afsim_running");
  state.currentFrame = null;
  state.activeJob = null;
  state.activeJobId = null;
  state.lastRun = null;
  state.outputFiles = [];
  state.logLines = [`提交生成场景作业: ${scenarioId}`];
  state.diagnostics = [];
  renderAll();
  const job = await api(`/api/afsim/designs/${encodeURIComponent(scenarioId)}/run/jobs`, {
    method: "POST",
    body: JSON.stringify({ timeout_seconds: timeoutSeconds() }),
  });
  watchJob(job);
}

function stopPreviewStream() {
  if (state.previewSocket) state.previewSocket.close();
  state.previewSocket = null;
  els.previewStreamBtn.classList.remove("active");
  els.previewStreamBtn.textContent = "开启设计预览流";
}

function startPreviewStream() {
  stopReplayPlayback();
  stopPreviewStream();
  setDataMode("design_preview");
  const params = sourceParams();
  params.set("interval_seconds", "0.75");
  params.set("loop_seconds", "120");
  const protocol = window.location.protocol === "https:" ? "wss" : "ws";
  const socket = new WebSocket(`${protocol}://${window.location.host}/ws/afsim/preview?${params.toString()}`);
  state.previewSocket = socket;
  els.previewStreamBtn.classList.add("active");
  els.previewStreamBtn.textContent = "停止设计预览流";
  socket.addEventListener("message", (message) => {
    const frame = JSON.parse(message.data);
    if (frame.error) {
      state.diagnostics.push(`预览流失败: ${frame.error}`);
      stopPreviewStream();
      renderDiagnostics();
      return;
    }
    state.currentFrame = frame;
    if (state.workbench?.simulation_time) state.workbench.simulation_time.current = Number(frame.sim_time || 0);
    setDataMode("design_preview");
    renderAll();
  });
  socket.addEventListener("close", () => {
    if (state.previewSocket === socket) stopPreviewStream();
  });
}

function togglePreviewStream() {
  if (state.previewSocket) stopPreviewStream();
  else startPreviewStream();
}

function stopReplayPlayback() {
  if (state.replayTimer) clearInterval(state.replayTimer);
  state.replayTimer = null;
  els.playReplayBtn.classList.remove("active");
  els.playReplayBtn.textContent = "播放 AFSIM 复盘";
}

function playReplay() {
  const frames = replayFrames();
  if (!frames.length) {
    state.diagnostics.push("没有 AFSIM replay frames，不能播放。");
    setDataMode("no_frames");
    renderAll();
    return;
  }
  stopPreviewStream();
  if (state.replayTimer) {
    stopReplayPlayback();
    return;
  }
  els.playReplayBtn.classList.add("active");
  els.playReplayBtn.textContent = "暂停 AFSIM 复盘";
  setDataMode("replay");
  state.replayTimer = setInterval(() => {
    const end = timelineEnd();
    const next = currentSimTime() + state.playbackSpeed;
    applyReplayTime(next > end ? 0 : next);
  }, 500);
}

async function refreshReplay() {
  if (state.activeJobId) {
    const job = await api(`/api/afsim/jobs/${encodeURIComponent(state.activeJobId)}`);
    updateJobState(job);
    if (job.status === "finished") await loadJobReplay(job.job_id);
    return;
  }
  const replay = await api("/api/afsim/replay/latest");
  applyReplay(replay, { strict: false });
}

async function loadLatestReplay() {
  const replay = await api("/api/afsim/replay/latest");
  applyReplay(replay, { strict: false });
}

function stopPlaybackAndPreview() {
  stopPreviewStream();
  stopReplayPlayback();
  renderAll();
}

function bindEvents() {
  els.demoSelect.addEventListener("change", () => loadDemoWorkbench().catch(showError));
  els.generatedSelect.addEventListener("change", () => loadGeneratedWorkbench().catch(showError));
  els.reloadWorkbenchBtn.addEventListener("click", () => {
    if (state.activeScenarioId) loadGeneratedWorkbench().catch(showError);
    else loadDemoWorkbench().catch(showError);
  });
  els.runDemoBtn.addEventListener("click", () => runDemo().catch(showError));
  els.runGeneratedBtn.addEventListener("click", () => runGenerated().catch(showError));
  els.refreshJobBtn.addEventListener("click", () => refreshReplay().catch(showError));
  els.refreshReplayBtn.addEventListener("click", () => loadLatestReplay().catch(showError));
  els.previewStreamBtn.addEventListener("click", togglePreviewStream);
  els.playReplayBtn.addEventListener("click", playReplay);
  els.stopPlaybackBtn.addEventListener("click", stopPlaybackAndPreview);
  els.fitMapBtn.addEventListener("click", () => {
    state.mapView = null;
    renderMap();
  });
  els.timelineRange.addEventListener("input", () => {
    stopReplayPlayback();
    applyReplayTime(Number(els.timelineRange.value));
  });
  els.layerList.addEventListener("change", (event) => {
    const row = event.target.closest("[data-layer-id]");
    if (!row || !state.workbench) return;
    const layer = state.workbench.layers.find((item) => item.id === row.dataset.layerId);
    if (!layer) return;
    layer.visible = event.target.checked;
    renderMap();
  });
  els.targetList.addEventListener("click", (event) => {
    const row = event.target.closest("[data-target-id]");
    if (!row) return;
    state.selectedId = row.dataset.targetId;
    renderTargets();
    renderMap();
  });
  els.eventList.addEventListener("click", (event) => {
    const row = event.target.closest("[data-event-index]");
    if (!row) return;
    const eventData = replayEvents().slice().sort((a, b) => Number(a.time || 0) - Number(b.time || 0))[Number(row.dataset.eventIndex)];
    if (!eventData) return;
    state.selectedEventId = eventData.id;
    state.selectedId = eventData.platform_id || eventData.target_id || eventData.detector_id || state.selectedId;
    applyReplayTime(Number(eventData.time || 0));
  });
  els.sceneView.addEventListener("afsim-map-select", (event) => {
    state.selectedId = event.detail.id;
    renderTargets();
    renderMap();
  });
  els.sceneView.addEventListener("afsim-map-view-change", (event) => {
    state.mapView = event.detail;
    renderMap();
  });
}

function showError(error) {
  state.diagnostics.push(error.message || String(error));
  renderDiagnostics();
}

window.addEventListener("error", (event) => showError(event.error || event.message));
window.addEventListener("unhandledrejection", (event) => showError(event.reason || "Unhandled promise rejection"));

async function boot() {
  assertElements();
  bindEvents();
  els.sceneView.innerHTML = `<div class="empty-row">正在加载 AFSIM workbench...</div>`;
  await loadHealth().catch(showError);
  await Promise.all([loadDemos(), loadDesigns()]).catch(showError);
  await loadDemoWorkbench().catch(showError);
  window.setInterval(renderStatus, 1000);
}

boot().catch(showError);
