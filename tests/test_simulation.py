from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.core.security import User
from app.models import CommanderCommand
from app.models import AFSimDesignedPlatform, AFSimRoutePoint, AFSimScenarioDesign
from app.services.afsim_aer_reader import aer_capabilities, inspect_aer_file
from app.services.afsim_design import generate_scenario, read_generated_scenario
from app.services.afsim_maps import map_resource_manifest, raster_metadata, read_raster_tile, vector_geojson
from app.services.afsim_parser import parse_demo_scenario
from app.services.afsim_parser import parse_scenario_file
from app.services.afsim_realtime import build_realtime_frame
from app.services.afsim_realtime_bridge import RealtimeBridgeManager, realtime_bridge_manager
from app.services.afsim_replay import build_latest_replay, build_run_replay
from app.services.afsim_runner import afsim_paths, discover_demos, run_demo, run_generated_scenario, status
from app.services.afsim_workbench import build_workbench_state, default_layer_catalog
from app.services.afsim_workbench import replay_for_run
from app.services.dis_bridge import DisBridge, parse_entity_state_pdu
from app.services.simulation import SimulationEngine
from app.services.storage import Storage
from app.services.xio_bridge import XioBridge


def _api_client_with_default_tokens(monkeypatch):
    for name in ("AFSIM_COMMANDER_TOKEN", "AFSIM_OPERATOR_TOKEN", "AFSIM_ANALYST_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    from app.main import app

    return TestClient(app)


def test_api_state_without_token_returns_401(monkeypatch):
    client = _api_client_with_default_tokens(monkeypatch)

    response = client.get("/api/state")

    assert response.status_code == 401


def test_api_state_with_invalid_token_returns_401(monkeypatch):
    client = _api_client_with_default_tokens(monkeypatch)

    response = client.get("/api/state", headers={"X-AFSIM-Token": "not-a-real-token"})

    assert response.status_code == 401


def test_api_state_with_analyst_token_returns_200(monkeypatch):
    client = _api_client_with_default_tokens(monkeypatch)

    response = client.get("/api/state", headers={"X-AFSIM-Token": "analyst-token"})

    assert response.status_code == 200
    assert response.json()["scenario_id"] == "demo_air_space_radar"


def test_api_tokens_prefer_environment_values(monkeypatch):
    client = _api_client_with_default_tokens(monkeypatch)
    monkeypatch.setenv("AFSIM_ANALYST_TOKEN", "custom-analyst-token")

    accepted = client.get("/api/state", headers={"X-AFSIM-Token": "custom-analyst-token"})
    rejected = client.get("/api/state", headers={"X-AFSIM-Token": "analyst-token"})

    assert accepted.status_code == 200
    assert rejected.status_code == 401


def test_api_control_with_analyst_token_returns_403(monkeypatch):
    client = _api_client_with_default_tokens(monkeypatch)

    response = client.post(
        "/api/control",
        headers={"X-AFSIM-Token": "analyst-token"},
        json={"action": "pause"},
    )

    assert response.status_code == 403


def test_api_control_with_operator_token_returns_200(monkeypatch):
    client = _api_client_with_default_tokens(monkeypatch)

    response = client.post(
        "/api/control",
        headers={"X-AFSIM-Token": "operator-token"},
        json={"action": "pause"},
    )

    assert response.status_code == 200
    assert response.json()["scenario_id"] == "demo_air_space_radar"


def test_api_control_with_commander_token_keeps_full_permissions(monkeypatch):
    client = _api_client_with_default_tokens(monkeypatch)

    response = client.post(
        "/api/control",
        headers={"X-AFSIM-Token": "commander-token"},
        json={"action": "pause"},
    )

    assert response.status_code == 200
    assert response.json()["scenario_id"] == "demo_air_space_radar"


def _assert_websocket_rejected(client: TestClient, path: str) -> None:
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect(path):
            pass
    assert exc.value.code == 1008


def _static_job_id() -> str:
    from app.services.afsim_jobs import afsim_job_manager

    return afsim_job_manager._new_job({"kind": "unit-test"})  # noqa: SLF001 - avoids launching mission.exe


def test_websocket_without_token_is_rejected(monkeypatch):
    client = _api_client_with_default_tokens(monkeypatch)

    _assert_websocket_rejected(client, "/ws/state")


def test_websocket_with_invalid_token_is_rejected(monkeypatch):
    client = _api_client_with_default_tokens(monkeypatch)

    _assert_websocket_rejected(client, "/ws/state?token=wrong-token")


def test_analyst_token_can_connect_read_state_websocket(monkeypatch):
    client = _api_client_with_default_tokens(monkeypatch)

    with client.websocket_connect("/ws/state?token=analyst-token") as websocket:
        payload = websocket.receive_json()

    assert payload["scenario_id"] == "demo_air_space_radar"


def test_analyst_token_can_connect_preview_websocket(monkeypatch):
    client = _api_client_with_default_tokens(monkeypatch)

    with client.websocket_connect(
        "/ws/afsim/preview?token=analyst-token&demo_name=simple_scenario&input_file=simple_scenario.txt"
    ) as websocket:
        payload = websocket.receive_json()

    assert payload["source"].startswith("demo:simple_scenario")
    assert payload["authoritative"] is False


def test_afsim_realtime_endpoint_reports_unavailable_for_missing_run(monkeypatch):
    client = _api_client_with_default_tokens(monkeypatch)

    with client.websocket_connect("/ws/afsim/realtime/missing_run_id?token=analyst-token") as websocket:
        payload = websocket.receive_json()

    assert payload["type"] == "afsim_realtime"
    assert payload["status"] in {"unavailable", "empty"}
    assert payload["run_id"] == "missing_run_id"
    assert payload["authoritative"] is False
    assert payload["entity_count"] == 0


def test_frontend_realtime_paths_are_split():
    text = Path("app/static/js/realtime.js").read_text(encoding="utf-8")

    assert "/ws/afsim/preview" in text
    assert "/ws/afsim/realtime/${encodeURIComponent(runId)}" in text
    assert "/ws/afsim/realtime?" not in text


def test_frontend_map_engine_adapters_are_optional():
    adapter_dir = Path("app/static/js/map_adapters")
    index = Path("app/static/index.html").read_text(encoding="utf-8")
    app_js = Path("app/static/js/app.js").read_text(encoding="utf-8")
    canvas = (adapter_dir / "canvas_adapter.js").read_text(encoding="utf-8")
    maplibre = (adapter_dir / "maplibre_adapter.js").read_text(encoding="utf-8")
    cesium = (adapter_dir / "cesium_adapter.js").read_text(encoding="utf-8")
    docs = Path("docs/MAP_ENGINE_ADAPTERS.md").read_text(encoding="utf-8")

    assert "map_renderer.js" in canvas
    assert "mapEngineSelect" in index
    assert "Canvas Tactical" in index
    assert "MapLibre 2D" in index
    assert "Cesium 3D" in index
    assert "afsim-map-frame.v1" in app_js
    assert "globalThis.maplibregl" in maplibre
    assert "globalThis.Cesium" in cesium
    assert "unavailable" in maplibre
    assert "unavailable" in cesium
    assert "app/static/vendor/maplibre" in docs
    assert "app/static/vendor/cesium" in docs
    assert "CDN" in docs


def test_analyst_token_can_connect_jobs_websocket(monkeypatch):
    client = _api_client_with_default_tokens(monkeypatch)
    job_id = _static_job_id()

    with client.websocket_connect(f"/ws/afsim/jobs/{job_id}?token=analyst-token") as websocket:
        payload = websocket.receive_json()

    assert payload["type"] == "job"
    assert payload["job"]["job_id"] == job_id


def test_websocket_jobs_requires_read_report(monkeypatch):
    client = _api_client_with_default_tokens(monkeypatch)
    from app.core import security

    monkeypatch.setitem(
        security.ROLE_USERS,
        "operator",
        User("operator", "operator", ("read:state", "control:sim", "edit:scenario", "ask:llm")),
    )
    job_id = _static_job_id()

    _assert_websocket_rejected(client, f"/ws/afsim/jobs/{job_id}?token=operator-token")


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


class _FakeMissionProcess:
    def __init__(self, command, **kwargs):
        self.command = command
        self.returncode = 0

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        return self.returncode


def _run_input_with_fake_mission(monkeypatch, tmp_path, mode_marker):
    from app.services import afsim_runner as runner_module

    mission = tmp_path / "afsim" / "bin" / "mission.exe"
    mission.parent.mkdir(parents=True)
    mission.write_text("fake", encoding="utf-8")
    input_path = tmp_path / "work" / "input.txt"
    input_path.parent.mkdir(parents=True)
    input_path.write_text("end_time 1 sec", encoding="utf-8")
    monkeypatch.setattr(
        runner_module,
        "afsim_paths",
        lambda: runner_module.AFSimPaths(
            root=tmp_path / "afsim",
            bin_dir=mission.parent,
            demos_dir=tmp_path / "afsim" / "demos",
            mission_exe=mission,
        ),
    )
    monkeypatch.setattr(runner_module, "RUN_ROOT", tmp_path / "runs")
    monkeypatch.setattr(runner_module.subprocess, "Popen", _FakeMissionProcess)
    kwargs = {} if mode_marker is None else {"mode": mode_marker}

    return runner_module._run_input(  # noqa: SLF001 - command construction is the behavior under test
        input_path=input_path,
        working_dir=input_path.parent,
        run_id=f"unit_{mode_marker or 'default'}",
        source="unit",
        timeout_seconds=5,
        **kwargs,
    )


def test_afsim_runner_default_mode_uses_es(monkeypatch, tmp_path):
    result = _run_input_with_fake_mission(monkeypatch, tmp_path, None)

    assert "-es" in result["command"]
    assert result["mode"] == "es"
    assert result["realtime"] is False


@pytest.mark.parametrize("mode", ["es", "fs", "rt"])
def test_afsim_runner_command_contains_selected_mode(monkeypatch, tmp_path, mode):
    result = _run_input_with_fake_mission(monkeypatch, tmp_path, mode)

    assert f"-{mode}" in result["command"]
    assert result["mode"] == mode
    assert result["realtime"] is (mode == "rt")


def test_afsim_runner_marks_external_cancel_and_preserves_partial_outputs(monkeypatch, tmp_path):
    import threading

    from app.services import afsim_runner as runner_module

    mission = tmp_path / "afsim" / "bin" / "mission.exe"
    mission.parent.mkdir(parents=True)
    mission.write_text("fake", encoding="utf-8")
    input_path = tmp_path / "work" / "input.txt"
    input_path.parent.mkdir(parents=True)
    input_path.write_text("end_time 60 sec", encoding="utf-8")
    cancel_event = threading.Event()

    class ExternallyCanceledMissionProcess:
        def __init__(self, command, cwd=None, stdout=None, stderr=None, **kwargs):
            self.command = command
            self.returncode = None
            output_dir = Path(str(cwd)) / "output"
            output_dir.mkdir(exist_ok=True)
            (output_dir / "partial.evt").write_text("0.0 PARTIAL_EVENT\n", encoding="utf-8")
            if stdout:
                stdout.write("mission started\n")
                stdout.flush()
            if stderr:
                stderr.write("mission warning before cancel\n")
                stderr.flush()

        def poll(self):
            return self.returncode

        def terminate(self):
            self.returncode = -15

        def wait(self, timeout=None):
            if self.returncode is None:
                self.returncode = 0
            return self.returncode

        def kill(self):
            self.returncode = -9

    def cancel_from_manager(process):
        cancel_event.set()
        process.terminate()

    monkeypatch.setattr(
        runner_module,
        "afsim_paths",
        lambda: runner_module.AFSimPaths(
            root=tmp_path / "afsim",
            bin_dir=mission.parent,
            demos_dir=tmp_path / "afsim" / "demos",
            mission_exe=mission,
        ),
    )
    monkeypatch.setattr(runner_module, "RUN_ROOT", tmp_path / "runs")
    monkeypatch.setattr(runner_module.subprocess, "Popen", ExternallyCanceledMissionProcess)

    result = runner_module._run_input(  # noqa: SLF001 - cancellation edge is runner behavior
        input_path=input_path,
        working_dir=input_path.parent,
        run_id="unit_external_cancel",
        source="unit",
        timeout_seconds=30,
        process_callback=cancel_from_manager,
        cancel_event=cancel_event,
    )

    assert result["canceled"] is True
    assert result["returncode"] == -15
    assert "CANCELED" in result["stderr"]
    assert (input_path.parent / "output" / "partial.evt").exists()
    assert (Path(str(result["run_dir"])) / "partial.evt").exists()
    assert any(file["name"] == "partial.evt" for file in result["files"])
    assert any(file["name"] == "mission.stdout.log" for file in result["files"])


def test_afsim_map_resources_are_served_from_local_assets():
    manifest = map_resource_manifest()
    assert manifest["tile_scheme"] == "afsim_plate_carree"
    assert manifest["readonly"] is True
    assert manifest["tile_matrix"]["url_y_origin"] == "north"
    assert any(layer["id"] == "bluemarble" and layer["exists"] for layer in manifest["raster_layers"])
    assert any(layer["id"] == "naturalearth" for layer in manifest["raster_layers"])
    assert any(layer["id"] == "coastline" and layer["exists"] for layer in manifest["vector_layers"])
    assert any(item["id"] == "bluemarble" and item["url"] for item in manifest["offline_maps"])

    meta = raster_metadata("bluemarble")
    assert meta["profile"] == "afsim_plate_carree"
    assert meta["tile_url_template"] == "/api/afsim/maps/bluemarble/{z}/{x}/{y}.png"
    assert meta["max_zoom"] >= 1
    body, media_type, _ = read_raster_tile("bluemarble", 1, 0, 0)
    assert media_type == "image/jpeg"
    assert body.startswith(b"\xff\xd8")


def test_afsim_vector_layers_convert_to_geojson():
    coastline = vector_geojson("coastline", simplify=0.1, max_features=3)
    political = vector_geojson("pol", simplify=1.0, max_features=3)
    us = vector_geojson("us", simplify=0.01, max_features=3)
    for payload in (coastline, political, us):
        assert payload["type"] == "FeatureCollection"
        assert payload["features"]
        assert payload["features"][0]["geometry"]["type"] == "MultiLineString"


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


def test_realtime_bridge_reads_csv_incrementally(tmp_path):
    manager = RealtimeBridgeManager()
    work_dir = tmp_path / "work"
    output_dir = work_dir / "output"
    output_dir.mkdir(parents=True)
    csv_path = output_dir / "event_output.csv"
    csv_path.write_text(
        "time,platform,lat,lon,alt_m,event,side\n"
        "0.5,Blue1,30.0,120.0,1000,track,blue\n",
        encoding="utf-8",
    )
    manager.register_run("unit_rt_csv", working_dir=work_dir)

    first = manager.poll("unit_rt_csv")
    assert first["schema_version"] == "afsim-realtime-frame.v1"
    assert first["source"] == "afsim-csv-event-output"
    assert first["authoritative"] is True
    assert first["sim_time"] == 0.5
    assert first["entity_count"] == 1
    assert first["events"][0]["platform_id"] == "Blue1"

    with csv_path.open("a", encoding="utf-8") as handle:
        handle.write("1.5,Blue1,30.1,120.2,1100,track,blue\n")

    second = manager.poll("unit_rt_csv")
    assert second["frame_id"] == 2
    assert second["sim_time"] == 1.5
    assert second["entity_count"] == 1
    assert second["entities"][0]["lat"] == 30.1
    assert second["tracks"][0]["platform_id"] == "Blue1"
    assert len(second["tracks"][0]["points"]) == 2


def test_realtime_bridge_reads_evt_incrementally(tmp_path):
    manager = RealtimeBridgeManager()
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    evt_path = output_dir / "event_output.evt"
    evt_path.write_text(
        "\n".join(
            [
                "0.00000 SENSOR_DETECTION_ATTEMPT radar_1 target_1 Sensor: radar_1 Mode: default Beam: 1 \\",
                "  Rcvr: Type: TEST_RADAR LLA: 30:00:00.00n 120:00:00.00e 10 m Heading: 0 deg Pitch: 0 deg Roll: 0 deg Speed: 0 m/s \\",
                "  Tgt: Type: TEST_TARGET LLA: 30:06:00.00n 120:06:00.00e 1000 m Heading: 45 deg Pitch: 0 deg Roll: 0 deg Speed: 250 m/s \\",
                "  Rcvr->Tgt: Range: 14.7 km (7.9 nm) Brg: 40 deg El: 1 deg \\",
                "  Pd: 0.9 RequiredPd: 0.5 Detected: 1",
                "",
            ]
        ),
        encoding="utf-8",
    )
    manager.register_run("unit_rt_evt", output_dir=output_dir)

    first = manager.poll("unit_rt_evt")
    assert first["source"] == "afsim-event-output"
    assert first["authoritative"] is True
    assert first["sim_time"] == 0.0
    assert first["detections"]
    assert any(entity["id"] == "target_1" for entity in first["entities"])

    with evt_path.open("a", encoding="utf-8") as handle:
        handle.write(
            "\n".join(
                [
                    "1.00000 SENSOR_DETECTION_ATTEMPT radar_1 target_1 Sensor: radar_1 Mode: default Beam: 1 \\",
                    "  Rcvr: Type: TEST_RADAR LLA: 30:00:00.00n 120:00:00.00e 10 m Heading: 0 deg Pitch: 0 deg Roll: 0 deg Speed: 0 m/s \\",
                    "  Tgt: Type: TEST_TARGET LLA: 30:07:00.00n 120:08:00.00e 1000 m Heading: 45 deg Pitch: 0 deg Roll: 0 deg Speed: 250 m/s \\",
                    "  Rcvr->Tgt: Range: 16.0 km (8.6 nm) Brg: 42 deg El: 1 deg \\",
                    "  Pd: 0.8 RequiredPd: 0.5 Detected: 1",
                    "",
                ]
            )
        )

    second = manager.poll("unit_rt_evt")
    target = next(entity for entity in second["entities"] if entity["id"] == "target_1")
    assert second["frame_id"] == 2
    assert second["sim_time"] == 1.0
    assert target["lat"] == 30.116666666666667
    assert len(next(track for track in second["tracks"] if track["platform_id"] == "target_1")["points"]) == 2


def test_realtime_bridge_reports_unavailable_without_output_files(tmp_path):
    manager = RealtimeBridgeManager()
    manager.register_run("unit_rt_empty", output_dir=tmp_path / "empty_output")

    frame = manager.poll("unit_rt_empty")

    assert frame["schema_version"] == "afsim-realtime-frame.v1"
    assert frame["status"] == "unavailable"
    assert frame["authoritative"] is False
    assert frame["events"] == []


def test_afsim_realtime_websocket_consumes_bridge(monkeypatch, tmp_path):
    client = _api_client_with_default_tokens(monkeypatch)
    output_dir = tmp_path / "ws_output"
    output_dir.mkdir()
    csv_path = output_dir / "event_output.csv"
    csv_path.write_text(
        "time,platform,lat,lon,alt_m,event,side\n"
        "2.0,BlueWs,31.0,121.0,1200,track,blue\n",
        encoding="utf-8",
    )
    run_id = "unit_rt_ws"
    realtime_bridge_manager.register_run(run_id, output_dir=output_dir)

    with client.websocket_connect(f"/ws/afsim/realtime/{run_id}?token=analyst-token&interval_seconds=0.1") as websocket:
        payload = websocket.receive_json()

    assert payload["schema_version"] == "afsim-realtime-frame.v1"
    assert payload["status"] == "ok"
    assert payload["authoritative"] is True
    assert payload["entities"][0]["id"] == "BlueWs"


def _minimal_entity_state_pdu() -> bytes:
    header = bytes([6, 1, 1, 1])
    timestamp = (123456).to_bytes(4, byteorder="big", signed=False)
    length = (12).to_bytes(2, byteorder="big", signed=False)
    padding = b"\x00\x00"
    return header + timestamp + length + padding


def test_dis_bridge_can_start_and_stop(tmp_path):
    bridge = DisBridge(packet_root=tmp_path / "dis_packets")

    started = bridge.start("unit_dis_start_stop", "127.0.0.1", 0)
    stopped = bridge.stop("unit_dis_start_stop")

    assert started["status"] == "running"
    assert started["active"] is True
    assert started["bind_port"] > 0
    assert Path(started["packet_dir"]).exists()
    assert stopped["status"] == "stopped"
    assert stopped["active"] is False


def test_dis_bridge_records_udp_packet_and_reports_unsupported(tmp_path):
    import socket
    import time

    bridge = DisBridge(packet_root=tmp_path / "dis_packets")
    run_id = "unit_dis_packet"
    packet = _minimal_entity_state_pdu()
    parsed = parse_entity_state_pdu(packet)

    assert parsed is not None
    assert parsed["status"] == "unsupported"
    assert parsed["header"]["pdu_type"] == 1

    started = bridge.start(run_id, "127.0.0.1", 0)
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
            sender.sendto(packet, ("127.0.0.1", int(started["bind_port"])))
        frame = None
        for _ in range(80):
            frames = bridge.frames(run_id)
            if frames:
                frame = frames[-1]
                break
            time.sleep(0.05)
    finally:
        bridge.stop(run_id)

    assert frame is not None
    assert frame["schema_version"] == "afsim-dis-frame.v1"
    assert frame["status"] == "unsupported"
    assert frame["source"] == "dis-udp"
    assert frame["authoritative"] is False
    assert frame["header"]["pdu_type"] == 1
    packet_path = Path(frame["packet_path"])
    assert packet_path.exists()
    assert packet_path.read_bytes() == packet
    assert packet_path.with_suffix(".json").exists()


def test_xio_bridge_returns_clear_unsupported_status(tmp_path):
    bridge = XioBridge(session_root=tmp_path / "xio_sessions")

    started = bridge.start("unit_xio", "127.0.0.1", 55000)
    frames = bridge.frames("unit_xio")
    status = bridge.status()
    stopped = bridge.stop("unit_xio")

    assert started["status"] == "unsupported"
    assert started["active"] is False
    assert Path(started["session_dir"]).exists()
    assert frames[0]["schema_version"] == "afsim-xio-frame.v1"
    assert frames[0]["status"] == "unsupported"
    assert frames[0]["authoritative"] is False
    assert frames[0]["entities"] == []
    assert status["status"] == "unsupported"
    assert status["available"] is False
    assert status["authoritative"] is False
    assert stopped["status"] == "stopped"


def test_afsim_bridge_capabilities_api_reports_supported_and_unsupported(monkeypatch):
    client = _api_client_with_default_tokens(monkeypatch)

    response = client.get("/api/afsim/bridges", headers={"X-AFSIM-Token": "analyst-token"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["event_output"]["status"] == "available"
    assert payload["csv_event_output"]["status"] == "available"
    assert payload["dis"]["status"] == "unsupported"
    assert payload["dis"]["capture_available"] is True
    assert payload["xio"]["status"] == "unsupported"
    assert payload["xio"]["authoritative"] is False
    assert payload["xio"]["source"] == "afsim-xio-interface"


def test_xio_bridge_documentation_describes_integration_routes():
    doc = Path("docs/XIO_BRIDGE.md")
    text = doc.read_text(encoding="utf-8")

    assert doc.exists()
    assert "Current Status" in text
    assert "Data Format To Confirm" in text
    assert "Python direct parser" in text
    assert "AFSim C++ plugin to JSON" in text
    assert "authoritative=true" in text


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
        "map_resources",
        "replay",
        "capabilities",
    }
    assert required.issubset(workbench)
    assert workbench["schema_version"] == "afsim-workbench.v1"
    assert workbench["platforms"]
    assert len(workbench["layers"]) >= 30
    assert workbench["map_resources"]["tile_scheme"] == "afsim_plate_carree"
    assert {"events", "frames", "tracks", "bounds", "summary"}.issubset(workbench["replay"])
    assert workbench["replay"]["summary"].get("lightweight") is True
    assert workbench["replay"]["frames"] == []
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
    assert {"run", "events", "frames", "tracks", "bounds", "source_files", "summary"}.issubset(replay)
    assert replay["summary"]["event_count"] >= 2
    assert replay["summary"]["frame_count"] >= 2
    assert replay["events"][0]["type"] == "detected"
    assert replay["events"][0]["detector_id"] == "radar_1"
    assert replay["events"][0]["target_id"] == "target_1"
    assert replay["frames"][0]["authoritative"] is True
    assert {"frame_id", "source", "sim_time", "entity_count", "entities", "events"}.issubset(replay["frames"][0])
    assert any(entity["id"] == "target_1" for entity in replay["frames"][-1]["entities"])
    assert replay["tracks"]
    assert {"platform_id", "points", "history"}.issubset(replay["tracks"][0])
    assert replay["bounds"] is not None


def test_lightweight_replay_cache_does_not_replace_full_replay(tmp_path):
    evt_path = tmp_path / "cache_sample.evt"
    evt_path.write_text(
        "\n".join(
            [
                "0.00000 SENSOR_DETECTION_ATTEMPT radar_1 target_1 Sensor: radar_1 Mode: default Beam: 1 \\",
                "  Rcvr: Type: TEST_RADAR LLA: 30:00:00.00n 120:00:00.00e 10 m Heading: 0 deg Pitch: 0 deg Roll: 0 deg Speed: 0 m/s \\",
                "  Tgt: Type: TEST_TARGET LLA: 30:06:00.00n 120:06:00.00e 1000 m Heading: 45 deg Pitch: 0 deg Roll: 0 deg Speed: 250 m/s \\",
                "  Pd: 0.9 RequiredPd: 0.5 Detected: 1",
                "1.00000 SENSOR_DETECTION_ATTEMPT radar_1 target_1 Sensor: radar_1 Mode: default Beam: 1 \\",
                "  Rcvr: Type: TEST_RADAR LLA: 30:00:00.00n 120:00:00.00e 10 m Heading: 0 deg Pitch: 0 deg Roll: 0 deg Speed: 0 m/s \\",
                "  Tgt: Type: TEST_TARGET LLA: 30:07:00.00n 120:08:00.00e 1000 m Heading: 45 deg Pitch: 0 deg Roll: 0 deg Speed: 250 m/s \\",
                "  Pd: 0.8 RequiredPd: 0.5 Detected: 1",
            ]
        ),
        encoding="utf-8",
    )
    run = {
        "run_id": "unit_replay_cache",
        "files": [{"name": evt_path.name, "path": str(evt_path), "size": evt_path.stat().st_size}],
        "summary": {"tail": ["start 0", "complete 1.0"]},
    }
    cache_dir = tmp_path / "cache"

    light = build_run_replay(run, max_events=1, max_frames=0, cache_dir=cache_dir)
    full = build_run_replay(run, max_events=10, max_frames=10, cache_dir=cache_dir)

    assert light["frames"] == []
    assert light["tracks"] == []
    assert light["summary"]["lightweight"] is True
    assert full["frames"]
    assert full["tracks"]
    assert full["summary"]["lightweight"] is False
    assert len(list(cache_dir.glob("unit_replay_cache_*.json"))) == 2


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


def test_generated_scenario_ids_include_uuid_and_do_not_collide():
    design = AFSimScenarioDesign(
        name="pytest_collision_design",
        end_time_seconds=10,
        platforms=[
            AFSimDesignedPlatform(
                name="Blue_Unique",
                side="blue",
                category="fighter",
                icon="F-22",
                lat=1.0,
                lon=1.0,
                route=[AFSimRoutePoint(lat=1.0, lon=1.1)],
            )
        ],
    )
    first = generate_scenario(design)
    second = generate_scenario(design)

    assert first["scenario_id"] != second["scenario_id"]
    assert first["scenario_id"].endswith(first["scenario_id"].split("_")[-1])
    assert len(first["scenario_id"].split("_")[-1]) == 8
    assert Path(first["scenario_dir"]).exists()
    assert Path(second["scenario_dir"]).exists()


def test_aer_reader_reports_extension_capability(tmp_path):
    aer_path = tmp_path / "sample.aer"
    aer_path.write_bytes(b"AER")

    capabilities = aer_capabilities()
    metadata = inspect_aer_file(aer_path)

    assert capabilities["schema_version"] == "afsim-aer-reader.v1"
    assert capabilities["status"] in {"available", "unsupported"}
    assert "pymystic" in capabilities
    assert metadata["status"] == "unsupported"
    assert metadata["reader_status"] in {"available", "unsupported"}
    assert metadata["parsed_records"] == 0


def test_runtime_cleanup_script_dry_run_lists_candidates_without_deleting(tmp_path):
    import os
    import shutil
    import subprocess
    import time

    script = Path("scripts/cleanup_runtime.ps1")
    text = script.read_text(encoding="utf-8")
    shell = shutil.which("powershell") or shutil.which("pwsh")

    assert script.exists()
    assert "KeepRuns = 30" in text
    assert "KeepDays = 7" in text
    assert "[switch]$Apply" in text
    assert "Assert-InCleanupRoots" in text
    assert "Test-ManualKeep" in text
    assert "generated_scenarios" in text
    if not shell:
        pytest.skip("PowerShell is required to execute cleanup_runtime.ps1")

    project = tmp_path / "project"
    old_run = project / "runtime" / "afsim_runs" / "old_run"
    recent_run = project / "runtime" / "afsim_runs" / "recent_run"
    kept_run = project / "runtime" / "afsim_runs" / "kept_run"
    old_workdir = project / "runtime" / "afsim_workdirs" / "old_run"
    replay_cache = project / "runtime" / "workbench" / "replay_cache"
    old_generated = project / "generated_scenarios" / "old_pytest_generated_design"
    user_generated = project / "generated_scenarios" / "old_user_design"
    for directory in [old_run, recent_run, kept_run, old_workdir, replay_cache, old_generated, user_generated]:
        directory.mkdir(parents=True, exist_ok=True)
    (kept_run / ".keep").write_text("keep", encoding="utf-8")
    old_cache_file = replay_cache / "old_run_e1_f1.json"
    old_cache_file.write_text("{}", encoding="utf-8")
    old_time = time.time() - 20 * 86400
    for path in [old_run, kept_run, old_workdir, replay_cache, old_generated, user_generated, old_cache_file]:
        os.utime(path, (old_time, old_time))

    result = subprocess.run(
        [
            shell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script.resolve()),
            "-ProjectRoot",
            str(project),
            "-KeepRuns",
            "1",
            "-KeepDays",
            "7",
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert "mode=dry-run" in output
    assert "[dry-run] afsim_run" in output
    assert "[dry-run] afsim_workdir" in output
    assert "[dry-run] replay_cache" in output
    assert "[dry-run] generated_scenario" in output
    assert "old_pytest_generated_design" in output
    assert "old_user_design" not in output
    assert old_run.exists()
    assert old_workdir.exists()
    assert old_cache_file.exists()
    assert old_generated.exists()
    assert kept_run.exists()


def test_demo_run_uses_runtime_workdir_and_preserves_original_demo():
    run = run_demo("simple_scenario", "simple_scenario.txt", 30)
    afsim_root = afsim_paths().root.resolve()
    working_dir = Path(str(run["working_dir"])).resolve()
    run_dir = Path(str(run["run_dir"])).resolve()

    assert run["returncode"] == 0
    assert afsim_root not in working_dir.parents
    assert working_dir != afsim_root
    assert "runtime" in working_dir.parts
    assert run_dir.exists()
    assert any(file["name"].endswith((".evt", ".aer", ".log")) for file in run["files"])


def test_afsim_job_manager_runs_generated_scenario_and_binds_replay():
    from app.services.afsim_jobs import AFSimRunJobManager

    design = AFSimScenarioDesign(
        name="pytest_job_design",
        end_time_seconds=20,
        platforms=[
            AFSimDesignedPlatform(
                name="Blue_Job",
                side="blue",
                category="fighter",
                icon="F-22",
                lat=1.05,
                lon=1.05,
                altitude_m=9000,
                speed_kts=320,
                heading_deg=90,
                route=[AFSimRoutePoint(lat=1.05, lon=1.1, altitude_m=9000, speed_kts=320)],
            ),
            AFSimDesignedPlatform(
                name="Red_Job",
                side="red",
                category="fighter",
                icon="SU-27",
                lat=1.2,
                lon=1.3,
                altitude_m=8500,
                speed_kts=330,
                heading_deg=270,
                route=[AFSimRoutePoint(lat=1.2, lon=1.25, altitude_m=8500, speed_kts=330)],
            ),
        ],
    )
    generated = generate_scenario(design)
    manager = AFSimRunJobManager()
    job = manager.submit_generated(generated["scenario_id"], 30)
    for _ in range(120):
        job = manager.get(job["job_id"])
        if job["status"] in {"finished", "failed"}:
            break
        import time

        time.sleep(0.25)

    assert job["status"] == "finished"
    assert job["run"]["returncode"] == 0
    assert job["replay_summary"]["run_id"] == job["run"]["run_id"]
    replay = replay_for_run(job["run"]["run_id"])
    assert replay["schema_version"] == "afsim-replay.v1"
    assert {"run", "events", "frames", "tracks", "bounds", "source_files", "summary"}.issubset(replay)
    assert replay["summary"]["run_id"] == job["run"]["run_id"]
    assert isinstance(replay["frames"], list)
    assert isinstance(replay["events"], list)
    assert isinstance(replay["tracks"], list)
    events, _ = manager.events_since(job["job_id"], 0)
    assert any(event["phase"] == "running" for event in events)
    assert any(event["phase"] == "finished" for event in events)


def test_afsim_job_manager_can_cancel_running_job(monkeypatch, tmp_path):
    from app.services import afsim_jobs as jobs_module
    from app.services.afsim_jobs import AFSimRunJobManager

    process_holder = {}
    working_dir = tmp_path / "fake_work"
    output_dir = working_dir / "output"
    run_dir = tmp_path / "fake_run"
    output_file = output_dir / "partial.evt"

    class FakeProcess:
        def __init__(self):
            self.returncode = None
            self.terminated = False
            self.killed = False

        def poll(self):
            return self.returncode

        def terminate(self):
            self.terminated = True
            self.returncode = -15

        def wait(self, timeout=None):
            return self.returncode

        def kill(self):
            self.killed = True
            self.returncode = -9

    def fake_run_demo(
        demo_name,
        input_file,
        timeout_seconds,
        mode="es",
        *,
        progress_callback=None,
        process_callback=None,
        cancel_event=None,
    ):
        output_dir.mkdir(parents=True, exist_ok=True)
        run_dir.mkdir(parents=True, exist_ok=True)
        output_file.write_text("0.0 PARTIAL_EVENT\n", encoding="utf-8")
        process = FakeProcess()
        process_holder["process"] = process
        if process_callback:
            process_callback(process)
        if progress_callback:
            progress_callback(
                {
                    "phase": "running",
                    "tail": ["fake mission running"],
                    "files": [{"name": output_file.name, "path": str(output_file), "size": output_file.stat().st_size}],
                    "working_dir": str(working_dir),
                    "run_dir": str(run_dir),
                    "output_dir": str(output_dir),
                }
            )
        for _ in range(100):
            if cancel_event and cancel_event.is_set():
                break
            import time

            time.sleep(0.01)
        return {
            "run_id": "unit_canceled_run",
            "source": "demo",
            "input_file": input_file or "fake.txt",
            "mode": mode,
            "realtime": mode == "rt",
            "command": ["mission.exe", f"-{mode}", "fake.txt"],
            "working_dir": str(working_dir),
            "run_dir": str(run_dir),
            "returncode": process.returncode,
            "duration_seconds": 0.1,
            "stdout": "partial stdout",
            "stderr": "partial stderr",
            "files": [{"name": output_file.name, "path": str(output_file), "size": output_file.stat().st_size}],
            "summary": {"tail": ["fake mission canceled"], "fatal": [], "warnings": []},
            "timed_out": False,
            "canceled": True,
        }

    monkeypatch.setattr(jobs_module, "run_demo", fake_run_demo)
    manager = AFSimRunJobManager()
    job = manager.submit_demo("fake_demo", "fake.txt", 30)
    for _ in range(100):
        job = manager.get(job["job_id"])
        if job["status"] == "running":
            break
        import time

        time.sleep(0.01)

    canceled = manager.cancel(job["job_id"])
    for _ in range(100):
        canceled = manager.get(job["job_id"])
        if canceled["status"] == "canceled":
            break
        import time

        time.sleep(0.01)

    assert canceled["status"] == "canceled"
    assert canceled["run"]["canceled"] is True
    assert canceled["run"]["stdout"] == "partial stdout"
    assert canceled["run"]["stderr"] == "partial stderr"
    assert process_holder["process"].terminated is True
    assert output_dir.exists()
    assert output_file.exists()
    events, _ = manager.events_since(job["job_id"], 0)
    assert any(event["phase"] == "canceled" for event in events)


def test_afsim_jobs_websocket_streams_canceled_event(monkeypatch):
    client = _api_client_with_default_tokens(monkeypatch)
    from app.services.afsim_jobs import afsim_job_manager

    job_id = afsim_job_manager._new_job({"kind": "unit-test-cancel-ws"})  # noqa: SLF001 - avoids launching mission.exe
    afsim_job_manager.cancel(job_id)

    phases = []
    with client.websocket_connect(f"/ws/afsim/jobs/{job_id}?token=analyst-token") as websocket:
        initial = websocket.receive_json()
        assert initial["type"] == "job"
        for _ in range(4):
            payload = websocket.receive_json()
            if payload.get("type") == "progress":
                phases.append(payload["event"]["phase"])
            if payload.get("type") == "job" and payload["job"]["status"] == "canceled":
                break

    assert "canceled" in phases
