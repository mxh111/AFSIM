from __future__ import annotations

from datetime import datetime
from pathlib import Path

from app.models import StateSnapshot


def build_report(snapshot: StateSnapshot) -> dict[str, object]:
    blue = [unit for unit in snapshot.units if unit.side == "blue"]
    red = [unit for unit in snapshot.units if unit.side == "red"]
    return {
        "title": f"{snapshot.scenario_id} 复盘报告",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "sim_time": snapshot.sim_time,
        "running": snapshot.running,
        "summary": {
            "blue_units": len(blue),
            "red_units": len(red),
            "detections": len(snapshot.detections),
            "events": len(snapshot.events),
        },
        "detections": snapshot.detections,
        "recent_events": snapshot.events[-20:],
        "assessment": [
            "态势链路以传感器探测、目标轨迹和事件日志为主。",
            "当前初版报告为结构化 JSON/Markdown，可继续扩展为 docx 模板。",
            "建议后续接入真实 AFSIM 输出和信号级数据采集结果。",
        ],
    }


def write_markdown_report(report: dict[str, object], output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    title = str(report["title"])
    path = output_dir / f"{title.replace(' ', '_')}.md"
    summary = report["summary"]
    lines = [
        f"# {title}",
        "",
        f"- 生成时间：{report['generated_at']}",
        f"- 仿真时间：{report['sim_time']} s",
        f"- 运行状态：{report['running']}",
        "",
        "## 摘要",
        "",
        f"- 蓝方单元：{summary['blue_units']}",
        f"- 红方单元：{summary['red_units']}",
        f"- 探测记录：{summary['detections']}",
        f"- 事件数量：{summary['events']}",
        "",
        "## 评估建议",
        "",
    ]
    lines.extend(f"- {item}" for item in report["assessment"])
    path.write_text("\n".join(lines), encoding="utf-8")
    return path
