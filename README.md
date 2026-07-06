# AFSIM_LLM

AFSIM_LLM 是一个面向 AFSIM 二次开发的本机 Web 工作台。当前主线是：网页端复现 AFSIM 风格地图，本地 AFSIM 负责权威仿真，网页端实时解析和渲染态势数据。

Warlock/Mystic 不再作为网页实时显示或自动启动路径。它们可以继续作为 AFSIM 外部工具独立使用，但本项目的核心显示链路由解析器和网页地图承担。

## 已完成能力

- 网页端场景设计器：配置平台、阵营、类型、图标、经纬度、高度、速度、航向和航路终点。
- 平台模板库：内置多类可运行 AFSIM 平台模板，覆盖飞机、无人机、地面雷达、指控节点、防空阵地、水面舰艇和保障点。
- 生成真实 AFSIM 输入文件：写入 `generated_scenarios/<scenario_id>/scenario.txt`。
- 场景库管理：网页端可选择、查看、保存和删除生成场景。
- AFSIM 文本解析：递归读取 `include/include_once`，提取平台、阵营、类型、位置、航路、边界和 GeoJSON。
- AFSIM 风格地图展示：中间工作区支持 2D 战术图、3D 地球和 2D/3D 同屏，直接渲染平台点位和航路。
- 实时态势帧接口：`/ws/afsim/realtime` 推送统一态势帧，当前先基于解析航路生成预览帧，后续可替换为 `mission.exe` 实时输出解析。
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

1. 在左侧“场景设计器”编辑平台，也可以从模板库添加平台。
2. 点击“保存”，检查中间的 2D/3D 地图、`scenario.txt` 和右侧解析摘要。
3. 点击“运行场景”，后端调用 `mission.exe`，输出文件会进入 `runtime/afsim_runs`。
4. 在“官方 AFSIM 场景”中选择 demo，点击“解析”可把官方输入文件展示到网页地图中。
5. 点击地图工具条里的 `2D`、`3D`、`分屏` 切换显示方式，点击 `实时` 接收态势帧流。
6. 在“实时指挥 Agent”中选择指挥方、步长和人工/自动模式，点击“单步指挥”或“启动循环”。
7. 点击“分析最近运行”，让大模型分析输出链路、错误和后续仿真工程步骤。

## 主要接口

- `GET /api/health`
- `GET /api/afsim/status`
- `GET /api/afsim/demos`
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

## 下一步

- 扩展 AFSIM 输入语法解析，覆盖更多 `platform_type`、传感器、武器、通信和任务配置。
- 解析 `.evt/.aer`，把运行后的时序态势转换为可回放的网页数据。
- 接入 AFSIM `event_output`、`csv_event_output`、DIS 或 event pipe，把预览帧替换为权威仿真输出帧。
- 复用 `resources/maps` 和 `resources/models/mil-std2525d`，继续提高地图底图、军标和符号风格的一致性。
- 增加图层开关、时间轴、实体检索和运行后回放。
- 增加场景版本管理、批量导入、运行记录索引和复盘库。
