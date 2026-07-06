from pathlib import Path

from app.models import CommanderCommand
from app.models import AFSimDesignedPlatform, AFSimRoutePoint, AFSimScenarioDesign
from app.services.afsim_design import generate_scenario, read_generated_scenario
from app.services.afsim_parser import parse_demo_scenario
from app.services.afsim_parser import parse_scenario_file
from app.services.afsim_runner import discover_demos, run_generated_scenario, status
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


def test_afsim_installation_is_discoverable():
    afsim_status = status()
    assert afsim_status["root_exists"]
    assert afsim_status["mission_exists"]
    demos = discover_demos()
    assert any(demo["name"] == "simple_scenario" for demo in demos)


def test_afsim_scenario_parser_extracts_platforms():
    parsed = parse_demo_scenario("simple_scenario", "simple_scenario.txt")
    assert parsed["platform_count"] >= 1
    striker = next(platform for platform in parsed["platforms"] if platform["id"] == "SimpleStriker")
    assert striker["side"] == "blue"
    assert striker["positions"][0]["lat"] == 1.05
    assert parsed["included_files"]
    assert parsed["bounds"] is not None
    assert parsed["geojson"]["type"] == "FeatureCollection"
    assert parsed["geojson"]["features"]
    assert "route_count" in parsed


def test_generated_afsim_design_runs_with_mission():
    design = AFSimScenarioDesign(
        name="pytest_generated_design",
        end_time_seconds=30,
        platforms=[
            AFSimDesignedPlatform(
                name="Blue_Test",
                side="blue",
                category="fighter",
                icon="F-22",
                lat=1.05,
                lon=1.05,
                altitude_m=9000,
                speed_kts=320,
                heading_deg=90,
                route=[AFSimRoutePoint(lat=1.05, lon=1.12, altitude_m=9000, speed_kts=320)],
            ),
            AFSimDesignedPlatform(
                name="Red_Test",
                side="red",
                category="fighter",
                icon="SU-27",
                lat=1.2,
                lon=1.3,
                altitude_m=8500,
                speed_kts=330,
                heading_deg=270,
                route=[AFSimRoutePoint(lat=1.2, lon=1.22, altitude_m=8500, speed_kts=330)],
            ),
        ],
    )
    generated = generate_scenario(design)
    loaded = read_generated_scenario(generated["scenario_id"])
    parsed = parse_scenario_file(Path(generated["scenario_path"]))
    run = run_generated_scenario(generated["scenario_id"], 30)

    assert loaded["scenario_id"] == generated["scenario_id"]
    assert parsed["platform_count"] == 2
    assert parsed["bounds"] is not None
    assert parsed["route_count"] == 2
    assert len(parsed["geojson"]["features"]) >= 4
    assert run["returncode"] == 0
    assert any(file["name"].endswith(".aer") for file in run["files"])
