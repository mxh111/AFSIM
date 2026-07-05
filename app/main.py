from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles

from app.core.config import PROJECT_ROOT, settings
from app.core.security import User, role_catalog, user_from_token
from app.models import AFSimGeneratedRunRequest, AFSimRunRequest, AFSimScenarioDesign, CommanderRequest, SimulationControl
from app.services.afsim_adapter import afsim_available, export_scenario_draft
from app.services.afsim_design import generate_scenario, list_generated_scenarios, read_generated_scenario
from app.services.afsim_parser import parse_demo_scenario, parse_scenario_file
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


storage = Storage()
engine = SimulationEngine(storage)
commander = CommanderLLM()

app = FastAPI(title=settings.app_name, version="0.1.0")
app.mount("/static", StaticFiles(directory=PROJECT_ROOT / "app" / "static"), name="static")


def current_user(x_afsim_token: str | None = Header(default=None)) -> User:
    return user_from_token(x_afsim_token)


@app.get("/", response_class=HTMLResponse)
async def index() -> FileResponse:
    return FileResponse(PROJECT_ROOT / "app" / "static" / "index.html")


@app.get("/api/health")
async def health() -> dict[str, object]:
    return {"ok": True, "app": settings.app_name, "afsim": afsim_runner_status()}


@app.get("/api/auth/roles")
async def roles() -> list[dict[str, object]]:
    return role_catalog()


@app.get("/api/scenario")
async def scenario(user: User = Depends(current_user)) -> dict[str, object]:
    if not user.can("read:state"):
        raise HTTPException(status_code=403, detail="permission denied")
    return engine.scenario.model_dump()


@app.post("/api/scenario/reset")
async def reload_default(user: User = Depends(current_user)) -> dict[str, object]:
    if not user.can("edit:scenario"):
        raise HTTPException(status_code=403, detail="permission denied")
    engine.load_scenario(DEFAULT_SCENARIO_PATH)
    return engine.snapshot().model_dump()


@app.get("/api/state")
async def state(user: User = Depends(current_user)) -> dict[str, object]:
    if not user.can("read:state"):
        raise HTTPException(status_code=403, detail="permission denied")
    return engine.snapshot().model_dump()


@app.post("/api/control")
async def control(payload: SimulationControl, user: User = Depends(current_user)) -> dict[str, object]:
    if not user.can("control:sim"):
        raise HTTPException(status_code=403, detail="permission denied")
    return engine.control(payload.action, payload.step_seconds).model_dump()


@app.post("/api/commander")
async def ask_commander(payload: CommanderRequest, user: User = Depends(current_user)) -> dict[str, object]:
    if not user.can("ask:llm"):
        raise HTTPException(status_code=403, detail="permission denied")
    result = await commander.advise(payload, engine.snapshot())
    applied = []
    if payload.autonomy == "auto_apply":
        applied = engine.apply_commands(result.commands)
    storage.add_event("commander.response", result.model_dump())
    return {"advice": result.model_dump(), "applied": applied, "state": engine.snapshot().model_dump()}


@app.post("/api/commands/apply")
async def apply_commands(payload: dict[str, object], user: User = Depends(current_user)) -> dict[str, object]:
    if not user.can("control:sim"):
        raise HTTPException(status_code=403, detail="permission denied")
    commands = payload.get("commands", [])
    if not isinstance(commands, list):
        raise HTTPException(status_code=400, detail="commands must be a list")
    from app.models import CommanderCommand

    parsed = [CommanderCommand.model_validate(item) for item in commands]
    return {"applied": engine.apply_commands(parsed), "state": engine.snapshot().model_dump()}


@app.post("/api/layers/{layer_id}/visibility")
async def layer_visibility(layer_id: str, payload: dict[str, bool], user: User = Depends(current_user)) -> dict[str, object]:
    if not user.can("edit:scenario"):
        raise HTTPException(status_code=403, detail="permission denied")
    layer = engine.set_layer_visibility(layer_id, bool(payload.get("visible", True)))
    if not layer:
        raise HTTPException(status_code=404, detail="layer not found")
    return layer.model_dump()


@app.post("/api/reports")
async def report(user: User = Depends(current_user)) -> dict[str, object]:
    if not user.can("read:report"):
        raise HTTPException(status_code=403, detail="permission denied")
    report_data = build_report(engine.snapshot())
    report_id = storage.add_report(engine.scenario.id, str(report_data["title"]), report_data)
    report_path = write_markdown_report(report_data, PROJECT_ROOT / "reports")
    return {"id": report_id, "path": str(report_path), "report": report_data}


@app.get("/api/events")
async def events(user: User = Depends(current_user)) -> list[dict[str, object]]:
    if not user.can("read:state"):
        raise HTTPException(status_code=403, detail="permission denied")
    return storage.list_events()


@app.post("/api/export/afsim")
async def export_afsim(user: User = Depends(current_user)) -> dict[str, object]:
    if not user.can("edit:scenario"):
        raise HTTPException(status_code=403, detail="permission denied")
    path = export_scenario_draft(engine.scenario, PROJECT_ROOT / "exports")
    return {"path": str(path), "afsim": afsim_available()}


@app.get("/api/afsim/status")
async def afsim_status(user: User = Depends(current_user)) -> dict[str, object]:
    if not user.can("read:state"):
        raise HTTPException(status_code=403, detail="permission denied")
    return afsim_runner_status()


@app.get("/api/afsim/demos")
async def afsim_demos(user: User = Depends(current_user)) -> list[dict[str, object]]:
    if not user.can("read:state"):
        raise HTTPException(status_code=403, detail="permission denied")
    return discover_demos()


@app.get("/api/afsim/runs")
async def afsim_runs(user: User = Depends(current_user)) -> list[dict[str, object]]:
    if not user.can("read:report"):
        raise HTTPException(status_code=403, detail="permission denied")
    return list_runs()


@app.get("/api/afsim/scenario")
async def afsim_scenario(
    demo_name: str = "simple_scenario",
    input_file: str | None = None,
    user: User = Depends(current_user),
) -> dict[str, object]:
    if not user.can("read:state"):
        raise HTTPException(status_code=403, detail="permission denied")
    return parse_demo_scenario(demo_name, input_file)


@app.get("/api/afsim/designs")
async def afsim_designs(user: User = Depends(current_user)) -> list[dict[str, object]]:
    if not user.can("read:state"):
        raise HTTPException(status_code=403, detail="permission denied")
    return list_generated_scenarios()


@app.post("/api/afsim/designs")
async def afsim_create_design(payload: AFSimScenarioDesign, user: User = Depends(current_user)) -> dict[str, object]:
    if not user.can("edit:scenario"):
        raise HTTPException(status_code=403, detail="permission denied")
    try:
        result = generate_scenario(payload)
        parsed = parse_scenario_file(Path(str(result["scenario_path"])))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    storage.add_event("afsim.design.generated", result)
    return {**result, "parsed": parsed}


@app.get("/api/afsim/designs/{scenario_id}")
async def afsim_read_design(scenario_id: str, user: User = Depends(current_user)) -> dict[str, object]:
    if not user.can("read:state"):
        raise HTTPException(status_code=403, detail="permission denied")
    try:
        result = read_generated_scenario(scenario_id)
        parsed = parse_scenario_file(Path(str(result["scenario_path"])))
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {**result, "parsed": parsed}


@app.post("/api/afsim/designs/{scenario_id}/run")
async def afsim_run_design(
    scenario_id: str,
    payload: AFSimGeneratedRunRequest,
    user: User = Depends(current_user),
) -> dict[str, object]:
    if not user.can("control:sim"):
        raise HTTPException(status_code=403, detail="permission denied")
    try:
        result = run_generated_scenario(scenario_id, payload.timeout_seconds)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    storage.add_event("afsim.generated.run", result)
    return result


@app.post("/api/afsim/designs/{scenario_id}/launch-map")
async def afsim_launch_design_map(scenario_id: str, user: User = Depends(current_user)) -> dict[str, object]:
    if not user.can("control:sim"):
        raise HTTPException(status_code=403, detail="permission denied")
    try:
        result = launch_generated_warlock(scenario_id)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    storage.add_event("afsim.generated.launch_map", result)
    return result


@app.post("/api/afsim/run")
async def afsim_run(payload: AFSimRunRequest, user: User = Depends(current_user)) -> dict[str, object]:
    if not user.can("control:sim"):
        raise HTTPException(status_code=403, detail="permission denied")
    result = run_demo(payload.demo_name, payload.input_file, payload.timeout_seconds)
    storage.add_event("afsim.run", result)
    return result


@app.post("/api/afsim/analyze")
async def afsim_analyze(payload: dict[str, object], user: User = Depends(current_user)) -> dict[str, object]:
    if not user.can("ask:llm"):
        raise HTTPException(status_code=403, detail="permission denied")
    runs = list_runs()
    run_id = payload.get("run_id")
    run = next((item for item in runs if item.get("run_id") == run_id), runs[0] if runs else None)
    if not run:
        raise HTTPException(status_code=404, detail="AFSIM run not found")
    analysis = await commander.analyze_afsim_run(run)
    storage.add_event("afsim.analysis", {"run_id": run.get("run_id"), "analysis": analysis})
    return {"run": run, "analysis": analysis}


@app.post("/api/afsim/launch-3d")
async def afsim_launch_3d(payload: dict[str, object], user: User = Depends(current_user)) -> dict[str, object]:
    if not user.can("control:sim"):
        raise HTTPException(status_code=403, detail="permission denied")
    result = launch_mystic(payload.get("run_id") if isinstance(payload.get("run_id"), str) else None)
    storage.add_event("afsim.launch_3d", result)
    return result


@app.post("/api/afsim/launch-map")
async def afsim_launch_map(payload: dict[str, object], user: User = Depends(current_user)) -> dict[str, object]:
    if not user.can("control:sim"):
        raise HTTPException(status_code=403, detail="permission denied")
    demo_name = payload.get("demo_name") if isinstance(payload.get("demo_name"), str) else None
    input_file = payload.get("input_file") if isinstance(payload.get("input_file"), str) else None
    result = launch_warlock(demo_name, input_file)
    storage.add_event("afsim.launch_map", result)
    return result


@app.get("/api/afsim/native-display")
async def afsim_native_display(user: User = Depends(current_user)) -> dict[str, object]:
    if not user.can("read:state"):
        raise HTTPException(status_code=403, detail="permission denied")
    return native_display_status(settings.afsim_native_stream_url)


@app.get("/api/afsim/native-frame.jpg")
async def afsim_native_frame(title: str = "Warlock", user: User = Depends(current_user)) -> Response:
    if not user.can("read:state"):
        raise HTTPException(status_code=403, detail="permission denied")
    safe_title = title if title.lower() in {"warlock", "mystic"} else "Warlock"
    return Response(
        content=capture_window_jpeg(safe_title),
        media_type="image/jpeg",
        headers={"Cache-Control": "no-store, max-age=0"},
    )


@app.websocket("/ws/state")
async def state_socket(websocket: WebSocket) -> None:
    await websocket.accept()
    try:
        while True:
            snapshot = engine.tick_from_wall_clock()
            await websocket.send_json(snapshot.model_dump())
            await asyncio.sleep(0.2)
    except WebSocketDisconnect:
        return


def main() -> None:
    import uvicorn

    uvicorn.run("app.main:app", host=settings.host, port=settings.port, reload=True)


if __name__ == "__main__":
    main()
