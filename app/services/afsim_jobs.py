from __future__ import annotations

import asyncio
import subprocess
import threading
import time
import uuid
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from app.services.afsim_runner import run_demo, run_generated_scenario
from app.services.afsim_realtime_bridge import realtime_bridge_manager
from app.services.afsim_workbench import replay_for_run


_MAX_EVENTS = 240
_MAX_WORKERS = 2


class AFSimRunJobManager:
    def __init__(self) -> None:
        self._executor = ThreadPoolExecutor(max_workers=_MAX_WORKERS, thread_name_prefix="afsim-mission")
        self._jobs: dict[str, dict[str, Any]] = {}
        self._events: dict[str, deque[dict[str, Any]]] = {}
        self._processes: dict[str, subprocess.Popen[Any]] = {}
        self._cancel_events: dict[str, threading.Event] = {}
        self._lock = threading.Lock()

    def submit_demo(self, demo_name: str, input_file: str | None, timeout_seconds: int, mode: str = "es") -> dict[str, Any]:
        job_id = self._new_job(
            {
                "kind": "demo",
                "demo_name": demo_name,
                "input_file": input_file,
                "timeout_seconds": timeout_seconds,
                "mode": mode,
            }
        )
        cancel_event = threading.Event()
        with self._lock:
            self._cancel_events[job_id] = cancel_event
        self._executor.submit(self._run_demo_job, job_id, demo_name, input_file, timeout_seconds, mode)
        return self.get(job_id)

    def submit_generated(self, scenario_id: str, timeout_seconds: int, mode: str = "es") -> dict[str, Any]:
        job_id = self._new_job(
            {
                "kind": "generated",
                "scenario_id": scenario_id,
                "timeout_seconds": timeout_seconds,
                "mode": mode,
            }
        )
        cancel_event = threading.Event()
        with self._lock:
            self._cancel_events[job_id] = cancel_event
        self._executor.submit(self._run_generated_job, job_id, scenario_id, timeout_seconds, mode)
        return self.get(job_id)

    def cancel(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                raise FileNotFoundError(f"AFSIM job not found: {job_id}")
            if job.get("status") in {"finished", "failed", "canceled"}:
                return dict(job)
            was_queued = job.get("status") == "queued"
            cancel_event = self._cancel_events.setdefault(job_id, threading.Event())
            cancel_event.set()
            process = self._processes.get(job_id)
            job["cancel_requested_at"] = time.time()

        if was_queued and not process:
            self._emit(job_id, "canceled", {"message": "AFSIM job canceled before start"})
            return self.get(job_id)

        if process and process.poll() is None:
            try:
                process.terminate()
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                try:
                    process.wait(timeout=3)
                except (subprocess.TimeoutExpired, OSError):
                    pass
            except OSError:
                pass

        self._emit(job_id, "canceling", {"message": "AFSIM job cancellation requested"})
        return self.get(job_id)

    def get(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                raise FileNotFoundError(f"AFSIM job not found: {job_id}")
            return dict(job)

    def list(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            jobs = sorted(self._jobs.values(), key=lambda item: float(item.get("created_at", 0.0)), reverse=True)
            return [dict(item) for item in jobs[:limit]]

    def events_since(self, job_id: str, cursor: int = 0) -> tuple[list[dict[str, Any]], int]:
        with self._lock:
            if job_id not in self._jobs:
                raise FileNotFoundError(f"AFSIM job not found: {job_id}")
            events = list(self._events.get(job_id, deque()))
        selected = [event for event in events if int(event["seq"]) > cursor]
        next_cursor = max([cursor, *[int(event["seq"]) for event in selected]], default=cursor)
        return selected, next_cursor

    def _new_job(self, request: dict[str, Any]) -> str:
        job_id = f"job_{int(time.time())}_{uuid.uuid4().hex[:10]}"
        now = time.time()
        with self._lock:
            self._jobs[job_id] = {
                "job_id": job_id,
                "status": "queued",
                "phase": "queued",
                "created_at": now,
                "updated_at": now,
                "started_at": None,
                "completed_at": None,
                "request": request,
                "progress": {},
                "run": None,
                "replay_summary": None,
                "error": None,
                "cancel_requested_at": None,
            }
            self._events[job_id] = deque(maxlen=_MAX_EVENTS)
        self._emit(job_id, "queued", {"message": "AFSIM run queued", "request": request})
        return job_id

    def _emit(self, job_id: str, phase: str, payload: dict[str, Any]) -> None:
        now = time.time()
        with self._lock:
            queue = self._events.setdefault(job_id, deque(maxlen=_MAX_EVENTS))
            seq = int(queue[-1]["seq"]) + 1 if queue else 1
            event = {
                "seq": seq,
                "job_id": job_id,
                "phase": phase,
                "time": now,
                **payload,
            }
            queue.append(event)
            job = self._jobs.get(job_id)
            if job:
                job["phase"] = phase
                job["updated_at"] = now
                if phase in {"starting", "running", "canceling"}:
                    job["status"] = "running"
                    job["started_at"] = job.get("started_at") or now
                elif phase == "finished":
                    job["status"] = "finished"
                    job["completed_at"] = now
                elif phase == "failed":
                    job["status"] = "failed"
                    job["completed_at"] = now
                elif phase == "canceled":
                    job["status"] = "canceled"
                    job["completed_at"] = now
                if "progress" in payload:
                    job["progress"] = payload["progress"]

    def _progress_callback(self, job_id: str):
        def on_progress(snapshot: dict[str, Any]) -> None:
            phase = str(snapshot.get("phase") or "running")
            if snapshot.get("realtime") and snapshot.get("run_id"):
                realtime_bridge_manager.register_run(
                    str(snapshot["run_id"]),
                    working_dir=snapshot.get("working_dir"),
                    run_dir=snapshot.get("run_dir"),
                    output_dir=snapshot.get("output_dir"),
                )
            self._emit(
                job_id,
                phase,
                {
                    "message": "mission.exe progress",
                    "progress": snapshot,
                },
            )

        return on_progress

    def _process_callback(self, job_id: str):
        def on_process(process: subprocess.Popen[Any]) -> None:
            should_cancel = False
            with self._lock:
                self._processes[job_id] = process
                should_cancel = bool(self._cancel_events.get(job_id) and self._cancel_events[job_id].is_set())
            if should_cancel and process.poll() is None:
                try:
                    process.terminate()
                except OSError:
                    pass

        return on_process

    def _cancel_event(self, job_id: str) -> threading.Event:
        with self._lock:
            return self._cancel_events.setdefault(job_id, threading.Event())

    def _run_demo_job(self, job_id: str, demo_name: str, input_file: str | None, timeout_seconds: int, mode: str) -> None:
        try:
            if self._cancel_event(job_id).is_set():
                self._emit(job_id, "canceled", {"message": "AFSIM job canceled before start"})
                return
            result = run_demo(
                demo_name,
                input_file,
                timeout_seconds,
                mode=mode,
                progress_callback=self._progress_callback(job_id),
                process_callback=self._process_callback(job_id),
                cancel_event=self._cancel_event(job_id),
            )
            self._finish(job_id, result)
        except Exception as exc:  # noqa: BLE001 - surfaced to browser client
            self._fail(job_id, exc)
        finally:
            with self._lock:
                self._processes.pop(job_id, None)

    def _run_generated_job(self, job_id: str, scenario_id: str, timeout_seconds: int, mode: str) -> None:
        try:
            if self._cancel_event(job_id).is_set():
                self._emit(job_id, "canceled", {"message": "AFSIM job canceled before start"})
                return
            result = run_generated_scenario(
                scenario_id,
                timeout_seconds,
                mode=mode,
                progress_callback=self._progress_callback(job_id),
                process_callback=self._process_callback(job_id),
                cancel_event=self._cancel_event(job_id),
            )
            self._finish(job_id, result)
        except Exception as exc:  # noqa: BLE001 - surfaced to browser client
            self._fail(job_id, exc)
        finally:
            with self._lock:
                self._processes.pop(job_id, None)

    def _finish(self, job_id: str, result: dict[str, Any]) -> None:
        try:
            replay = replay_for_run(str(result["run_id"]))
            summary = replay.get("summary", {})
        except FileNotFoundError:
            summary = {"run_id": result.get("run_id"), "event_count": 0, "frame_count": 0, "track_count": 0}
        with self._lock:
            job = self._jobs[job_id]
            job["run"] = result
            job["replay_summary"] = summary
        if result.get("canceled"):
            self._emit(
                job_id,
                "canceled",
                {
                    "message": "mission.exe canceled",
                    "run_id": result.get("run_id"),
                    "returncode": result.get("returncode"),
                    "run": result,
                    "replay_summary": summary,
                },
            )
            return
        self._emit(
            job_id,
            "finished",
            {
                "message": "mission.exe finished",
                "run_id": result.get("run_id"),
                "returncode": result.get("returncode"),
                "run": result,
                "replay_summary": summary,
            },
        )

    def _fail(self, job_id: str, exc: Exception) -> None:
        with self._lock:
            job = self._jobs[job_id]
            job["error"] = str(exc)
            canceled = bool(self._cancel_events.get(job_id) and self._cancel_events[job_id].is_set())
        if canceled:
            self._emit(job_id, "canceled", {"message": "AFSIM job canceled", "error": str(exc)})
            return
        self._emit(job_id, "failed", {"message": str(exc), "error": str(exc)})


afsim_job_manager = AFSimRunJobManager()


async def stream_job_events(job_id: str, start_cursor: int = 0):
    cursor = start_cursor
    while True:
        events, cursor = afsim_job_manager.events_since(job_id, cursor)
        for event in events:
            yield event
        job = afsim_job_manager.get(job_id)
        if job.get("status") in {"finished", "failed", "canceled"} and not events:
            return
        await asyncio.sleep(0.5)
