from __future__ import annotations

import argparse
import asyncio
import json
import mimetypes
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.models import (
    AFSimAgentTickRequest,
    AFSimGeneratedRunRequest,
    AFSimRunRequest,
    AFSimScenarioDesign,
    CommanderCommand,
    CommanderRequest,
)
from app.services.afsim_adapter import afsim_available, export_scenario_draft
from app.services.afsim_design import (
    delete_generated_scenario,
    generate_scenario,
    list_generated_scenarios,
    platform_templates,
    read_generated_scenario,
    scene_overview,
)
from app.services.afsim_parser import parse_demo_scenario
from app.services.afsim_parser import parse_scenario_file
from app.services.afsim_runner import (
    discover_demos,
    launch_generated_warlock,
    launch_mystic,
    launch_warlock,
    list_runs,
    run_demo,
    run_generated_scenario,
    status as afsim_runner_status,
)
from app.services.llm import CommanderLLM
from app.services.native_display import capture_window_jpeg, native_display_status
from app.services.reports import build_report, write_markdown_report
from app.services.simulation import DEFAULT_SCENARIO_PATH, SimulationEngine
from app.services.storage import Storage


STATIC_ROOT = PROJECT_ROOT / "app" / "static"
storage = Storage(PROJECT_ROOT / "afsim_llm_preview.sqlite3")
engine = SimulationEngine(storage)
commander = CommanderLLM()


def _json_response(handler: BaseHTTPRequestHandler, data: object, status: int = 200) -> None:
    body = json.dumps(data, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _read_json(handler: BaseHTTPRequestHandler) -> dict[str, object]:
    length = int(handler.headers.get("Content-Length", "0"))
    if length <= 0:
        return {}
    return json.loads(handler.rfile.read(length).decode("utf-8"))


class PreviewHandler(BaseHTTPRequestHandler):
    server_version = "AFSIM_LLM_Preview/0.1"

    def log_message(self, format: str, *args: object) -> None:
        print(f"{self.address_string()} - {format % args}")

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        try:
            if path == "/":
                self._serve_file(STATIC_ROOT / "index.html")
            elif path.startswith("/static/"):
                self._serve_file(PROJECT_ROOT / "app" / path.lstrip("/"))
            elif path == "/api/health":
                _json_response(self, {"ok": True, "mode": "preview", "afsim": afsim_runner_status()})
            elif path == "/api/scenario":
                _json_response(self, engine.scenario.model_dump())
            elif path == "/api/state":
                _json_response(self, engine.tick_from_wall_clock().model_dump())
            elif path == "/api/events":
                _json_response(self, storage.list_events())
            elif path == "/api/afsim/status":
                _json_response(self, afsim_runner_status())
            elif path == "/api/afsim/demos":
                _json_response(self, discover_demos())
            elif path == "/api/afsim/runs":
                _json_response(self, list_runs())
            elif path == "/api/afsim/designs":
                _json_response(self, list_generated_scenarios())
            elif path == "/api/afsim/platform-templates":
                _json_response(self, platform_templates())
            elif path.startswith("/api/afsim/designs/"):
                scenario_id = path.removeprefix("/api/afsim/designs/").strip("/")
                if scenario_id.endswith("/scene"):
                    scenario_id = scenario_id.removesuffix("/scene").strip("/")
                    result = read_generated_scenario(scenario_id)
                    parsed_scene = parse_scenario_file(Path(str(result["scenario_path"])))
                    _json_response(self, scene_overview(parsed_scene))
                else:
                    result = read_generated_scenario(scenario_id)
                    result["parsed"] = parse_scenario_file(Path(str(result["scenario_path"])))
                    _json_response(self, result)
            elif path == "/api/afsim/native-display":
                _json_response(self, native_display_status(""))
            elif path == "/api/afsim/native-frame.jpg":
                query = parse_qs(parsed.query)
                title = query.get("title", ["Warlock"])[0]
                self._binary_response(capture_window_jpeg(title), "image/jpeg")
            elif path == "/api/afsim/scenario":
                query = parse_qs(parsed.query)
                _json_response(
                    self,
                    parse_demo_scenario(
                        query.get("demo_name", ["simple_scenario"])[0],
                        query.get("input_file", [None])[0],
                    ),
                )
            else:
                _json_response(self, {"detail": "not found"}, 404)
        except Exception as exc:
            _json_response(self, {"detail": str(exc)}, 500)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        try:
            payload = _read_json(self)
            if path == "/api/control":
                _json_response(
                    self,
                    engine.control(
                        str(payload.get("action", "step")),
                        float(payload["step_seconds"]) if payload.get("step_seconds") is not None else None,
                    ).model_dump(),
                )
            elif path == "/api/scenario/reset":
                engine.load_scenario(DEFAULT_SCENARIO_PATH)
                _json_response(self, engine.snapshot().model_dump())
            elif path == "/api/commander":
                request = CommanderRequest.model_validate(payload)
                result = asyncio.run(commander.advise(request, engine.snapshot()))
                applied = []
                if request.autonomy == "auto_apply":
                    applied = engine.apply_commands(result.commands)
                storage.add_event("commander.response", result.model_dump())
                _json_response(
                    self,
                    {
                        "advice": result.model_dump(),
                        "applied": applied,
                        "state": engine.snapshot().model_dump(),
                    },
                )
            elif path == "/api/agent/tick":
                request = AFSimAgentTickRequest.model_validate(payload)
                engine.step(request.step_seconds)
                commander_request = CommanderRequest(
                    objective=request.objective,
                    side=request.side,
                    autonomy=request.autonomy,
                )
                result = asyncio.run(commander.advise(commander_request, engine.snapshot()))
                applied = []
                if request.autonomy == "auto_apply":
                    applied = engine.apply_commands(result.commands)
                storage.add_event(
                    "agent.tick",
                    {
                        "objective": request.objective,
                        "side": request.side,
                        "autonomy": request.autonomy,
                        "advice": result.model_dump(),
                        "applied": applied,
                    },
                )
                _json_response(
                    self,
                    {
                        "advice": result.model_dump(),
                        "applied": applied,
                        "state": engine.snapshot().model_dump(),
                    },
                )
            elif path == "/api/commands/apply":
                commands = [CommanderCommand.model_validate(item) for item in payload.get("commands", [])]
                _json_response(
                    self,
                    {"applied": engine.apply_commands(commands), "state": engine.snapshot().model_dump()},
                )
            elif path.startswith("/api/layers/") and path.endswith("/visibility"):
                layer_id = path.removeprefix("/api/layers/").removesuffix("/visibility").strip("/")
                layer = engine.set_layer_visibility(layer_id, bool(payload.get("visible", True)))
                if not layer:
                    _json_response(self, {"detail": "layer not found"}, 404)
                else:
                    _json_response(self, layer.model_dump())
            elif path == "/api/reports":
                report_data = build_report(engine.snapshot())
                report_id = storage.add_report(engine.scenario.id, str(report_data["title"]), report_data)
                report_path = write_markdown_report(report_data, PROJECT_ROOT / "reports")
                _json_response(self, {"id": report_id, "path": str(report_path), "report": report_data})
            elif path == "/api/export/afsim":
                export_path = export_scenario_draft(engine.scenario, PROJECT_ROOT / "exports")
                _json_response(self, {"path": str(export_path), "afsim": afsim_available()})
            elif path == "/api/afsim/run":
                request = AFSimRunRequest.model_validate(payload)
                result = run_demo(request.demo_name, request.input_file, request.timeout_seconds)
                storage.add_event("afsim.run", result)
                _json_response(self, result)
            elif path == "/api/afsim/designs":
                result = generate_scenario(AFSimScenarioDesign.model_validate(payload))
                result["parsed"] = parse_scenario_file(Path(str(result["scenario_path"])))
                storage.add_event("afsim.design.generated", result)
                _json_response(self, result)
            elif path.startswith("/api/afsim/designs/") and path.endswith("/run"):
                scenario_id = path.removeprefix("/api/afsim/designs/").removesuffix("/run").strip("/")
                request = AFSimGeneratedRunRequest.model_validate(payload)
                result = run_generated_scenario(scenario_id, request.timeout_seconds)
                storage.add_event("afsim.generated.run", result)
                _json_response(self, result)
            elif path.startswith("/api/afsim/designs/") and path.endswith("/launch-map"):
                scenario_id = path.removeprefix("/api/afsim/designs/").removesuffix("/launch-map").strip("/")
                result = launch_generated_warlock(scenario_id)
                storage.add_event("afsim.generated.launch_map", result)
                _json_response(self, result)
            elif path == "/api/afsim/analyze":
                runs = list_runs()
                run_id = payload.get("run_id")
                run = next((item for item in runs if item.get("run_id") == run_id), runs[0] if runs else None)
                if not run:
                    _json_response(self, {"detail": "AFSIM run not found"}, 404)
                else:
                    analysis = asyncio.run(commander.analyze_afsim_run(run))
                    storage.add_event("afsim.analysis", {"run_id": run.get("run_id"), "analysis": analysis})
                    _json_response(self, {"run": run, "analysis": analysis})
            elif path == "/api/afsim/launch-3d":
                result = launch_mystic(payload.get("run_id") if isinstance(payload.get("run_id"), str) else None)
                storage.add_event("afsim.launch_3d", result)
                _json_response(self, result)
            elif path == "/api/afsim/launch-map":
                result = launch_warlock(
                    payload.get("demo_name") if isinstance(payload.get("demo_name"), str) else None,
                    payload.get("input_file") if isinstance(payload.get("input_file"), str) else None,
                )
                storage.add_event("afsim.launch_map", result)
                _json_response(self, result)
            else:
                _json_response(self, {"detail": "not found"}, 404)
        except Exception as exc:
            _json_response(self, {"detail": str(exc)}, 500)

    def do_DELETE(self) -> None:
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        try:
            if path.startswith("/api/afsim/designs/"):
                scenario_id = path.removeprefix("/api/afsim/designs/").strip("/")
                result = delete_generated_scenario(scenario_id)
                storage.add_event("afsim.design.deleted", result)
                _json_response(self, result)
            else:
                _json_response(self, {"detail": "not found"}, 404)
        except Exception as exc:
            _json_response(self, {"detail": str(exc)}, 500)

    def _serve_file(self, path: Path) -> None:
        if not path.exists() or not path.is_file():
            _json_response(self, {"detail": "not found"}, 404)
            return
        body = path.read_bytes()
        content_type = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _binary_response(self, body: bytes, content_type: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store, max-age=0")
        self.end_headers()
        self.wfile.write(body)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the dependency-light AFSIM_LLM preview server.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8766)
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), PreviewHandler)
    print(f"AFSIM_LLM preview server: http://{args.host}:{args.port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
