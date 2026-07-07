# AFSIM Web Map Workbench

## 启动

```powershell
cd D:\AFISM\AFSIM\AFSIM_LLM
.\scripts\run_dev.ps1 -SkipInstall
```

访问 `http://127.0.0.1:8766`。二开代码只写入本项目的 `runtime/`、`generated_scenarios/` 和 `app/static/`，不修改 AFSIM 原始安装目录。

## 地图资源配置

工作台通过 `/api/afsim/workbench` 返回 `map_resources`，只读引用并服务化本机 AFSIM 资源：

- `resources/maps/bluemarble_db/bmng.mbtiles`：Blue Marble 离线影像瓦片。
- `resources/maps/naturalearth_db/natural.mbtiles`：Natural Earth 离线底图瓦片。
- `resources/maps/political_db/border.mbtiles`：政治边界瓦片。
- `resources/maps/layers/ne_50m_coastline.shp`：海岸线矢量层。
- `resources/maps/layers/pol.shp`：AFSIM geocentric/ECEF 政治边界矢量层。
- `resources/maps/layers/us.shp`：美国边界矢量层。
- `resources/models/milStdIconMappings.csv` 和 `resources/models/3d`：符号和模型来源线索。

后端新增地图服务接口，浏览器不直接读取 AFSIM 安装目录：

- `GET /api/afsim/maps`：返回 raster/vector 图层清单、Plate Carrée 瓦片矩阵和 3D 贴图 URL。
- `GET /api/afsim/maps/bluemarble/{z}/{x}/{y}.png`：读取 `bmng.mbtiles` 原始瓦片；URL 的 `y` 为北向原点，后端转换为 MBTiles/TMS `tile_row`。
- `GET /api/afsim/maps/{map_id}/metadata`：返回 MBTiles metadata 和 osgEarth profile。
- `GET /api/afsim/maps/{map_id}/texture.jpg?z=3`：把 AFSIM 瓦片拼成 Three.js 地球用 Plate Carrée 贴图，缓存到 `runtime/map_cache/`。
- `GET /api/afsim/maps/vectors/{layer}.geojson`：把 `coastline/pol/us` Shapefile 转成 GeoJSON，可用 `simplify` 和 `bbox` 参数裁剪/简化。

前端 2D Canvas 先绘制 AFSIM Blue Marble MBTiles 瓦片，再按图层叠加 coastline/pol/us GeoJSON、平台、航迹、雷达圈、通信链路、探测关系和事件标记。3D 地球使用同一套 Blue Marble 瓦片合成贴图，旧的程序化地貌、水系、道路、假海岸线底图已停用。

## AFSIM 字段映射

`app/services/afsim_parser.py` 递归读取 `include/include_once`，解析并保留原始文件和行号：

- `platform NAME TYPE` -> `platforms[].id/type/afsim.source_ref`
- `platform_type NAME BASE` -> `afsim_definitions.platform_types[NAME]`，并继承到同类型平台。
- `side` -> `platforms[].side`
- `commander` / `group_join` -> `platforms[].commander/groups` 和 `communications[].chain_type=command`
- `icon/category` -> `platforms[].category/symbol`
- `route/label/goto/position/altitude/heading/speed` -> `route[].lat/lon/alt_m/heading_deg/speed_kts` 和 `route_metadata.labels/gotos`
- `sensor/weapon/processor/task_processor/comm/edit ...` -> `platforms[].afsim.sensors/weapons/processors/communications`
- `maximum_range/one_m2_detect_range/azimuth_*_limits/elevation_*_limits/power/frequency/quantity` -> `sensors/weapons` 的 range、FOV、功率、频率、数量字段。

include 解析会同时尝试当前文件目录和想定根目录，覆盖 wargame 这类按 demo 根目录组织 `platforms/include.txt` 的场景。

前端目标属性面板展示源文件和行号，便于从网页对象追溯到 AFSIM 场景文本。

## 图层配置

图层目录由 `app/services/afsim_workbench.py` 维护，状态持久化到 `runtime/workbench/layer_state.json`。图层支持：

- 显隐、透明度、锁定、查询。
- 聚焦：前端记录当前聚焦图层并强制该层可见。
- 导出：导出当前图层关联的 platforms/tracks/sensors/weapons/detections/communications/events JSON。

图层分组覆盖基础地理、军事部署、动态态势、电磁态势、复盘分析和环境保障，内置超过 50 个图层。

## 符号体系

2D 使用 Canvas 绘制 AFSIM/Warlock 风格深色战术符号：

- 飞机、预警机、轰炸机、无人机、导弹、卫星、舰船、潜艇、地面目标、雷达、指挥中心、干扰源。
- 红蓝中立颜色克制区分，目标支持方向、选中高亮、阵营/类型/高度/速度/航向标签。
- 航迹支持历史线、预测线、航段方向箭头和航路点编号。

3D 使用 Three.js 数字地球和简化几何模型表达平台类型；大型 AFSIM OSGB 模型只作为来源线索，未直接加载。

## 地图交互和编辑

- 2D/2.5D 地图支持滚轮缩放、鼠标拖拽平移、目标点选、矩形框选查询。
- 3D 地球支持鼠标拖动旋转和滚轮缩放，平台简化模型按高度贴地/贴球显示。
- “测量”模式在地图上点击两个点，输出距离和方位。
- “编辑”模式点击地图写入选中目标的新坐标；“预览 patch”只更新当前浏览器态势和中间 JSON，不直接改 AFSIM 原始文件。
- “保存草稿”把 `afsim-controlled-patch.v1` 操作写入 `runtime/workbench/drafts/*.json`，并记录审计日志。撤销/清空会回退预览状态。

## 时间轴和复盘数据

`/api/afsim/replay/latest` 优先选择最近的可生成 replay frame 的运行输出；指定 `/api/afsim/replay/{run_id}` 不回退。前端拖动时间轴时，在相邻 replay frames 之间线性插值平台经纬度、高度和航向，使目标位置连续变化。

复盘数据结构包含 `events/frames/tracks/bounds/semantic_events/summary`。事件列表点击会跳转到对应时间并高亮相关目标。

## 网页调用 AFSIM 链路

网页“运行当前 Demo”和“运行生成场景”优先调用后端作业接口：

- `POST /api/afsim/run/jobs`
- `POST /api/afsim/designs/{scenario_id}/run/jobs`
- `GET /api/afsim/jobs/{job_id}`
- `GET /api/afsim/jobs/{job_id}/replay`
- `WS /ws/afsim/jobs/{job_id}`

后端把本机 `mission.exe` 放入后台线程执行，并通过 WebSocket 推送 `queued/starting/running/finished/failed`、运行目录、输出文件列表和日志尾部。官方 demo 会先复制到 `runtime/afsim_workdirs/<run_id>/`，包括入口文件声明的 sibling `file_path` 依赖，例如 `../base_types`；原始 AFSIM 安装目录只读使用，不写入官方 demo 的 `output/`。

完成后，后端把本次 `.log/.evt/.aer/.csv` 与 `mission.stdout.log` 归档到 `runtime/afsim_runs/<run_id>/`，并按同一个 `run_id` 构造 replay。前端收到作业完成事件后调用 `/api/afsim/jobs/{job_id}/replay`，再驱动地图、时间轴、事件列表、航迹和链路视图。

同步兼容接口仍保留：

- `POST /api/afsim/run`
- `POST /api/afsim/designs/{scenario_id}/run`

同步接口执行本机 `mission.exe`，把本次输出复制到 `runtime/afsim_runs/<run_id>/`，并立即返回：

```json
{
  "run": { "run_id": "...", "returncode": 0, "files": [] },
  "replay": { "schema_version": "afsim-replay.v1", "summary": { "run_id": "..." } }
}
```

前端优先使用返回的 `replay` 驱动地图、时间轴和事件列表；如果需要手动刷新指定运行，调用 `/api/afsim/replay/{run_id}`。这条链路不再依赖“最近一次运行”推断，避免多个运行交错时显示错复盘。

## 性能和验证约束

`/api/afsim/workbench` 返回 `performance_design`，当前设计目标为 5Hz 刷新、200 个三维动态目标、300 个二维动态目标。前端渲染层保持 Canvas/Three.js 单次重绘，不在拖拽过程中写入原始 AFSIM 目录。
