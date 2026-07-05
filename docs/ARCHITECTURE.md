# AFSIM_LLM 初版架构

## 目标

当前版本先实现一个小规模、可运行的军事仿真平台骨架，用于验证“浏览器态势显示 + LLM 指挥核心 + 仿真控制 + AFSIM 适配层”的闭环。

## 模块

- `app/main.py`：FastAPI 应用入口，提供 REST API、WebSocket、静态页面。
- `app/services/simulation.py`：轻量仿真内核，负责仿真时间、目标运动、雷达探测、事件记录。
- `app/services/llm.py`：硅基流动 OpenAI 兼容调用适配器。未配置 API Key 时自动使用本地规则指挥官。
- `app/services/afsim_adapter.py`：AFSIM 集成适配层，当前可导出初版草案文件，后续可接入真实 runner、grammar 校验和结果解析。
- `app/static/`：浏览器端主控界面，包含二维态势、图层显隐、装备列表、事件日志、LLM 指挥台。
- `configs/sample_scenario.json`：初始想定，覆盖雷达、预警机、临近空间目标、干扰源、卫星、图层。
- `app/services/storage.py`：SQLite 事件、快照、报告存储。

## LLM 指挥安全边界

指挥智能体只允许输出白名单动作：

- `set_heading`
- `set_speed`
- `set_altitude`
- `set_sensor`
- `assign_track`
- `annotate`
- `no_op`

平台不会执行真实世界作战命令，也不会提供武器释放或杀伤动作。后续接入真实 AFSIM 时也应保持“模型建议、系统校验、人工确认、仿真执行”的链路。

## 后续路线

1. 接入真实 AFSIM `bin`、scenario 文件和输出解析。
2. 将 Canvas 二维态势替换或扩展为 Cesium/WebGL 三维数字地球。
3. 引入数据库适配层，支持 MySQL/SQL Server/国产数据库。
4. 建立模型库、素材库、30+ 图层模板和批量导入。
5. 增加 DOCX 复盘模板、xlsx/csv 导出、安全审计与脱敏。
