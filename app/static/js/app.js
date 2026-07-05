const state = {
  scenario: null,
  snapshot: null,
  pendingAdvice: null,
};

window.__AFSIM_LLM_BOOTED = false;
window.addEventListener("error", (event) => {
  const label = document.getElementById("scenarioLabel");
  if (label) label.textContent = `前端错误：${event.message}`;
});

const els = {
  scenarioLabel: document.getElementById("scenarioLabel"),
  simTime: document.getElementById("simTime"),
  runState: document.getElementById("runState"),
  speedFactor: document.getElementById("speedFactor"),
  unitCount: document.getElementById("unitCount"),
  detectionCount: document.getElementById("detectionCount"),
  layerCount: document.getElementById("layerCount"),
  eventCount: document.getElementById("eventCount"),
  layers: document.getElementById("layers"),
  units: document.getElementById("units"),
  events: document.getElementById("events"),
  canvas: document.getElementById("battleCanvas"),
  objective: document.getElementById("objective"),
  autonomy: document.getElementById("autonomy"),
  askCommander: document.getElementById("askCommander"),
  commanderOutput: document.getElementById("commanderOutput"),
  integrationOutput: document.getElementById("integrationOutput"),
  applyAdvice: document.getElementById("applyAdvice"),
  makeReport: document.getElementById("makeReport"),
  exportAfsim: document.getElementById("exportAfsim"),
};

const ctx = els.canvas && typeof els.canvas.getContext === "function" ? els.canvas.getContext("2d") : null;

function api(path, options = {}) {
  return fetch(path, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  }).then((response) => {
    if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
    return response.json();
  });
}

function resizeCanvas() {
  if (!ctx) return;
  const rect = els.canvas.getBoundingClientRect();
  const scale = window.devicePixelRatio || 1;
  els.canvas.width = Math.max(600, Math.floor(rect.width * scale));
  els.canvas.height = Math.max(400, Math.floor(rect.height * scale));
  ctx.setTransform(scale, 0, 0, scale, 0, 0);
}

function worldToScreen(x, y) {
  const rect = els.canvas.getBoundingClientRect();
  const scale = Math.min(rect.width / 640, rect.height / 420);
  return {
    x: rect.width / 2 + x * scale,
    y: rect.height / 2 + y * scale,
    scale,
  };
}

function drawGrid(rect) {
  if (!ctx) return;
  ctx.fillStyle = "#0d1114";
  ctx.fillRect(0, 0, rect.width, rect.height);
  ctx.strokeStyle = "#1f2a33";
  ctx.lineWidth = 1;
  for (let x = 0; x < rect.width; x += 50) {
    ctx.beginPath();
    ctx.moveTo(x, 0);
    ctx.lineTo(x, rect.height);
    ctx.stroke();
  }
  for (let y = 0; y < rect.height; y += 50) {
    ctx.beginPath();
    ctx.moveTo(0, y);
    ctx.lineTo(rect.width, y);
    ctx.stroke();
  }
  const origin = worldToScreen(0, 0);
  ctx.strokeStyle = "#2f4652";
  ctx.beginPath();
  ctx.moveTo(0, origin.y);
  ctx.lineTo(rect.width, origin.y);
  ctx.moveTo(origin.x, 0);
  ctx.lineTo(origin.x, rect.height);
  ctx.stroke();
}

function drawFeatures(snapshot) {
  if (!ctx) return;
  const layers = new Map(snapshot.layers.map((layer) => [layer.id, layer]));
  const intel = layers.get("intel_watch");
  if (intel?.visible) {
    ctx.strokeStyle = "rgba(84, 214, 214, 0.45)";
    ctx.fillStyle = "rgba(84, 214, 214, 0.08)";
    for (const feature of intel.features) {
      if (feature.type === "circle") {
        const p = worldToScreen(feature.x, feature.y);
        ctx.beginPath();
        ctx.arc(p.x, p.y, feature.r * p.scale, 0, Math.PI * 2);
        ctx.fill();
        ctx.stroke();
      }
    }
  }
}

function sideColor(side) {
  if (side === "blue") return "#4aa3ff";
  if (side === "red") return "#ff6767";
  return "#65d38b";
}

function drawSensorRange(unit) {
  if (!ctx) return;
  const radar = unit.sensors?.find((sensor) => sensor.type === "radar" || sensor.type === "jammer");
  if (!radar || !radar.enabled) return;
  const p = worldToScreen(unit.position.x, unit.position.y);
  ctx.strokeStyle = unit.side === "blue" ? "rgba(74,163,255,0.22)" : "rgba(255,103,103,0.2)";
  ctx.fillStyle = unit.side === "blue" ? "rgba(74,163,255,0.05)" : "rgba(255,103,103,0.05)";
  ctx.beginPath();
  ctx.arc(p.x, p.y, radar.range_km * radar.power * p.scale, 0, Math.PI * 2);
  ctx.fill();
  ctx.stroke();
}

function drawTrack(unit) {
  if (!ctx) return;
  if (!unit.route || unit.route.length < 2) return;
  ctx.strokeStyle = sideColor(unit.side);
  ctx.globalAlpha = 0.5;
  ctx.beginPath();
  unit.route.forEach((point, index) => {
    const p = worldToScreen(point.x, point.y);
    if (index === 0) ctx.moveTo(p.x, p.y);
    else ctx.lineTo(p.x, p.y);
  });
  ctx.stroke();
  ctx.globalAlpha = 1;
}

function drawUnit(unit) {
  if (!ctx) return;
  if (!unit.visible) return;
  const p = worldToScreen(unit.position.x, unit.position.y);
  const color = sideColor(unit.side);
  ctx.save();
  ctx.translate(p.x, p.y);
  ctx.rotate((unit.heading_deg * Math.PI) / 180);
  ctx.fillStyle = color;
  if (["aircraft", "missile", "satellite"].includes(unit.kind)) {
    ctx.beginPath();
    ctx.moveTo(0, -9);
    ctx.lineTo(6, 8);
    ctx.lineTo(0, 4);
    ctx.lineTo(-6, 8);
    ctx.closePath();
    ctx.fill();
  } else if (unit.kind === "radar" || unit.kind === "jammer") {
    ctx.beginPath();
    ctx.arc(0, 0, 7, 0, Math.PI * 2);
    ctx.fill();
    ctx.strokeStyle = "#111417";
    ctx.stroke();
  } else {
    ctx.fillRect(-6, -6, 12, 12);
  }
  ctx.restore();
  ctx.fillStyle = "#d8e1e8";
  ctx.font = "12px Microsoft YaHei, sans-serif";
  ctx.fillText(unit.name, p.x + 9, p.y - 9);
}

function drawDetections(snapshot) {
  if (!ctx) return;
  const units = new Map(snapshot.units.map((unit) => [unit.id, unit]));
  ctx.strokeStyle = "rgba(244,189,91,0.78)";
  ctx.lineWidth = 1.5;
  for (const detection of snapshot.detections) {
    const a = units.get(detection.sensor);
    const b = units.get(detection.target);
    if (!a || !b) continue;
    const pa = worldToScreen(a.position.x, a.position.y);
    const pb = worldToScreen(b.position.x, b.position.y);
    ctx.beginPath();
    ctx.moveTo(pa.x, pa.y);
    ctx.lineTo(pb.x, pb.y);
    ctx.stroke();
  }
}

function draw() {
  if (!ctx) return;
  resizeCanvas();
  const rect = els.canvas.getBoundingClientRect();
  const snapshot = state.snapshot;
  drawGrid(rect);
  if (!snapshot) return;
  drawFeatures(snapshot);
  snapshot.units.forEach(drawSensorRange);
  snapshot.units.forEach(drawTrack);
  drawDetections(snapshot);
  snapshot.units.forEach(drawUnit);
}

function renderSidebar(snapshot) {
  els.simTime.textContent = `T+${snapshot.sim_time.toFixed(1)}s`;
  els.runState.textContent = snapshot.running ? "RUN" : "STOP";
  els.speedFactor.textContent = `${snapshot.speed_factor}x`;
  els.unitCount.textContent = snapshot.units.length;
  els.detectionCount.textContent = snapshot.detections.length;
  els.layerCount.textContent = snapshot.layers.length;
  els.eventCount.textContent = snapshot.events.length;

  els.layers.innerHTML = snapshot.layers
    .map(
      (layer) => `
      <div class="layer-row">
        <label>
          <input type="checkbox" data-layer="${layer.id}" ${layer.visible ? "checked" : ""} />
          <span>${layer.name}</span>
        </label>
        <small>${layer.group}</small>
      </div>
    `,
    )
    .join("");

  els.units.innerHTML = snapshot.units
    .map(
      (unit) => `
      <div class="unit-card">
        <strong><span>${unit.name}</span><span style="color:${sideColor(unit.side)}">${unit.side}</span></strong>
        <small>${unit.kind} | (${unit.position.x.toFixed(1)}, ${unit.position.y.toFixed(1)}, ${unit.altitude_km.toFixed(1)}km) | ${unit.speed_kps.toFixed(2)}km/s</small>
      </div>
    `,
    )
    .join("");

  els.events.innerHTML = snapshot.events
    .slice(-12)
    .reverse()
    .map((event) => `<div class="event">T+${event.t}s ${event.category}</div>`)
    .join("");
}

function setSnapshot(snapshot) {
  state.snapshot = snapshot;
  renderSidebar(snapshot);
  draw();
}

async function loadScenario() {
  state.scenario = await api("/api/scenario");
  els.scenarioLabel.textContent = `${state.scenario.name} | ${state.scenario.domain} | ${state.scenario.weather}`;
  setSnapshot(await api("/api/state"));
}

async function sendControl(action) {
  const snapshot = await api("/api/control", {
    method: "POST",
    body: JSON.stringify({ action }),
  });
  setSnapshot(snapshot);
}

async function askCommander() {
  els.commanderOutput.textContent = "正在请求指挥智能体...";
  const result = await api("/api/commander", {
    method: "POST",
    body: JSON.stringify({
      objective: els.objective.value,
      side: "blue",
      autonomy: els.autonomy.value,
    }),
  });
  state.pendingAdvice = result.advice;
  els.commanderOutput.textContent = `${result.advice.source}: ${result.advice.summary}\n${result.advice.commands
    .map((cmd) => `${cmd.action} ${cmd.unit_id || ""} ${cmd.value ?? ""} | ${cmd.reason}`)
    .join("\n")}`;
  setSnapshot(result.state);
}

async function applyAdvice() {
  if (!state.pendingAdvice?.commands?.length) {
    els.commanderOutput.textContent = "暂无可应用的指挥建议。";
    return;
  }
  const result = await api("/api/commands/apply", {
    method: "POST",
    body: JSON.stringify({ commands: state.pendingAdvice.commands }),
  });
  els.commanderOutput.textContent += `\n已应用 ${result.applied.filter((item) => item.status === "ok").length} 条命令。`;
  setSnapshot(result.state);
}

async function makeReport() {
  const result = await api("/api/reports", { method: "POST", body: "{}" });
  els.integrationOutput.textContent = `复盘已生成：${result.path}`;
}

async function exportAfsim() {
  const result = await api("/api/export/afsim", { method: "POST", body: "{}" });
  els.integrationOutput.textContent = `AFSIM 草案已导出：${result.path}\nAFSIM 根目录：${result.afsim.root}`;
}

function connectSocket() {
  const protocol = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${protocol}://${location.host}/ws/state`);
  ws.onmessage = (event) => setSnapshot(JSON.parse(event.data));
  ws.onclose = startPolling;
  ws.onerror = startPolling;
}

let pollTimer = null;

function startPolling() {
  if (pollTimer) return;
  pollTimer = setInterval(async () => {
    try {
      setSnapshot(await api("/api/state"));
    } catch (error) {
      els.scenarioLabel.textContent = `连接中断：${error.message}`;
    }
  }, 500);
}

document.querySelectorAll("[data-action]").forEach((button) => {
  button.addEventListener("click", () => sendControl(button.dataset.action));
});

els.askCommander.addEventListener("click", askCommander);
els.applyAdvice.addEventListener("click", applyAdvice);
els.makeReport.addEventListener("click", makeReport);
els.exportAfsim.addEventListener("click", exportAfsim);
els.layers.addEventListener("change", async (event) => {
  const input = event.target;
  if (!input.dataset.layer) return;
  await api(`/api/layers/${input.dataset.layer}/visibility`, {
    method: "POST",
    body: JSON.stringify({ visible: input.checked }),
  });
});

window.addEventListener("resize", draw);

window.__AFSIM_LLM_BOOTED = true;

loadScenario().then(connectSocket).catch((error) => {
  els.scenarioLabel.textContent = `启动失败：${error.message}`;
});
