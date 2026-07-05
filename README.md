# AFSIM_LLM

AFSIM_LLM 是一个面向 AFSIM 二次开发的本机 Web 工作台。当前版本已经把主线改为 AFSIM 原生路线：网页负责场景设计、运行调度、输出采集和大模型分析；地图/三维/回放以 Warlock、Mystic 等 AFSIM 原生工具为准。

## 已完成能力

- 网页端场景设计器：配置平台、阵营、类型、图标、经纬度、高度、速度、航向和航路终点。
- 生成真实 AFSIM 输入文件：写入 `generated_scenarios/<scenario_id>/scenario.txt`。
- 调用 AFSIM `mission.exe` 运行生成场景或官方 demo。
- 自动采集 `.log`、`.evt`、`.aer` 到 `runtime/afsim_runs`。
- 网页按钮启动 Warlock 打开生成场景或官方 demo。
- 网页按钮启动 Mystic 打开最近一次 `.aer` 回放。
- 网页中显示 Warlock/Mystic 原生窗口截图；如果配置 `AFSIM_NATIVE_STREAM_URL`，可切换为 noVNC/RDP/WebRTC 等交互式嵌入流。
- 调用硅基流动 OpenAI 兼容 API，对 AFSIM 运行结果做仿真工程分析。

## 启动

```powershell
cd D:\AFISM\AFSIM\AFSIM_LLM
.\scripts\run_dev.ps1
```

然后打开：

```text
http://127.0.0.1:8766
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
AFSIM_NATIVE_STREAM_URL=
SILICONFLOW_BASE_URL=https://api.siliconflow.cn/v1
SILICONFLOW_MODEL=deepseek-ai/DeepSeek-V3
SILICONFLOW_API_KEY=你的Key
```

`AFSIM_NATIVE_STREAM_URL` 留空时，网页使用本机窗口截图显示 Warlock/Mystic；配置为 noVNC/RDP/WebRTC 页面地址时，中间工作区会以 iframe 方式嵌入该交互流。

## 使用流程

1. 在左侧“场景设计器”编辑平台。
2. 点击“生成AFSIM场景”，检查中间的 `scenario.txt`。
3. 点击“运行生成场景”，后端调用 `mission.exe`。
4. 点击“打开Warlock”，使用 AFSIM 原生地图打开该场景。
5. 运行后点击“打开Mystic回放”，查看 `.aer`。
6. 点击“分析最近运行”，让大模型分析输出链路、错误和后续仿真工程步骤。

## 主要接口

- `GET /api/afsim/status`
- `GET /api/afsim/demos`
- `GET /api/afsim/designs`
- `POST /api/afsim/designs`
- `GET /api/afsim/designs/{scenario_id}`
- `POST /api/afsim/designs/{scenario_id}/run`
- `POST /api/afsim/designs/{scenario_id}/launch-map`
- `POST /api/afsim/run`
- `POST /api/afsim/launch-map`
- `POST /api/afsim/launch-3d`
- `GET /api/afsim/native-display`
- `GET /api/afsim/native-frame.jpg`
- `POST /api/afsim/analyze`

## 边界

当前网页不会自绘战场地图，也不再把旧 2D/3D demo 当作主界面。Warlock/Mystic 是权威显示端。内置窗口截图是本机可落地显示桥；若要完整交互式网页内嵌，需要部署 noVNC、RDP 网关或 WebRTC 桌面流，并把地址写入 `AFSIM_NATIVE_STREAM_URL`。
