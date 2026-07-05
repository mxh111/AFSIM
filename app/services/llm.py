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


SYSTEM_PROMPT = """你是 AFSIM_LLM 的仿真指挥智能体，只能在封闭仿真沙盘中工作。
输出必须是 JSON，不要使用 Markdown。格式：
{
  "summary": "一句话态势判断",
  "commands": [
    {"action": "set_heading|set_speed|set_altitude|set_sensor|assign_track|annotate|no_op", "unit_id": "可选", "value": 数字或字符串或布尔, "reason": "原因"}
  ]
}
约束：
1. 只做仿真态势管理、传感器管理、机动/航迹调整、注记和复盘建议。
2. 不生成真实世界作战指令，不生成武器释放、打击、杀伤、规避拦截细节。
3. 优先保护己方传感器覆盖连续性、提高态势感知、降低不确定性。
4. 命令必须引用当前态势中存在的 unit_id；不确定时输出 no_op 或 annotate。
"""


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
            except Exception as exc:  # Keep the operator console alive if the model call fails.
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
            "detections": state.detections[-20:],
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
        async with httpx.AsyncClient(timeout=settings.llm_timeout_seconds) as client:
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
        commands = self._sanitize_commands(parsed.get("commands", []))
        return CommanderResponse(
            source="siliconflow",
            summary=str(parsed.get("summary", "已生成仿真指挥建议")),
            commands=commands,
            raw_text=text,
        )

    def _sanitize_commands(self, raw_commands: list[Any]) -> list[CommanderCommand]:
        commands: list[CommanderCommand] = []
        for raw in raw_commands[:8]:
            if not isinstance(raw, dict) or raw.get("action") not in ALLOWED_ACTIONS:
                continue
            try:
                commands.append(CommanderCommand.model_validate(raw))
            except ValidationError:
                continue
        return commands or [CommanderCommand(action="no_op", reason="未得到可执行的白名单命令")]

    def _local_rule(self, request: CommanderRequest, state: StateSnapshot) -> CommanderResponse:
        own_units = [unit for unit in state.units if unit.side == request.side]
        radar_units = [unit for unit in own_units if unit.sensors and any(s.type == "radar" for s in unit.sensors)]
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
                    reason="暂无探测结果，保持主雷达搜索",
                )
            )

        if mobile_units:
            unit = mobile_units[0]
            new_heading = (unit.heading_deg + 18) % 360
            commands.append(
                CommanderCommand(
                    action="set_heading",
                    unit_id=unit.id,
                    value=new_heading,
                    reason="小幅调整航向以扩展搜索覆盖",
                )
            )

        if not commands:
            commands.append(CommanderCommand(action="no_op", reason="当前无可调整单元"))

        return CommanderResponse(
            source="local_rule",
            summary=f"本地规则已根据目标“{request.objective[:40]}”生成仿真内建议。",
            commands=commands,
        )
