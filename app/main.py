from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from app.core.config import PROJECT_ROOT, settings
from app.core.security import User, role_catalog, user_from_token
from app.models import CommanderRequest, SimulationControl
from app.services.afsim_adapter import afsim_available, export_scenario_draft
from app.services.llm import CommanderLLM
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
    return {"ok": True, "app": settings.app_name, "afsim": afsim_available()}


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
