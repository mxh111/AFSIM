from app.models import CommanderCommand
from app.services.simulation import SimulationEngine
from app.services.storage import Storage


def test_engine_steps_and_detects(tmp_path):
    engine = SimulationEngine(Storage(tmp_path / "test.sqlite3"))
    initial_time = engine.sim_time
    engine.step(1.0)
    assert engine.sim_time == initial_time + 1.0
    assert isinstance(engine.detections, list)
    assert engine.snapshot().scenario_id == "demo_air_space_radar"


def test_apply_commander_command(tmp_path):
    engine = SimulationEngine(Storage(tmp_path / "test.sqlite3"))
    result = engine.apply_commands(
        [CommanderCommand(action="set_heading", unit_id="blue_awacs", value=123.0, reason="test")]
    )
    assert result[0]["status"] == "ok"
    unit = next(unit for unit in engine.scenario.units if unit.id == "blue_awacs")
    assert unit.heading_deg == 123.0
