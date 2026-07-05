from __future__ import annotations

import copy
import json
import math
import time
from pathlib import Path
from typing import Any

from app.models import CommanderCommand, Layer, Scenario, StateSnapshot, TrackPoint, Unit
from app.services.storage import Storage


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SCENARIO_PATH = PROJECT_ROOT / "configs" / "sample_scenario.json"


def _distance(a: Unit, b: Unit) -> float:
    dx = a.position.x - b.position.x
    dy = a.position.y - b.position.y
    dz = a.position.z - b.position.z
    return math.sqrt(dx * dx + dy * dy + dz * dz)


def _weather_factor(weather: str) -> float:
    return {
        "clear": 1.0,
        "cloud": 0.92,
        "fog": 0.72,
        "rain": 0.78,
        "snow": 0.68,
        "wind": 0.88,
    }.get(weather, 0.9)


class SimulationEngine:
    def __init__(self, storage: Storage) -> None:
        self.storage = storage
        self.scenario = self._load_scenario(DEFAULT_SCENARIO_PATH)
        self._baseline = copy.deepcopy(self.scenario)
        self.sim_time = 0.0
        self.running = False
        self.speed_factor = 1.0
        self.events: list[dict[str, Any]] = []
        self.detections: list[dict[str, Any]] = []
        self._last_wall = time.time()

    def _load_scenario(self, path: Path) -> Scenario:
        return Scenario.model_validate_json(path.read_text(encoding="utf-8"))

    def load_scenario(self, path: Path) -> Scenario:
        self.scenario = self._load_scenario(path)
        self._baseline = copy.deepcopy(self.scenario)
        self.sim_time = 0.0
        self.running = False
        self._log("scenario.load", {"scenario_id": self.scenario.id, "path": str(path)})
        return self.scenario

    def control(self, action: str, step_seconds: float | None = None) -> StateSnapshot:
        if action == "start":
            self.running = True
            self.sim_time = 0.0
            self._last_wall = time.time()
        elif action == "pause":
            self.running = False
        elif action == "resume":
            self.running = True
            self._last_wall = time.time()
        elif action == "step":
            self.step(step_seconds or self.scenario.step_seconds)
        elif action == "reset":
            self.scenario = copy.deepcopy(self._baseline)
            self.sim_time = 0.0
            self.running = False
            self.events.clear()
            self.detections.clear()
        elif action == "stop":
            self.running = False
        elif action == "faster":
            self.speed_factor = min(16.0, self.speed_factor * 2)
        elif action == "slower":
            self.speed_factor = max(0.25, self.speed_factor / 2)
        self._log("sim.control", {"action": action, "step_seconds": step_seconds})
        return self.snapshot()

    def tick_from_wall_clock(self) -> StateSnapshot:
        now = time.time()
        elapsed = now - self._last_wall
        self._last_wall = now
        if self.running:
            self.step(max(0.0, elapsed * self.speed_factor))
        return self.snapshot()

    def step(self, dt: float) -> None:
        self.sim_time += dt
        for unit in self.scenario.units:
            if unit.speed_kps <= 0 or unit.kind in {"radar", "jammer", "c2"}:
                continue
            radians = math.radians(unit.heading_deg)
            unit.position.x += math.sin(radians) * unit.speed_kps * dt
            unit.position.y -= math.cos(radians) * unit.speed_kps * dt
            unit.position.z = unit.altitude_km
            unit.route.append(
                TrackPoint(t=self.sim_time, x=unit.position.x, y=unit.position.y, z=unit.position.z)
            )
            if len(unit.route) > 240:
                unit.route = unit.route[-240:]
        self._update_detections()
        if int(self.sim_time) % 10 == 0:
            self.storage.add_snapshot(self.scenario.id, self.sim_time, self.snapshot().model_dump())

    def _update_detections(self) -> None:
        detections: list[dict[str, Any]] = []
        weather = _weather_factor(self.scenario.weather)
        jammers = [u for u in self.scenario.units if u.kind == "jammer" and u.visible]
        for sensor_unit in self.scenario.units:
            for sensor in sensor_unit.sensors:
                if not sensor.enabled or sensor.type not in {"radar", "esm"}:
                    continue
                for target in self.scenario.units:
                    if target.side == sensor_unit.side or target.id == sensor_unit.id or not target.visible:
                        continue
                    jammer_factor = 1.0
                    for jammer in jammers:
                        if jammer.side != sensor_unit.side and _distance(sensor_unit, jammer) < 180:
                            jammer_factor -= 0.18 * max(0.2, jammer.sensors[0].power if jammer.sensors else 0.6)
                    effective_range = sensor.range_km * sensor.power * weather * max(0.42, jammer_factor)
                    distance = _distance(sensor_unit, target)
                    if distance <= effective_range:
                        detections.append(
                            {
                                "sensor": sensor_unit.id,
                                "sensor_name": sensor_unit.name,
                                "target": target.id,
                                "target_name": target.name,
                                "distance_km": round(distance, 2),
                                "confidence": round(max(0.12, 1 - distance / max(effective_range, 1)), 2),
                                "jammed": jammer_factor < 0.95,
                            }
                        )
        self.detections = detections

    def apply_commands(self, commands: list[CommanderCommand]) -> list[dict[str, Any]]:
        applied: list[dict[str, Any]] = []
        unit_index = {unit.id: unit for unit in self.scenario.units}
        for command in commands:
            if command.action == "no_op":
                applied.append({"action": "no_op", "status": "skipped", "reason": command.reason})
                continue
            if command.action == "annotate":
                self._log("annotation", {"text": command.value, "reason": command.reason})
                applied.append({"action": "annotate", "status": "ok"})
                continue
            unit = unit_index.get(command.unit_id or "")
            if not unit:
                applied.append({"action": command.action, "status": "error", "reason": "unit not found"})
                continue
            if command.action == "set_heading" and isinstance(command.value, (int, float)):
                unit.heading_deg = float(command.value) % 360
            elif command.action == "set_speed" and isinstance(command.value, (int, float)):
                unit.speed_kps = max(0.0, min(float(command.value), 12.0))
            elif command.action == "set_altitude" and isinstance(command.value, (int, float)):
                unit.altitude_km = max(0.0, min(float(command.value), 1200.0))
            elif command.action == "set_sensor":
                enabled = bool(command.value)
                for sensor in unit.sensors:
                    sensor.enabled = enabled
            elif command.action == "assign_track" and isinstance(command.value, str):
                unit.metadata["assignment"] = command.value[:160]
            else:
                applied.append({"action": command.action, "status": "ignored", "reason": "invalid value"})
                continue
            applied.append({"action": command.action, "unit_id": unit.id, "status": "ok", "reason": command.reason})
        if applied:
            self._log("commander.apply", {"commands": applied})
        return applied

    def set_layer_visibility(self, layer_id: str, visible: bool) -> Layer | None:
        for layer in self.scenario.layers:
            if layer.id == layer_id:
                layer.visible = visible
                self._log("layer.visibility", {"layer_id": layer_id, "visible": visible})
                return layer
        return None

    def snapshot(self) -> StateSnapshot:
        return StateSnapshot(
            scenario_id=self.scenario.id,
            sim_time=round(self.sim_time, 2),
            running=self.running,
            speed_factor=self.speed_factor,
            detections=self.detections,
            units=self.scenario.units,
            layers=self.scenario.layers,
            events=self.events[-80:],
        )

    def export_json(self) -> str:
        return self.scenario.model_dump_json(indent=2)

    def _log(self, category: str, payload: dict[str, Any]) -> None:
        item = {"t": round(self.sim_time, 2), "category": category, "payload": payload}
        self.events.append(item)
        self.storage.add_event(category, item)
