# AFSIM_LLM 架构

## 定位

AFSIM_LLM 是 AFSIM 的 Web 数据解析与仿真工程工作台。它不再调用 Warlock 做网页实时显示，也不再把 Mystic 回放作为主界面能力。当前主线是网页端复现 AFSIM 风格地图，本地 AFSIM 负责权威仿真，网页端实时解析和渲染态势数据。

Warlock/Mystic 可以作为外部 AFSIM 工具单独打开用于人工对照，但它们不属于本系统的网页渲染链路。

## 当前闭环

```mermaid
flowchart LR
  A["网页场景设计器"] --> B["生成 scenario.txt"]
  B --> C["AFSIM 输入解析器"]
  C --> D["平台 / 航路 / 传感器 / 链路 / 边界"]
  D --> E["统一工作台态势 API"]
  E --> F["2D 战术图 / 3D 地球 / 分屏"]
  B --> N["mission.exe 运行"]
  N --> G["采集 log / evt / aer"]
  G --> H["运行复盘适配器"]
  H --> E
  G --> J["LLM 仿真工程复盘"]
  K["官方 demo 输入文件"] --> C
  L["实时指挥 Agent"] --> M["封闭仿真沙盘"]
  M --> L
```

## 后端模块

- `app/main.py`：FastAPI 路由、静态页面、AFSIM API。
- `app/services/afsim_design.py`：网页场景设计转 AFSIM 输入文件。
- `app/services/afsim_parser.py`：递归解析 scenario/include 中的平台、阵营、类型、位置、航路、边界和 GeoJSON。
- `app/services/afsim_realtime.py`：生成统一态势帧，当前使用解析航路生成预览帧，后续替换为 AFSIM 实时输出解析。
- `app/services/afsim_replay.py`：解析 AFSIM 运行输出，当前支持 `event_output` 文本事件和 CSV 事件输出，生成复盘事件、态势帧、轨迹和边界；`.aer` 先作为源产物索引。
- `app/services/afsim_runner.py`：发现 demo、运行 `mission.exe`、采集输出、列出运行记录。
- `app/services/afsim_workbench.py`：把解析场景、运行输出和图层状态合成为网页工作台态势数据，管理图层持久化、复盘事件、草稿审计和回退。
- `app/services/simulation.py`：实时指挥 Agent 使用的封闭仿真沙盘状态和白名单指令应用。
- `app/services/llm.py`：硅基流动 OpenAI 兼容 API 适配、实时指挥建议和本地兜底分析。
- `app/static/js/map_renderer.js`：AFSIM 风格 2D 战术图、Three.js 3D 地球和分屏渲染。
- `app/static/`：网页地图工作台前端。

## 前端模块

- 左侧工作区：图层树、平台资源库、生成场景库、官方 demo 和草稿审计。
- 中央工作区：2D 战术图、3D 地球、分屏、实时态势、底部时间轴和仿真控制条。
- 右侧工作区：目标属性、事件时间线、链路摘要和 LLM 分析。
- 网页地图渲染：使用 `/api/afsim/workbench` 返回的 `bounds/platforms/tracks/sensors/weapons/detections/communications/events/layers` 渲染态势。
- 实时态势通道：通过 `WS /ws/afsim/realtime` 接收统一态势帧并刷新地图。第一版帧源为解析航路预览，权威帧源将来自 `mission.exe` 的事件输出。
- 运行/复盘面板：运行生成场景或 demo，从 `runtime/afsim_runs` 读取最近输出，生成 replay frames，时间轴拖动时按帧刷新地图，并把最近运行交给 LLM 分析。

## 数据设计

解析器返回的核心结构：

```json
{
  "input_file": "scenario.txt",
  "included_files": ["scenario.txt", "setup.txt"],
  "platform_count": 2,
  "route_count": 1,
  "bounds": {
    "min_lat": 1.05,
    "max_lat": 1.20,
    "min_lon": 1.05,
    "max_lon": 1.30
  },
  "platforms": [],
  "geojson": {
    "type": "FeatureCollection",
    "features": []
  }
}
```

这让前端不直接理解 AFSIM 文本语法，而是消费稳定的结构化结果。后续扩展传感器、武器、通信链路和任务段时，优先在解析器输出中增加结构化字段，再让前端按字段渲染图层。

实时态势帧核心结构：

```json
{
  "frame_id": 7,
  "source": "generated:scenario_id",
  "authoritative": false,
  "sim_time": 12.5,
  "bounds": {},
  "entity_count": 2,
  "entities": [
    {
      "id": "Blue_Test",
      "side": "blue",
      "lat": 1.05,
      "lon": 1.12,
      "alt_m": 9000,
      "heading_deg": 90,
      "route": []
    }
  ],
  "events": []
}
```

`authoritative=false` 表示当前帧来自解析航路预览。接入 `event_output`、`csv_event_output`、DIS 或 event pipe 后，权威帧应标记为 `authoritative=true`。

工作台态势核心结构：

```json
{
  "schema_version": "afsim-workbench.v1",
  "source": {},
  "bounds": {},
  "platforms": [],
  "tracks": [],
  "sensors": [],
  "weapons": [],
  "detections": [],
  "communications": [],
  "events": [],
  "layers": [],
  "simulation_time": {},
  "chain_graph": {}
}
```

图层目录由后端集中维护，按基础地理、军事部署、动态态势、环境保障、情报监控、电磁态势和复盘事件分组。图层状态写入 `runtime/workbench/layer_state.json`。网页编辑草稿写入 `runtime/workbench/drafts`，审计记录写入 `runtime/workbench/audit.jsonl`，回退只恢复中间 JSON，不直接改 AFSIM 官方输入文件。

复盘数据结构：

```json
{
  "schema_version": "afsim-replay.v1",
  "events": [],
  "frames": [
    {
      "authoritative": true,
      "sim_time": 12.5,
      "entities": [],
      "events": []
    }
  ],
  "tracks": [],
  "source_files": [],
  "summary": {
    "event_count": 120,
    "frame_count": 60,
    "timeline": { "start": 0.0, "end": 4200.0 }
  }
}
```

`event_output` 解析器会识别事件起始时间、事件类型、探测方/目标方、Rcvr/Tgt LLA、Range、Pd、RequiredPd 和 Detected 字段；这些字段会转换为地图实体、探测事件和可拖动回放帧。CSV 事件输出通过常见字段名 `time/sim_time/platform/lat/lon/alt/heading` 生成帧。AER/DIS/event pipe 的完整二进制解码仍保留为下一阶段工作。

`/api/afsim/replay/latest` 面向地图工作台使用，会在最近运行列表中优先选择可生成 replay frame 的运行；如果最新运行只有日志或空 `.evt`，则回退到最近一个有坐标帧的运行，并在 `summary.selection_policy`、`latest_run_id` 和 `selected_run_index` 中说明。`/api/afsim/replay/{run_id}` 始终返回指定运行，不做回退。

## Agent 方向

当前版本先用轻量编排实现可落地闭环：前端设置目标、指挥方、步长和自治模式，后端推进封闭仿真沙盘，调用硅基流动模型输出 JSON 指令，再由白名单执行器应用到仿真状态。此阶段不强依赖 LangChain，原因是工具数量和状态机还不复杂，直接代码更容易审计和落地。

当后续加入更多工具时，再把这一层迁移到 LangGraph/LangChain 更合适，例如：AFSIM 场景检查工具、`.evt/.aer` 时序解析工具、场景版本工具、RAG 知识库工具和权限审计工具。

## 安全边界

LLM 只用于封闭仿真工程分析、场景检查、复盘建议和仿真内态势管理。系统不执行真实世界作战命令，不输出武器释放、杀伤或规避拦截等现实伤害性指令。生成场景用于 AFSIM 仿真验证。

## 下一步

- 扩展 AFSIM base_types、demo 类型库，形成分层平台模板库。
- 增加图上拖放、绘制航线/区域/威力区和批量平台导入。
- 扩展 `.evt/.csv` 事件覆盖，形成更完整的时间索引态势数据。
- 接入 AER/DIS/event pipe 专用解码器，形成更完整的权威实时帧。
- 复用 `resources/maps` 和 `resources/models/mil-std2525d`，提高底图、军标和符号风格的一致性。
- 将网页地图升级为更完整的 WebGIS 编辑器，支持保存为中间 JSON 后同步生成 AFSIM 配置。
- 将实时 Agent 升级为 LangGraph/LangChain 工具编排，并加入人工审批、记忆和任务规划。
- 增加数据库化场景库、运行库、复盘库和权限审计。
