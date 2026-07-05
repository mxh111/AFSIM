from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class Position(BaseModel):
    x: float
    y: float
    z: float = 0.0


class SensorModel(BaseModel):
    name: str
    type: Literal["radar", "eo", "comms", "esm", "jammer"] = "radar"
    range_km: float = 120.0
    power: float = 0.7
    enabled: bool = True


class TrackPoint(BaseModel):
    t: float
    x: float
    y: float
    z: float = 0.0


class Unit(BaseModel):
    id: str
    name: str
    side: Literal["blue", "red", "neutral"]
    kind: Literal["aircraft", "missile", "satellite", "ship", "ground", "radar", "jammer", "c2"]
    position: Position
    speed_kps: float = 0.0
    heading_deg: float = 0.0
    altitude_km: float = 0.0
    health: float = 1.0
    visible: bool = True
    sensors: list[SensorModel] = Field(default_factory=list)
    route: list[TrackPoint] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class Layer(BaseModel):
    id: str
    name: str
    group: Literal["base", "deployment", "dynamic", "environment", "intelligence"]
    visible: bool = True
    opacity: float = 1.0
    features: list[dict[str, Any]] = Field(default_factory=list)


class Scenario(BaseModel):
    id: str
    name: str
    domain: Literal["earth", "near_space", "space"]
    description: str
    terrain: str = "ocean"
    weather: str = "clear"
    step_seconds: float = 1.0
    units: list[Unit]
    layers: list[Layer]


class SimulationControl(BaseModel):
    action: Literal["start", "pause", "resume", "step", "reset", "stop", "faster", "slower"]
    step_seconds: float | None = None


class CommanderRequest(BaseModel):
    objective: str = Field(..., min_length=2, max_length=1200)
    side: Literal["blue", "red"] = "blue"
    autonomy: Literal["manual_review", "auto_apply"] = "manual_review"


class CommanderCommand(BaseModel):
    action: Literal[
        "set_heading",
        "set_speed",
        "set_altitude",
        "set_sensor",
        "assign_track",
        "annotate",
        "no_op",
    ]
    unit_id: str | None = None
    value: float | str | bool | None = None
    reason: str = ""


class CommanderResponse(BaseModel):
    source: Literal["siliconflow", "local_rule"]
    summary: str
    commands: list[CommanderCommand]
    raw_text: str = ""


class StateSnapshot(BaseModel):
    scenario_id: str
    sim_time: float
    running: bool
    speed_factor: float
    detections: list[dict[str, Any]]
    units: list[Unit]
    layers: list[Layer]
    events: list[dict[str, Any]]
