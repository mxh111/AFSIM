# AFSIM_LLM

AFSIM_LLM 是一个面向 AFSIM 二次开发的初始版本工程，目标是逐步建设“以大模型为核心指挥”的浏览器化军事仿真平台。

当前版本已经实现：

- 浏览器主控界面、二维态势图、图层显隐、装备列表、事件日志。
- 轻量仿真内核：仿真启动/暂停/继续/单步/加速/减速/复位、目标运动、雷达探测、干扰影响。
- 大模型指挥接口：硅基流动 OpenAI 兼容 API 调用，未配置 Key 时自动使用本地规则指挥官。
- 数据存储：SQLite 事件、快照、复盘报告。
- AFSIM 适配层：识别本机 AFSIM 目录，并导出初版 AFSIM scenario 草案。

## 快速启动

```powershell
cd D:\AFISM\AFSIM\AFSIM_LLM
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

如默认源或清华源被代理影响，可以使用阿里云源：

```powershell
$env:NO_PROXY="*"
python -m pip install -r requirements.txt --index-url https://mirrors.aliyun.com/pypi/simple/ --trusted-host mirrors.aliyun.com
```

浏览器打开：

```text
http://127.0.0.1:8000
```

如果 8000 端口已被占用，可以换端口，例如：

```powershell
python -m uvicorn app.main:app --host 127.0.0.1 --port 8766
```

也可以使用脚本：

```powershell
.\scripts\run_dev.ps1
```

如果当前机器暂时无法安装 FastAPI/uvicorn 依赖，可以用轻量预览服务器先打开界面：

```powershell
C:\Users\14191\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe .\scripts\run_preview.py --host 127.0.0.1 --port 8000
```

预览服务器提供主要演示 API，不包含 FastAPI 的 WebSocket；前端会自动切换为 0.5 秒轮询。

## 配置硅基流动

复制 `.env.example` 为 `.env` 或在 PowerShell 中设置环境变量：

```powershell
$env:SILICONFLOW_API_KEY="你的 API Key"
$env:SILICONFLOW_BASE_URL="https://api.siliconflow.cn/v1"
$env:SILICONFLOW_MODEL="Pro/zai-org/GLM-4.7"
```

接口采用 `/v1/chat/completions` OpenAI 兼容格式。模型名可按硅基流动控制台中实际可用模型调整。

## 项目结构

```text
app/
  main.py                 # FastAPI API、WebSocket、静态页面入口
  models.py               # 场景、目标、图层、命令数据模型
  services/
    simulation.py         # 轻量仿真内核
    llm.py                # 硅基流动 / 本地规则指挥官
    afsim_adapter.py      # AFSIM 草案导出与集成探测
    reports.py            # 复盘报告
    storage.py            # SQLite 存储
  static/                 # 浏览器界面
configs/sample_scenario.json
docs/ARCHITECTURE.md
tests/
```

## 说明

当前是“小规模初始版本”，不是完整 AFSIM 替代品。它先把平台主线跑通：场景输入、仿真控制、态势显示、LLM 指挥建议、人工/自动应用、复盘导出、AFSIM 文件草案。后续可以沿 `docs/ARCHITECTURE.md` 中路线接入真实 AFSIM runner、Cesium/WebGL 三维数字地球、数据库集群、模型库和安全审计。
