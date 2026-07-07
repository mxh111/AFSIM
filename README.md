# AFSIM_LLM

AFSIM_LLM 是一个面向 AFSIM 二次开发的本机 Web 工作台。当前主线是：网页端复现 AFSIM 风格地图，本地 AFSIM 负责权威仿真，网页端实时解析和渲染态势数据。

Warlock/Mystic 不再作为网页实时显示或自动启动路径。它们可以继续作为 AFSIM 外部工具独立使用，但本项目的核心显示链路由解析器和网页地图承担。

## 已完成能力

- 网页端场景设计器：配置平台、阵营、类型、图标、经纬度、高度、速度、航向和航路终点。
- 平台模板库：内置多类可运行 AFSIM 平台模板，覆盖飞机、无人机、地面雷达、指控节点、防空阵地、水面舰艇和保障点。
- 生成真实 AFSIM 输入文件：写入 `generated_scenarios/<scenario_id>/scenario.txt`。
- 场景库管理：网页端可选择、查看、保存和删除生成场景。
- AFSIM 文本解析：递归读取 `include/include_once`，提取平台、阵营、类型、位置、航路、边界和 GeoJSON。
- AFSIM 场景语义增强：解析 `platform_type` 继承、`commander` 指挥链、`route label/goto`、`edit sensor/comm/processor`、传感器范围/FOV/功率/频率和武器数量等字段，并保留源文件行号。
- AFSIM 风格地图展示：中间工作区支持 2D 战术图、3D 地球和 2D/3D 同屏，直接渲染平台点位和航路。
- AFSIM 地图工作台：顶部状态栏、左侧图层/资源/场景、中央 2D/3D 地图、右侧目标/事件/链路/LLM、底部时间轴和仿真控制条。
- 地图交互：2D/2.5D 支持滚轮缩放、拖拽平移、点选、矩形框选和距离/方位测量；3D 地球支持拖动旋转和滚轮缩放。
- 统一态势 API：后端输出 `platforms/tracks/sensors/weapons/detections/communications/events/layers/simulation_time`，前端不硬编码复杂解析逻辑。
- 图层系统：内置 30+ 图层，按基础地理、军事部署、动态态势、环境保障、情报监控、电磁态势、复盘事件组织，支持显隐、透明度、锁定、查询和持久化。
- 作战态势效果：支持红蓝目标符号、历史/预测轨迹、雷达探测范围、干扰区、通信链路、探测关系和复盘事件标记。
- 场景草稿审计：网页编辑结果可先保存为 `runtime/workbench/drafts/*.json`，并写入 `runtime/workbench/audit.jsonl`，回退不会破坏原始 AFSIM 场景文件。
- 受控场景 patch：网页端位置编辑只生成 `afsim-controlled-patch.v1` 中间 JSON patch，支持预览、撤销、清空和保存草稿，不直接改写原始 AFSIM 文件。
- 实时态势帧接口：`/ws/afsim/realtime` 推送统一态势帧，当前先基于解析航路生成预览帧，后续可替换为 `mission.exe` 实时输出解析。
- AFSIM 输出复盘帧：解析 `event_output` 文本事件和 CSV 事件输出，生成 `events/frames/tracks/bounds`；`.aer` 文件先作为 AFSIM/Warlock/Mystic 源产物索引保存。
- 调用 AFSIM `mission.exe` 运行生成场景或官方 demo。
- 自动采集 `.log`、`.evt`、`.aer` 到 `runtime/afsim_runs`。
- 调用硅基流动 OpenAI 兼容 API，对 AFSIM 运行结果做仿真工程分析。
- 实时指挥 Agent：按步长推进封闭仿真沙盘，调用大模型生成白名单指令，支持人工确认或自动应用。

## 启动

```powershell
cd D:\AFISM\AFSIM\AFSIM_LLM
.\scripts\run_dev.ps1
```

然后打开：

```text
http://127.0.0.1:8766
```

停止服务：

```powershell
cd D:\AFISM\AFSIM\AFSIM_LLM
.\scripts\stop_dev.ps1
```

也可以手动启动：

```powershell
python -m pip install -r requirements.txt --index-url https://mirrors.aliyun.com/pypi/simple/ --trusted-host mirrors.aliyun.com
python -m uvicorn app.main:app --host 127.0.0.1 --port 8766
```

## 配置

复制 `.env.example` 为 `.env`，按本机路径调整：

```text
AFSIM_LLM_HOST=127.0.0.1
AFSIM_LLM_PORT=8766
AFSIM_ROOT=D:\AFISM\AFSIM\am-2.9.0-win64.part1\afsim-2.9.0-win64
AFSIM_LLM_DB=D:\AFISM\AFSIM\AFSIM_LLM\afsim_llm.sqlite3
SILICONFLOW_BASE_URL=https://api.siliconflow.cn/v1
SILICONFLOW_MODEL=deepseek-ai/DeepSeek-V3
SILICONFLOW_API_KEY=你的Key
```

## 使用流程

1. 打开页面后默认载入生成场景库中的最新场景；没有生成场景时载入官方 demo。
2. 左侧“图层”面板控制图层显隐、透明度、锁定和查询，点击“保存状态”会写入 `runtime/workbench/layer_state.json`。
3. 左侧“资源”面板从模板添加平台，编辑平台属性后可“生成 AFSIM 场景”，也可先“保存中间 JSON”。
4. 左侧“场景”面板可选择生成场景或官方 demo，并运行本地 `mission.exe`。
5. 中央地图支持 `2D`、`3D`、`分屏`、实时态势流、跟随、框选状态和视角复位。
6. 点击右侧“事件”里的“刷新复盘”会优先读取最近一次可生成 replay frame 的 AFSIM 运行输出；拖动底部时间轴可按帧更新地图目标位置。若最新运行只有日志事件没有坐标帧，接口会回退到最近一个可回放运行，并在 `summary.selection_policy` 中标记。
7. 底部仿真控制条支持启动、暂停、继续、加速、减速、单步、复位和终止，并显示时间、目标数、事件数和复盘帧数。
8. 右侧“目标/事件/链路/LLM”用于选择目标、按时间查看复盘事件、查看链路摘要和分析最近运行输出。

## 主要接口

- `GET /api/health`
- `GET /api/afsim/status`
- `GET /api/afsim/demos`
- `GET /api/afsim/workbench`
- `GET /api/afsim/layers`
- `POST /api/afsim/layers/state`
- `GET /api/afsim/replay/latest`
- `GET /api/afsim/replay/{run_id}`
- `GET /api/afsim/drafts`
- `POST /api/afsim/drafts`
- `POST /api/afsim/drafts/{draft_id}/restore`
- `GET /api/afsim/platform-templates`
- `GET /api/afsim/scenario`
- `GET /api/afsim/designs`
- `POST /api/afsim/designs`
- `GET /api/afsim/designs/{scenario_id}`
- `DELETE /api/afsim/designs/{scenario_id}`
- `GET /api/afsim/designs/{scenario_id}/scene`
- `POST /api/afsim/designs/{scenario_id}/run`
- `POST /api/afsim/run`
- `POST /api/afsim/analyze`
- `POST /api/agent/tick`
- `POST /api/commands/apply`
- `WS /ws/afsim/realtime`

## 新增工作台数据契约

高保真地图、离线资源、字段映射、图层、符号和复盘时间轴说明见 `docs/MAP_WORKBENCH.md`。

`GET /api/afsim/workbench` 返回网页地图的统一态势数据：

```json
{
  "platforms": [],
  "tracks": [],
  "sensors": [],
  "weapons": [],
  "detections": [],
  "communications": [],
  "events": [],
  "layers": [],
  "simulation_time": {}
}
```

图层和草稿只写入项目自身的 `runtime/workbench`，不会写入 AFSIM 安装目录，也不会直接改官方 demo 或原始想定文件。

`GET /api/afsim/replay/latest` 返回最近一次 AFSIM 运行的复盘数据：

```json
{
  "schema_version": "afsim-replay.v1",
  "events": [],
  "frames": [],
  "tracks": [],
  "bounds": {},
  "source_files": [],
  "summary": {
    "run_id": "178...",
    "event_count": 120,
    "frame_count": 60,
    "timeline": { "start": 0.0, "end": 4200.0 }
  }
}
```

当前已支持 AFSIM `event_output` 文本和 CSV 事件输出的结构化解析。`.aer` 属于 AFSIM 专用/二进制回放产物，本版本先作为源产物索引，保留后续接入专用解码器或外部转换器的位置。

## 下一步

- 扩展 AFSIM 输入语法解析，覆盖更多 `platform_type`、传感器、武器、通信和任务配置。
- 扩展 `.evt/.csv` 事件类型覆盖，识别命中、拦截、通信中断、链路建立和任务处理器事件。
- 接入 `.aer`/DIS/event pipe 的专用解码路径，把预览帧替换为更完整的权威仿真输出帧。
- 复用 `resources/maps` 和 `resources/models/mil-std2525d`，继续提高地图底图、军标和符号风格的一致性。
- 增加图上拖放、绘制区域/航路、批量目标生成和 AFSIM 场景同步器。
- 增加数据库化场景版本管理、运行记录索引和复盘库。
