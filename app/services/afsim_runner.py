from __future__ import annotations

import json
import re
import shutil
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from app.core.config import PROJECT_ROOT, settings
from app.services.afsim_design import generated_input_path


RUN_ROOT = PROJECT_ROOT / "runtime" / "afsim_runs"
WORKDIR_ROOT = PROJECT_ROOT / "runtime" / "afsim_workdirs"


@dataclass(frozen=True)
class AFSimPaths:
    root: Path
    bin_dir: Path
    demos_dir: Path
    mission_exe: Path


def afsim_paths() -> AFSimPaths:
    root = settings.afsim_root
    bin_dir = root / "bin"
    return AFSimPaths(
        root=root,
        bin_dir=bin_dir,
        demos_dir=root / "demos",
        mission_exe=bin_dir / "mission.exe",
    )


def status() -> dict[str, Any]:
    paths = afsim_paths()
    return {
        "root": str(paths.root),
        "root_exists": paths.root.exists(),
        "bin_exists": paths.bin_dir.exists(),
        "demos_exists": paths.demos_dir.exists(),
        "mission_exists": paths.mission_exe.exists(),
    }


def _safe_child(parent: Path, child_name: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9_.\-]+", child_name):
        raise ValueError(f"invalid child name: {child_name}")
    child = (parent / child_name).resolve()
    parent_resolved = parent.resolve()
    if parent_resolved not in child.parents and child != parent_resolved:
        raise ValueError("path escaped parent")
    return child


def _candidate_inputs(demo_dir: Path) -> list[Path]:
    candidates: list[Path] = []
    for path in sorted(demo_dir.glob("*.txt")):
        name = path.name.lower()
        if name in {"terrain.txt", "event_pipe.txt", "event_output.txt", "dis_realtime.txt"}:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")[:12000].lower()
        if "end_time" in text or "include" in text or "platform" in text:
            candidates.append(path)
    preferred = [p for p in candidates if p.stem.lower() == demo_dir.name.lower()]
    return preferred + [p for p in candidates if p not in preferred]


def discover_demos(limit: int = 200) -> list[dict[str, Any]]:
    paths = afsim_paths()
    if not paths.demos_dir.exists():
        return []
    demos: list[dict[str, Any]] = []
    for demo_dir in sorted([p for p in paths.demos_dir.iterdir() if p.is_dir()], key=lambda p: p.name):
        inputs = _candidate_inputs(demo_dir)
        if not inputs:
            continue
        readme = demo_dir / "README.md"
        demos.append(
            {
                "name": demo_dir.name,
                "path": str(demo_dir),
                "default_input": inputs[0].name,
                "inputs": [p.name for p in inputs[:20]],
                "has_readme": readme.exists(),
            }
        )
        if len(demos) >= limit:
            break
    return demos


def _copy_outputs(output_dir: Path, run_dir: Path, started_at: float) -> list[dict[str, Any]]:
    copied: list[dict[str, Any]] = []
    if not output_dir.exists():
        return copied
    run_dir.mkdir(parents=True, exist_ok=True)
    for src in sorted([p for p in output_dir.iterdir() if p.is_file()], key=lambda p: p.name):
        stat = src.stat()
        if stat.st_mtime + 5 < started_at:
            continue
        dest = run_dir / src.name
        try:
            shutil.copy2(src, dest)
        except OSError:
            continue
        copied.append(
            {
                "name": src.name,
                "source": str(src),
                "path": str(dest),
                "size": stat.st_size,
                "modified": stat.st_mtime,
            }
        )
    return copied


def _tail_text(path: Path, max_chars: int = 12000) -> str:
    if not path.exists() or path.stat().st_size == 0:
        return ""
    with path.open("rb") as handle:
        size = path.stat().st_size
        handle.seek(max(0, size - max_chars))
        return handle.read().decode("utf-8", errors="ignore")


def _output_snapshot(
    output_dir: Path,
    run_dir: Path,
    started_at: float,
    stdout_path: Path | None = None,
    stderr_path: Path | None = None,
) -> dict[str, Any]:
    files: list[dict[str, Any]] = []
    tail_lines: list[str] = []
    if output_dir.exists():
        for src in sorted([p for p in output_dir.iterdir() if p.is_file()], key=lambda p: p.name):
            stat = src.stat()
            if stat.st_mtime + 5 < started_at:
                continue
            files.append(
                {
                    "name": src.name,
                    "source": str(src),
                    "size": stat.st_size,
                    "modified": stat.st_mtime,
                }
            )
            if src.suffix.lower() in {".log", ".evt", ".txt", ".csv"}:
                tail_lines.extend(line.strip() for line in _tail_text(src, 8000).splitlines() if line.strip())
    for aux in [stdout_path, stderr_path]:
        if aux and aux.exists():
            stat = aux.stat()
            files.append({"name": aux.name, "source": str(aux), "path": str(aux), "size": stat.st_size, "modified": stat.st_mtime})
            tail_lines.extend(line.strip() for line in _tail_text(aux, 8000).splitlines() if line.strip())
    return {
        "elapsed_seconds": round(time.time() - started_at, 3),
        "files": files[-80:],
        "tail": tail_lines[-30:],
        "run_dir": str(run_dir),
        "output_dir": str(output_dir),
    }


def _summarize_log(files: list[dict[str, Any]], stdout: str, stderr: str) -> dict[str, Any]:
    log_text = ""
    for file in files:
        if str(file["name"]).lower().endswith(".log"):
            log_text += Path(str(file["path"])).read_text(encoding="utf-8", errors="ignore") + "\n"
    combined = (stdout + "\n" + stderr + "\n" + log_text).strip()
    lines = [line.strip() for line in combined.splitlines() if line.strip()]
    return {
        "version": next((line for line in lines if "WSF Version" in line or "version wsf" in line), ""),
        "started": any("Starting simulation" in line or re.search(r"\bstart\b", line) for line in lines),
        "completed": any("Simulation complete" in line or re.search(r"\bcomplete\b", line) for line in lines),
        "fatal": [line for line in lines if "FATAL" in line.upper()][:10],
        "warnings": [line for line in lines if "WARN" in line.upper()][:10],
        "referenced_files": [line for line in lines if re.search(r"\bfile\b", line)][:30],
        "tail": lines[-20:],
    }


def _run_input(
    input_path: Path,
    working_dir: Path,
    run_id: str,
    source: str,
    timeout_seconds: int,
    metadata: dict[str, Any] | None = None,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
    process_callback: Callable[[subprocess.Popen[str]], None] | None = None,
    cancel_event: threading.Event | None = None,
) -> dict[str, Any]:
    paths = afsim_paths()
    if not paths.mission_exe.exists():
        raise FileNotFoundError(f"mission.exe not found: {paths.mission_exe}")
    if not input_path.exists() or input_path.suffix.lower() != ".txt":
        raise FileNotFoundError(f"input file not found: {input_path}")

    output_dir = working_dir / "output"
    output_dir.mkdir(exist_ok=True)
    run_dir = RUN_ROOT / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    started_at = time.time()
    command = [str(paths.mission_exe), input_path.name]
    stdout_path = run_dir / "mission.stdout.log"
    stderr_path = run_dir / "mission.stderr.log"
    if progress_callback:
        progress_callback(
            {
                "phase": "starting",
                "command": command,
                "working_dir": str(working_dir),
                "run_dir": str(run_dir),
                "output_dir": str(output_dir),
                "elapsed_seconds": 0.0,
                "files": [],
                "tail": [],
            }
        )
    timed_out = False
    canceled = False
    with stdout_path.open("w", encoding="utf-8", errors="ignore") as stdout_handle, stderr_path.open("w", encoding="utf-8", errors="ignore") as stderr_handle:
        proc = subprocess.Popen(
            command,
            cwd=str(working_dir),
            stdout=stdout_handle,
            stderr=stderr_handle,
            text=True,
            encoding="utf-8",
            errors="ignore",
        )
        if process_callback:
            process_callback(proc)
        while proc.poll() is None:
            elapsed = time.time() - started_at
            if cancel_event and cancel_event.is_set():
                canceled = True
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()
                break
            if elapsed > timeout_seconds:
                timed_out = True
                proc.kill()
                break
            if progress_callback:
                snapshot = _output_snapshot(output_dir, run_dir, started_at, stdout_path, stderr_path)
                snapshot.update({"phase": "running", "command": command, "working_dir": str(working_dir)})
                progress_callback(snapshot)
            time.sleep(0.75)
        proc.wait()
    completed_at = time.time()
    stdout = _tail_text(stdout_path)
    stderr = _tail_text(stderr_path)
    if timed_out:
        stderr = (stderr + f"\nTIMEOUT: mission.exe exceeded {timeout_seconds}s and was killed.").strip()
    if canceled:
        stderr = (stderr + "\nCANCELED: mission.exe was canceled by AFSIM_LLM.").strip()
    files = _copy_outputs(output_dir, run_dir, started_at)
    for aux in [stdout_path, stderr_path]:
        if aux.exists() and aux.stat().st_size:
            stat = aux.stat()
            files.append(
                {
                    "name": aux.name,
                    "source": "process",
                    "path": str(aux),
                    "size": stat.st_size,
                    "modified": stat.st_mtime,
                }
            )
    result = {
        "run_id": run_id,
        "source": source,
        "input_file": input_path.name,
        "command": command,
        "working_dir": str(working_dir),
        "run_dir": str(run_dir),
        "returncode": proc.returncode,
        "duration_seconds": round(completed_at - started_at, 3),
        "stdout": stdout[-12000:],
        "stderr": stderr[-12000:],
        "files": files,
        "summary": _summarize_log(files, stdout, stderr),
        "timed_out": timed_out,
        "canceled": canceled,
    }
    if metadata:
        result.update(metadata)
    (run_dir / "run.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    if progress_callback:
        snapshot = _output_snapshot(output_dir, run_dir, started_at, stdout_path, stderr_path)
        snapshot.update(
            {
                "phase": "canceled" if canceled else "finished",
                "command": command,
                "working_dir": str(working_dir),
                "returncode": proc.returncode,
            }
        )
        progress_callback(snapshot)
    return result


def _copy_demo_tree(src: Path, dest: Path) -> None:
    if src.is_dir():
        shutil.copytree(src, dest, ignore=shutil.ignore_patterns("output", "__pycache__"))
    else:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)


def _demo_file_path_dependencies(input_path: Path, demo_dir: Path, demos_dir: Path) -> list[Path]:
    dependencies: list[Path] = []
    demos_root = demos_dir.resolve()
    demo_root = demo_dir.resolve()
    text = input_path.read_text(encoding="utf-8", errors="ignore")
    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].strip()
        match = re.match(r"^file_path\s+(.+)$", line, flags=re.IGNORECASE)
        if not match:
            continue
        value = match.group(1).strip().strip('"').strip("'")
        if not value or value == ".":
            continue
        candidate = (demo_dir / value).resolve()
        if not candidate.exists() or candidate == demo_root:
            continue
        if demos_root == candidate or demos_root in candidate.parents:
            dependencies.append(candidate)
    return sorted(set(dependencies), key=lambda path: str(path).lower())


def _stage_demo_workdir(demo_dir: Path, input_path: Path, run_id: str) -> Path:
    paths = afsim_paths()
    work_root = WORKDIR_ROOT / run_id
    work_dir = work_root / demo_dir.name
    if work_root.exists():
        raise FileExistsError(f"AFSIM workdir already exists: {work_root}")
    work_root.mkdir(parents=True, exist_ok=True)
    demos_root = paths.demos_dir.resolve()
    for src in [demo_dir, *_demo_file_path_dependencies(input_path, demo_dir, paths.demos_dir)]:
        relative = src.resolve().relative_to(demos_root)
        _copy_demo_tree(src, work_root / relative)
    return work_dir


def run_demo(
    demo_name: str,
    input_file: str | None = None,
    timeout_seconds: int = 120,
    *,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
    process_callback: Callable[[subprocess.Popen[str]], None] | None = None,
    cancel_event: threading.Event | None = None,
) -> dict[str, Any]:
    paths = afsim_paths()
    demo_dir = _safe_child(paths.demos_dir, demo_name)
    if not demo_dir.exists() or not demo_dir.is_dir():
        raise FileNotFoundError(f"AFSIM demo not found: {demo_name}")

    candidates = _candidate_inputs(demo_dir)
    if not candidates:
        raise FileNotFoundError(f"no runnable .txt input found in {demo_dir}")

    if input_file:
        input_path = _safe_child(demo_dir, input_file)
        if input_path not in candidates and input_path.suffix.lower() != ".txt":
            raise ValueError("input_file must be a .txt file inside the demo directory")
    else:
        input_path = candidates[0]

    run_id = f"{int(time.time())}_{demo_name}_{input_path.stem}_{uuid.uuid4().hex[:8]}"
    work_dir = _stage_demo_workdir(demo_dir, input_path, run_id)
    staged_input = work_dir / input_path.name
    return _run_input(
        input_path=staged_input,
        working_dir=work_dir,
        run_id=run_id,
        source="demo",
        timeout_seconds=timeout_seconds,
        metadata={"demo_name": demo_name, "original_demo_dir": str(demo_dir)},
        progress_callback=progress_callback,
        process_callback=process_callback,
        cancel_event=cancel_event,
    )


def run_generated_scenario(
    scenario_id: str,
    timeout_seconds: int = 120,
    *,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
    process_callback: Callable[[subprocess.Popen[str]], None] | None = None,
    cancel_event: threading.Event | None = None,
) -> dict[str, Any]:
    input_path = generated_input_path(scenario_id)
    run_id = f"{int(time.time())}_generated_{scenario_id}_{uuid.uuid4().hex[:8]}"
    return _run_input(
        input_path=input_path,
        working_dir=input_path.parent,
        run_id=run_id,
        source="generated",
        timeout_seconds=timeout_seconds,
        metadata={"scenario_id": scenario_id, "demo_name": f"generated:{scenario_id}"},
        progress_callback=progress_callback,
        process_callback=process_callback,
        cancel_event=cancel_event,
    )


def list_runs(limit: int = 50) -> list[dict[str, Any]]:
    if not RUN_ROOT.exists():
        return []
    runs: list[dict[str, Any]] = []
    for run_json in sorted(RUN_ROOT.glob("*/run.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:limit]:
        try:
            runs.append(json.loads(run_json.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            continue
    return runs
