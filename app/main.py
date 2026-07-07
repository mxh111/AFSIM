from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from app.core.config import PROJECT_ROOT, settings
from app.core.security import User, role_catalog, user_from_token
from app.models import (
    AFSimAgentTickRequest,
    AFSimDraftRequest,
    AFSimGeneratedRunRequest,
    AFSimLayerStateUpdate,
    AFSimRunRequest,
    AFSimScenarioDesign,
    CommanderRequest,
    SimulationControl,
)
from app.services.afsim_adapter import afsim_available, export_scenario_draft
from app.services.afsim_aer_reader import aer_capabilities
from app.services.afsim_design import (
    delete_generated_scenario,
    generate_scenario,
    list_generated_scenarios,
    platform_templates,
    read_generated_scenario,
    scene_overview,
)
from app.services.afsim_jobs import afsim_job_manager, stream_job_events
from app.services.afsim_maps import (
    compose_raster_texture,
    map_resource_manifest,
    parse_bbox,
    raster_metadata,
    read_raster_tile,
    vector_geojson,
)
from app.services.afsim_parser import parse_demo_scenario, parse_scenario_file
from app.services.afsim_realtime import build_realtime_frame
from app.services.afsim_runner import (
    discover_demos,
    list_runs,
    run_demo,
    run_generated_scenario,
    status as afsim_runner_status,
)
from app.services.afsim_workbench import (
    build_workbench_state,
    latest_replay,
    list_scene_drafts,
    load_layer_catalog,
    replay_for_run,
    restore_scene_draft,
    save_layer_state,
    save_scene_draft,
)
from app.services.llm import CommanderLLM
from app.services.reports import build_report, write_markdown_report
from app.services.simulation import DEFAULT_SCENARIO_PATH, SimulationEngine
from app.services.storage import Storage


storage = Storage()
engine = SimulationEngine(storage)
commander = CommanderLLM()

app = FastAPI(title=settings.app_name, version="0.1.0")
app.mount("/static", StaticFiles(directory=PROJECT_ROOT / "app" / "static"), name="static")


@app.middleware("http")
async def no_cache_for_dev_assets(request, call_next):
    response = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-store, max-age=0"
        response.headers["Pragma"] = "no-cache"
    return response


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


@app.get("/api/afsim/aer/status")
async def afsim_aer_status(user: User = Depends(current_user)) -> dict[str, object]:
    if not user.can("read:state"):
        raise HTTPException(status_code=403, detail="permission denied")
    return aer_capabilities()


@app.get("/api/afsim/maps")
async def afsim_map_resources(user: User = Depends(current_user)) -> dict[str, object]:
    if not user.can("read:state"):
        raise HTTPException(status_code=403, detail="permission denied")
    return map_resource_manifest()


@app.get("/api/afsim/maps/vectors/{layer_name}")
async def afsim_vector_layer(
    layer_name: str,
    bbox: str | None = None,
    simplify: float = 0.02,
    user: User = Depends(current_user),
) -> JSONResponse:
    if not user.can("read:state"):
        raise HTTPException(status_code=403, detail="permission denied")
    layer_id = layer_name[:-8] if layer_name.endswith(".geojson") else layer_name
    try:
        payload = vector_geojson(layer_id, bbox=parse_bbox(bbox), simplify=max(0.0, min(float(simplify), 2.0)))
    except (FileNotFoundError, KeyError, ValueError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return JSONResponse(content=payload, media_type="application/geo+json")


@app.get("/api/afsim/maps/{map_id}/metadata")
async def afsim_map_metadata(map_id: str, user: User = Depends(current_user)) -> dict[str, object]:
    if not user.can("read:state"):
        raise HTTPException(status_code=403, detail="permission denied")
    try:
        return raster_metadata(map_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/afsim/maps/{map_id}/texture.jpg")
async def afsim_map_texture(map_id: str, z: int = 3, user: User = Depends(current_user)) -> FileResponse:
    if not user.can("read:state"):
        raise HTTPException(status_code=403, detail="permission denied")
    try:
        path, media_type, _meta = compose_raster_texture(map_id, z)
    except (FileNotFoundError, KeyError, ValueError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return FileResponse(
        path,
        media_type=media_type,
        headers={"Cache-Control": "public, max-age=86400, immutable"},
    )


@app.get("/api/afsim/maps/{map_id}/{z}/{x}/{tile}")
async def afsim_map_tile(
    map_id: str,
    z: int,
    x: int,
    tile: str,
    user: User = Depends(current_user),
) -> Response:
    if not user.can("read:state"):
        raise HTTPException(status_code=403, detail="permission denied")
    y_token = tile.split(".", 1)[0]
    try:
        y = int(y_token)
        body, media_type, _meta = read_raster_tile(map_id, z, x, y)
    except (FileNotFoundError, KeyError, ValueError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return Response(
        content=body,
        media_type=media_type,
        headers={"Cache-Control": "public, max-age=86400, immutable"},
    )


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


@app.get("/api/afsim/jobs")
async def afsim_jobs(user: User = Depends(current_user)) -> list[dict[str, object]]:
    if not user.can("read:report"):
        raise HTTPException(status_code=403, detail="permission denied")
    return afsim_job_manager.list()


@app.get("/api/afsim/jobs/{job_id}")
async def afsim_job(job_id: str, user: User = Depends(current_user)) -> dict[str, object]:
    if not user.can("read:report"):
        raise HTTPException(status_code=403, detail="permission denied")
    try:
        return afsim_job_manager.get(job_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/afsim/jobs/{job_id}/cancel")
async def afsim_cancel_job(job_id: str, user: User = Depends(current_user)) -> dict[str, object]:
    if not user.can("control:sim"):
        raise HTTPException(status_code=403, detail="permission denied")
    try:
        job = afsim_job_manager.cancel(job_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    storage.add_event("afsim.job.canceled", {"job_id": job_id, "status": job.get("status")})
    return job


@app.get("/api/afsim/jobs/{job_id}/replay")
async def afsim_job_replay(job_id: str, user: User = Depends(current_user)) -> dict[str, object]:
    if not user.can("read:report"):
        raise HTTPException(status_code=403, detail="permission denied")
    try:
        job = afsim_job_manager.get(job_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    run = job.get("run") or {}
    run_id = run.get("run_id")
    if not run_id:
        raise HTTPException(status_code=409, detail="AFSIM job has not produced a run yet")
    return replay_for_run(str(run_id))


@app.get("/api/afsim/workbench")
async def afsim_workbench(
    scenario_id: str | None = None,
    demo_name: str = "simple_scenario",
    input_file: str | None = None,
    user: User = Depends(current_user),
) -> dict[str, object]:
    if not user.can("read:state"):
        raise HTTPException(status_code=403, detail="permission denied")
    try:
        return build_workbench_state(scenario_id=scenario_id, demo_name=demo_name, input_file=input_file)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/afsim/layers")
async def afsim_layers(user: User = Depends(current_user)) -> dict[str, object]:
    if not user.can("read:state"):
        raise HTTPException(status_code=403, detail="permission denied")
    return {"layers": load_layer_catalog()}


@app.post("/api/afsim/layers/state")
async def afsim_layer_state(
    payload: AFSimLayerStateUpdate,
    user: User = Depends(current_user),
) -> dict[str, object]:
    if not user.can("edit:scenario"):
        raise HTTPException(status_code=403, detail="permission denied")
    result = save_layer_state(payload.model_dump())
    storage.add_event("afsim.layers.updated", {"layer_count": len(result["layers"])})
    return result


@app.get("/api/afsim/replay/latest")
async def afsim_latest_replay(user: User = Depends(current_user)) -> dict[str, object]:
    if not user.can("read:report"):
        raise HTTPException(status_code=403, detail="permission denied")
    return latest_replay()


@app.get("/api/afsim/replay/{run_id}")
async def afsim_run_replay(run_id: str, user: User = Depends(current_user)) -> dict[str, object]:
    if not user.can("read:report"):
        raise HTTPException(status_code=403, detail="permission denied")
    try:
        return replay_for_run(run_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/afsim/drafts")
async def afsim_drafts(user: User = Depends(current_user)) -> dict[str, object]:
    if not user.can("read:state"):
        raise HTTPException(status_code=403, detail="permission denied")
    return list_scene_drafts()


@app.post("/api/afsim/drafts")
async def afsim_save_draft(
    payload: AFSimDraftRequest,
    user: User = Depends(current_user),
) -> dict[str, object]:
    if not user.can("edit:scenario"):
        raise HTTPException(status_code=403, detail="permission denied")
    result = save_scene_draft(payload.model_dump())
    storage.add_event("afsim.draft.saved", {"draft_id": result["draft_id"], "path": result["path"]})
    return result


@app.post("/api/afsim/drafts/{draft_id}/restore")
async def afsim_restore_draft(draft_id: str, user: User = Depends(current_user)) -> dict[str, object]:
    if not user.can("edit:scenario"):
        raise HTTPException(status_code=403, detail="permission denied")
    try:
        result = restore_scene_draft(draft_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    storage.add_event("afsim.draft.restored", {"draft_id": draft_id})
    return result


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


@app.get("/api/afsim/platform-templates")
async def afsim_platform_templates(user: User = Depends(current_user)) -> list[dict[str, object]]:
    if not user.can("read:state"):
        raise HTTPException(status_code=403, detail="permission denied")
    return platform_templates()


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


@app.delete("/api/afsim/designs/{scenario_id}")
async def afsim_delete_design(scenario_id: str, user: User = Depends(current_user)) -> dict[str, object]:
    if not user.can("edit:scenario"):
        raise HTTPException(status_code=403, detail="permission denied")
    try:
        result = delete_generated_scenario(scenario_id)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    storage.add_event("afsim.design.deleted", result)
    return result


@app.get("/api/afsim/designs/{scenario_id}/scene")
async def afsim_generated_scene(scenario_id: str, user: User = Depends(current_user)) -> dict[str, object]:
    if not user.can("read:state"):
        raise HTTPException(status_code=403, detail="permission denied")
    try:
        result = read_generated_scenario(scenario_id)
        parsed = parse_scenario_file(Path(str(result["scenario_path"])))
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return scene_overview(parsed)


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
        replay = replay_for_run(str(result["run_id"]))
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    storage.add_event("afsim.generated.run", result)
    return {"run": result, "replay": replay}


@app.post("/api/afsim/designs/{scenario_id}/run/jobs")
async def afsim_run_design_job(
    scenario_id: str,
    payload: AFSimGeneratedRunRequest,
    user: User = Depends(current_user),
) -> dict[str, object]:
    if not user.can("control:sim"):
        raise HTTPException(status_code=403, detail="permission denied")
    try:
        job = afsim_job_manager.submit_generated(scenario_id, payload.timeout_seconds)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    storage.add_event("afsim.generated.job.started", {"job_id": job["job_id"], "scenario_id": scenario_id})
    return job


@app.post("/api/agent/tick")
async def agent_tick(payload: AFSimAgentTickRequest, user: User = Depends(current_user)) -> dict[str, object]:
    if not user.can("ask:llm"):
        raise HTTPException(status_code=403, detail="permission denied")
    if not user.can("control:sim") and payload.autonomy == "auto_apply":
        raise HTTPException(status_code=403, detail="permission denied")
    engine.step(payload.step_seconds)
    request = CommanderRequest(objective=payload.objective, side=payload.side, autonomy=payload.autonomy)
    advice = await commander.advise(request, engine.snapshot())
    applied = []
    if payload.autonomy == "auto_apply":
        applied = engine.apply_commands(advice.commands)
    storage.add_event(
        "agent.tick",
        {
            "objective": payload.objective,
            "side": payload.side,
            "autonomy": payload.autonomy,
            "advice": advice.model_dump(),
            "applied": applied,
        },
    )
    return {"advice": advice.model_dump(), "applied": applied, "state": engine.snapshot().model_dump()}


@app.post("/api/afsim/run")
async def afsim_run(payload: AFSimRunRequest, user: User = Depends(current_user)) -> dict[str, object]:
    if not user.can("control:sim"):
        raise HTTPException(status_code=403, detail="permission denied")
    result = run_demo(payload.demo_name, payload.input_file, payload.timeout_seconds)
    replay = replay_for_run(str(result["run_id"]))
    storage.add_event("afsim.run", result)
    return {"run": result, "replay": replay}


@app.post("/api/afsim/run/jobs")
async def afsim_run_job(payload: AFSimRunRequest, user: User = Depends(current_user)) -> dict[str, object]:
    if not user.can("control:sim"):
        raise HTTPException(status_code=403, detail="permission denied")
    try:
        job = afsim_job_manager.submit_demo(payload.demo_name, payload.input_file, payload.timeout_seconds)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    storage.add_event("afsim.job.started", {"job_id": job["job_id"], "demo_name": payload.demo_name})
    return job


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


@app.websocket("/ws/afsim/preview")
@app.websocket("/ws/afsim/realtime")
async def afsim_preview_socket(websocket: WebSocket) -> None:
    await websocket.accept()
    query = websocket.query_params
    scenario_id = query.get("scenario_id")
    demo_name = query.get("demo_name") or "simple_scenario"
    input_file = query.get("input_file")
    interval_seconds = float(query.get("interval_seconds") or 0.75)
    loop_seconds = float(query.get("loop_seconds") or 120.0)
    try:
        if scenario_id:
            scenario = read_generated_scenario(scenario_id)
            parsed = parse_scenario_file(Path(str(scenario["scenario_path"])))
            source = f"generated:{scenario_id}"
        else:
            parsed = parse_demo_scenario(demo_name, input_file)
            source = f"demo:{demo_name}/{parsed.get('input_name', input_file or '')}"
        frame_id = 0
        sim_time = 0.0
        while True:
            await websocket.send_json(
                build_realtime_frame(
                    parsed,
                    sim_time,
                    frame_id=frame_id,
                    source=source,
                    loop_seconds=loop_seconds,
                )
            )
            frame_id += 1
            sim_time += interval_seconds
            await asyncio.sleep(max(0.1, interval_seconds))
    except WebSocketDisconnect:
        return


@app.websocket("/ws/afsim/jobs/{job_id}")
async def afsim_job_socket(websocket: WebSocket, job_id: str) -> None:
    await websocket.accept()
    try:
        cursor = int(websocket.query_params.get("cursor") or 0)
    except ValueError:
        cursor = 0
    try:
        await websocket.send_json({"type": "job", "job": afsim_job_manager.get(job_id)})
        async for event in stream_job_events(job_id, cursor):
            await websocket.send_json({"type": "progress", "event": event, "job": afsim_job_manager.get(job_id)})
        await websocket.send_json({"type": "job", "job": afsim_job_manager.get(job_id)})
    except FileNotFoundError as exc:
        await websocket.send_json({"type": "error", "error": str(exc)})
    except WebSocketDisconnect:
        return
    except Exception as exc:
        await websocket.send_json({"error": str(exc), "source": "afsim-preview"})


def main() -> None:
    import uvicorn

    uvicorn.run("app.main:app", host=settings.host, port=settings.port, reload=True)


if __name__ == "__main__":
    main()
