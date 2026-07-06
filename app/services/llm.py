from __future__ import annotations

import json
import re
from typing import Any

from pydantic import ValidationError

from app.core.config import settings
from app.models import CommanderCommand, CommanderRequest, CommanderResponse, StateSnapshot


ALLOWED_ACTIONS = {
    "set_heading",
    "set_speed",
    "set_altitude",
    "set_sensor",
    "assign_track",
    "annotate",
    "no_op",
}


SYSTEM_PROMPT = """你是 AFSIM_LLM 的封闭仿真指挥 Agent。
你只能在军事仿真沙盘中工作，输出必须是 JSON，不要使用 Markdown。

输出格式：
{
  "summary": "一句话态势判断",
  "commands": [
    {
      "action": "set_heading|set_speed|set_altitude|set_sensor|assign_track|annotate|no_op",
      "unit_id": "可选，必须来自当前态势 units",
      "value": 数字或字符串或布尔值,
      "reason": "仿真内原因"
    }
  ]
}

约束：
1. 只能做仿真态势管理、传感器管理、机动航迹调整、目标分配、标注和复盘建议。
2. 不输出真实世界作战命令，不输出武器释放、杀伤、规避拦截或现实伤害性步骤。
3. 指令必须引用当前态势中存在的 unit_id；不确定时输出 annotate 或 no_op。
4. 优先保持传感器覆盖连续性、提高态势感知、降低不确定性。
5. 每轮最多输出 6 条命令，避免频繁大幅调整。"""


AFSIM_ANALYSIS_PROMPT = """你是 AFSIM_LLM 的仿真工程分析 Agent。
你只分析封闭仿真运行结果，输出 JSON，不要使用 Markdown。

输出格式：
{
  "summary": "一句话工程结论",
  "findings": ["问题或发现"],
  "next_steps": ["下一步仿真工程建议"],
  "risk_level": "low|medium|high"
}

不要提供真实世界作战、武器使用或伤害性建议。"""


def _extract_json(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("{"):
        return json.loads(text)
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError("model response did not contain JSON")
    return json.loads(match.group(0))


class CommanderLLM:
    async def advise(self, request: CommanderRequest, state: StateSnapshot) -> CommanderResponse:
        if settings.siliconflow_api_key:
            try:
                return await self._siliconflow(request, state)
            except Exception as exc:
                fallback = self._local_rule(request, state)
                fallback.summary = f"硅基流动调用失败，已使用本地规则：{exc}"
                return fallback
        return self._local_rule(request, state)

    async def _siliconflow(self, request: CommanderRequest, state: StateSnapshot) -> CommanderResponse:
        try:
            import httpx
        except ImportError as exc:
            raise RuntimeError("httpx is not installed; install requirements.txt before calling SiliconFlow") from exc

        url = f"{settings.siliconflow_base_url.rstrip('/')}/chat/completions"
        compact_state = {
            "objective": request.objective,
            "side": request.side,
            "sim_time": state.sim_time,
            "running": state.running,
            "speed_factor": state.speed_factor,
            "detections": state.detections[-20:],
            "events": state.events[-20:],
            "units": [
                {
                    "id": unit.id,
                    "name": unit.name,
                    "side": unit.side,
                    "kind": unit.kind,
                    "position": unit.position.model_dump(),
                    "speed_kps": unit.speed_kps,
                    "heading_deg": unit.heading_deg,
                    "altitude_km": unit.altitude_km,
                    "sensors": [sensor.model_dump() for sensor in unit.sensors],
                    "metadata": unit.metadata,
                }
                for unit in state.units
            ],
        }
        payload = {
            "model": settings.siliconflow_model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(compact_state, ensure_ascii=False)},
            ],
            "temperature": 0.2,
            "max_tokens": 900,
        }
        async with httpx.AsyncClient(timeout=settings.llm_timeout_seconds, trust_env=False) as client:
            response = await client.post(
                url,
                headers={
                    "Authorization": f"Bearer {settings.siliconflow_api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            response.raise_for_status()
            data = response.json()
        text = data["choices"][0]["message"]["content"]
        parsed = _extract_json(text)
        return CommanderResponse(
            source="siliconflow",
            summary=str(parsed.get("summary", "已生成仿真指挥建议")),
            commands=self._sanitize_commands(parsed.get("commands", [])),
            raw_text=text,
        )

    def _sanitize_commands(self, raw_commands: list[Any]) -> list[CommanderCommand]:
        commands: list[CommanderCommand] = []
        for raw in raw_commands[:6]:
            if not isinstance(raw, dict) or raw.get("action") not in ALLOWED_ACTIONS:
                continue
            try:
                commands.append(CommanderCommand.model_validate(raw))
            except ValidationError:
                continue
        return commands or [CommanderCommand(action="no_op", reason="未得到可执行的白名单仿真命令")]

    def _local_rule(self, request: CommanderRequest, state: StateSnapshot) -> CommanderResponse:
        own_units = [unit for unit in state.units if unit.side == request.side]
        radar_units = [unit for unit in own_units if unit.sensors and any(s.type in {"radar", "esm"} for s in unit.sensors)]
        mobile_units = [unit for unit in own_units if unit.kind in {"aircraft", "ship", "satellite"}]
        commands: list[CommanderCommand] = []

        if state.detections:
            first = state.detections[0]
            commands.append(
                CommanderCommand(
                    action="annotate",
                    value=f"持续跟踪 {first['target_name']}，当前置信度 {first['confidence']}",
                    reason="已有探测链路，优先保持态势连续性",
                )
            )
        elif radar_units:
            radar = radar_units[0]
            commands.append(
                CommanderCommand(
                    action="set_sensor",
                    unit_id=radar.id,
                    value=True,
                    reason="当前无探测结果，保持主传感器搜索",
                )
            )

        if mobile_units:
            unit = mobile_units[0]
            new_heading = (unit.heading_deg + 12) % 360
            commands.append(
                CommanderCommand(
                    action="set_heading",
                    unit_id=unit.id,
                    value=new_heading,
                    reason="小幅调整航向以扩展仿真搜索覆盖",
                )
            )

        if not commands:
            commands.append(CommanderCommand(action="no_op", reason="当前无可调整单元"))

        return CommanderResponse(
            source="local_rule",
            summary=f"本地规则已根据目标“{request.objective[:40]}”生成实时仿真建议。",
            commands=commands,
        )

    async def analyze_afsim_run(self, run: dict[str, Any]) -> dict[str, Any]:
        if settings.siliconflow_api_key:
            try:
                return await self._siliconflow_afsim_analysis(run)
            except Exception as exc:
                fallback = self._local_afsim_analysis(run)
                fallback["summary"] = f"硅基流动调用失败，已使用本地分析：{exc}"
                return fallback
        return self._local_afsim_analysis(run)

    async def _siliconflow_afsim_analysis(self, run: dict[str, Any]) -> dict[str, Any]:
        try:
            import httpx
        except ImportError as exc:
            raise RuntimeError("httpx is not installed; install requirements.txt before calling SiliconFlow") from exc

        url = f"{settings.siliconflow_base_url.rstrip('/')}/chat/completions"
        compact = {
            "demo_name": run.get("demo_name"),
            "scenario_id": run.get("scenario_id"),
            "input_file": run.get("input_file"),
            "returncode": run.get("returncode"),
            "duration_seconds": run.get("duration_seconds"),
            "files": run.get("files", []),
            "summary": run.get("summary", {}),
            "stdout_tail": str(run.get("stdout", ""))[-5000:],
            "stderr_tail": str(run.get("stderr", ""))[-5000:],
        }
        payload = {
            "model": settings.siliconflow_model,
            "messages": [
                {"role": "system", "content": AFSIM_ANALYSIS_PROMPT},
                {"role": "user", "content": json.dumps(compact, ensure_ascii=False)},
            ],
            "temperature": 0.2,
            "max_tokens": 900,
        }
        async with httpx.AsyncClient(timeout=settings.llm_timeout_seconds, trust_env=False) as client:
            response = await client.post(
                url,
                headers={
                    "Authorization": f"Bearer {settings.siliconflow_api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            response.raise_for_status()
            data = response.json()
        text = data["choices"][0]["message"]["content"]
        parsed = _extract_json(text)
        return {
            "source": "siliconflow",
            "summary": str(parsed.get("summary", "AFSIM 运行结果已分析")),
            "findings": parsed.get("findings", []),
            "next_steps": parsed.get("next_steps", []),
            "risk_level": parsed.get("risk_level", "low"),
            "raw_text": text,
        }

    def _local_afsim_analysis(self, run: dict[str, Any]) -> dict[str, Any]:
        summary = run.get("summary", {})
        findings: list[str] = []
        if run.get("returncode") == 0:
            findings.append("AFSIM mission.exe 返回码为 0，运行链路可用。")
        else:
            findings.append(f"AFSIM 返回码为 {run.get('returncode')}，需要检查 stderr 和 log。")
        if summary.get("completed"):
            findings.append("日志显示仿真已经完成。")
        if summary.get("fatal"):
            findings.extend([f"致命错误：{item}" for item in summary.get("fatal", [])[:3]])
        files = run.get("files", [])
        findings.append(f"采集到 {len(files)} 个输出文件。")
        return {
            "source": "local_rule",
            "summary": f"AFSIM 场景 {run.get('demo_name') or run.get('scenario_id')} 已完成基础工程分析。",
            "findings": findings,
            "next_steps": [
                "将该场景纳入场景库版本管理。",
                "继续解析 log、evt、aer 输出，形成时间序列态势数据。",
                "用实时指挥 Agent 对关键传感器、机动单元和任务目标进行闭环验证。",
            ],
            "risk_level": "low" if run.get("returncode") == 0 else "medium",
            "raw_text": "",
        }
