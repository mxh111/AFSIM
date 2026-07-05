from __future__ import annotations

import argparse
import asyncio
import json
import mimetypes
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.models import CommanderCommand, CommanderRequest
from app.services.afsim_adapter import afsim_available, export_scenario_draft
from app.services.llm import CommanderLLM
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
                _json_response(self, {"ok": True, "mode": "preview", "afsim": afsim_available()})
            elif path == "/api/scenario":
                _json_response(self, engine.scenario.model_dump())
            elif path == "/api/state":
                _json_response(self, engine.tick_from_wall_clock().model_dump())
            elif path == "/api/events":
                _json_response(self, storage.list_events())
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
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the dependency-light AFSIM_LLM preview server.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), PreviewHandler)
    print(f"AFSIM_LLM preview server: http://{args.host}:{args.port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
