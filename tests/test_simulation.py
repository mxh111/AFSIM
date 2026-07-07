from pathlib import Path

from app.models import CommanderCommand
from app.models import AFSimDesignedPlatform, AFSimRoutePoint, AFSimScenarioDesign
from app.services.afsim_design import generate_scenario, read_generated_scenario
from app.services.afsim_parser import parse_demo_scenario
from app.services.afsim_parser import parse_scenario_file
from app.services.afsim_realtime import build_realtime_frame
from app.services.afsim_replay import build_latest_replay, build_run_replay
from app.services.afsim_runner import discover_demos, run_generated_scenario, status
from app.services.afsim_workbench import build_workbench_state, default_layer_catalog
from app.services.afsim_workbench import replay_for_run
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


def test_afsim_parser_keeps_warlock_style_semantics():
    parsed = parse_demo_scenario("wargame", "single_player_scenario.txt")
    assert parsed["platform_count"] >= 20
    assert len(parsed["platform_types"]) >= 8
    bravo = next(platform for platform in parsed["platforms"] if platform["id"] == "BravoBlue")
    assert bravo["route_metadata"]["labels"][0]["name"] == "bravo_loiter"
    assert bravo["route_metadata"]["gotos"][0]["target"] == "bravo_loiter"
    assert bravo["sensors"]
    assert bravo["weapons"]


def test_afsim_realtime_frame_uses_parsed_routes():
    parsed = parse_demo_scenario("simple_scenario", "simple_scenario.txt")
    frame = build_realtime_frame(parsed, 12.5, frame_id=7, loop_seconds=60)
    assert frame["frame_id"] == 7
    assert frame["source"] == "parser-preview"
    assert frame["authoritative"] is False
    assert frame["entity_count"] == len(frame["entities"])
    assert frame["entities"]
    first = frame["entities"][0]
    assert {"id", "side", "lat", "lon", "route"}.issubset(first)


def test_afsim_workbench_layer_catalog_has_required_groups():
    layers = default_layer_catalog()
    groups = {layer["group"] for layer in layers}
    assert len(layers) >= 30
    assert {
        "base",
        "deployment",
        "dynamic",
        "environment",
        "intelligence",
        "electromagnetic",
        "replay",
    }.issubset(groups)
    assert all({"id", "name", "visible", "opacity", "locked", "queryable"}.issubset(layer) for layer in layers)


def test_afsim_workbench_state_contract_from_demo():
    workbench = build_workbench_state(demo_name="simple_scenario", input_file="simple_scenario.txt")
    required = {
        "platforms",
        "tracks",
        "sensors",
        "weapons",
        "detections",
        "communications",
        "events",
        "layers",
        "simulation_time",
    }
    assert required.issubset(workbench)
    assert workbench["schema_version"] == "afsim-workbench.v1"
    assert workbench["platforms"]
    assert len(workbench["layers"]) >= 30
    assert workbench["stats"]["platform_count"] == len(workbench["platforms"])
    assert "map_pan_zoom" in workbench["capabilities"]
    assert "controlled_scene_patch" in workbench["capabilities"]
    assert workbench["editing_workflow"]["raw_afsim_write"] is False


def test_afsim_event_output_builds_replay_frames(tmp_path):
    evt_path = tmp_path / "sample.evt"
    evt_path.write_text(
        "\n".join(
            [
                "0.00000 SENSOR_DETECTION_ATTEMPT radar_1 target_1 Sensor: radar_1 Mode: default Beam: 1 \\",
                "  Rcvr: Type: TEST_RADAR LLA: 30:00:00.00n 120:00:00.00e 10 m Heading: 0 deg Pitch: 0 deg Roll: 0 deg Speed: 0 m/s \\",
                "  Tgt: Type: TEST_TARGET LLA: 30:06:00.00n 120:06:00.00e 1000 m Heading: 45 deg Pitch: 0 deg Roll: 0 deg Speed: 250 m/s \\",
                "  Rcvr->Tgt: Range: 14.7 km (7.9 nm) Brg: 40 deg El: 1 deg \\",
                "  Pd: 0.9 RequiredPd: 0.5 Detected: 1",
                "1.00000 SENSOR_DETECTION_ATTEMPT radar_1 target_1 Sensor: radar_1 Mode: default Beam: 1 \\",
                "  Rcvr: Type: TEST_RADAR LLA: 30:00:00.00n 120:00:00.00e 10 m Heading: 0 deg Pitch: 0 deg Roll: 0 deg Speed: 0 m/s \\",
                "  Tgt: Type: TEST_TARGET LLA: 30:07:00.00n 120:08:00.00e 1000 m Heading: 45 deg Pitch: 0 deg Roll: 0 deg Speed: 250 m/s \\",
                "  Rcvr->Tgt: Range: 16.0 km (8.6 nm) Brg: 42 deg El: 1 deg \\",
                "  Pd: 0.8 RequiredPd: 0.5 Detected: 1",
            ]
        ),
        encoding="utf-8",
    )
    run = {
        "run_id": "unit_replay",
        "files": [{"name": evt_path.name, "path": str(evt_path), "size": evt_path.stat().st_size}],
        "summary": {"tail": ["start 0", "complete 1.0"]},
    }

    replay = build_run_replay(run, max_events=10, max_frames=10, cache_dir=tmp_path / "cache")

    assert replay["schema_version"] == "afsim-replay.v1"
    assert replay["summary"]["event_count"] >= 2
    assert replay["summary"]["frame_count"] >= 2
    assert replay["events"][0]["type"] == "detected"
    assert replay["events"][0]["detector_id"] == "radar_1"
    assert replay["events"][0]["target_id"] == "target_1"
    assert replay["frames"][0]["authoritative"] is True
    assert any(entity["id"] == "target_1" for entity in replay["frames"][-1]["entities"])
    assert replay["bounds"] is not None


def test_latest_replay_prefers_recent_run_with_frames(tmp_path):
    evt_path = tmp_path / "latest_sample.evt"
    evt_path.write_text(
        "\n".join(
            [
                "0.00000 SENSOR_DETECTION_ATTEMPT radar_1 target_1 Sensor: radar_1 Mode: default Beam: 1 \\",
                "  Rcvr: Type: TEST_RADAR LLA: 30:00:00.00n 120:00:00.00e 10 m Heading: 0 deg Pitch: 0 deg Roll: 0 deg Speed: 0 m/s \\",
                "  Tgt: Type: TEST_TARGET LLA: 30:06:00.00n 120:06:00.00e 1000 m Heading: 45 deg Pitch: 0 deg Roll: 0 deg Speed: 250 m/s \\",
                "  Pd: 0.9 RequiredPd: 0.5 Detected: 1",
            ]
        ),
        encoding="utf-8",
    )
    empty_run = {
        "run_id": f"{tmp_path.name}_latest_no_frames",
        "files": [],
        "summary": {"tail": ["start 0", "complete 1.0"]},
    }
    framed_run = {
        "run_id": f"{tmp_path.name}_older_with_frames",
        "files": [{"name": evt_path.name, "path": str(evt_path), "size": evt_path.stat().st_size}],
        "summary": {"tail": ["start 0", "complete 1.0"]},
    }

    replay = build_latest_replay([empty_run, framed_run])

    assert replay["summary"]["run_id"] == framed_run["run_id"]
    assert replay["summary"]["latest_run_id"] == empty_run["run_id"]
    assert replay["summary"]["selected_run_index"] == 1
    assert replay["summary"]["selection_policy"] == "latest_with_replay_frames"
    assert replay["summary"]["frame_count"] >= 1


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
    replay = replay_for_run(run["run_id"])
    assert replay["summary"]["run_id"] == run["run_id"]
    assert replay["schema_version"] == "afsim-replay.v1"
