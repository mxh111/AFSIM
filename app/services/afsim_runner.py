from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.core.config import PROJECT_ROOT, settings
from app.services.afsim_design import generated_input_path


RUN_ROOT = PROJECT_ROOT / "runtime" / "afsim_runs"


@dataclass(frozen=True)
class AFSimPaths:
    root: Path
    bin_dir: Path
    demos_dir: Path
    mission_exe: Path
    warlock_exe: Path
    mystic_exe: Path


def afsim_paths() -> AFSimPaths:
    root = settings.afsim_root
    bin_dir = root / "bin"
    return AFSimPaths(
        root=root,
        bin_dir=bin_dir,
        demos_dir=root / "demos",
        mission_exe=bin_dir / "mission.exe",
        warlock_exe=bin_dir / "warlock.exe",
        mystic_exe=bin_dir / "mystic.exe",
    )


def status() -> dict[str, Any]:
    paths = afsim_paths()
    return {
        "root": str(paths.root),
        "root_exists": paths.root.exists(),
        "bin_exists": paths.bin_dir.exists(),
        "demos_exists": paths.demos_dir.exists(),
        "mission_exists": paths.mission_exe.exists(),
        "warlock_exists": paths.warlock_exe.exists(),
        "mystic_exists": paths.mystic_exe.exists(),
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
) -> dict[str, Any]:
    paths = afsim_paths()
    if not paths.mission_exe.exists():
        raise FileNotFoundError(f"mission.exe not found: {paths.mission_exe}")
    if not input_path.exists() or input_path.suffix.lower() != ".txt":
        raise FileNotFoundError(f"input file not found: {input_path}")

    output_dir = working_dir / "output"
    output_dir.mkdir(exist_ok=True)
    run_dir = RUN_ROOT / run_id
    started_at = time.time()
    command = [str(paths.mission_exe), input_path.name]
    proc = subprocess.run(
        command,
        cwd=str(working_dir),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="ignore",
        timeout=timeout_seconds,
    )
    completed_at = time.time()
    files = _copy_outputs(output_dir, run_dir, started_at)
    result = {
        "run_id": run_id,
        "source": source,
        "input_file": input_path.name,
        "command": command,
        "working_dir": str(working_dir),
        "run_dir": str(run_dir),
        "returncode": proc.returncode,
        "duration_seconds": round(completed_at - started_at, 3),
        "stdout": proc.stdout[-12000:],
        "stderr": proc.stderr[-12000:],
        "files": files,
        "summary": _summarize_log(files, proc.stdout, proc.stderr),
    }
    if metadata:
        result.update(metadata)
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "run.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def run_demo(demo_name: str, input_file: str | None = None, timeout_seconds: int = 120) -> dict[str, Any]:
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

    run_id = f"{int(time.time())}_{demo_name}_{input_path.stem}"
    return _run_input(
        input_path=input_path,
        working_dir=demo_dir,
        run_id=run_id,
        source="demo",
        timeout_seconds=timeout_seconds,
        metadata={"demo_name": demo_name},
    )


def run_generated_scenario(scenario_id: str, timeout_seconds: int = 120) -> dict[str, Any]:
    input_path = generated_input_path(scenario_id)
    run_id = f"{int(time.time())}_generated_{scenario_id}"
    return _run_input(
        input_path=input_path,
        working_dir=input_path.parent,
        run_id=run_id,
        source="generated",
        timeout_seconds=timeout_seconds,
        metadata={"scenario_id": scenario_id, "demo_name": f"generated:{scenario_id}"},
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


def launch_mystic(run_id: str | None = None) -> dict[str, Any]:
    paths = afsim_paths()
    if not paths.mystic_exe.exists():
        raise FileNotFoundError(f"mystic.exe not found: {paths.mystic_exe}")
    runs = list_runs()
    run = next((item for item in runs if item.get("run_id") == run_id), runs[0] if runs else None)
    if not run:
        raise FileNotFoundError("AFSIM run not found")
    aer_file = next((file for file in run.get("files", []) if str(file.get("name", "")).lower().endswith(".aer")), None)
    if not aer_file:
        raise FileNotFoundError("No .aer output found for this run")
    aer_path = Path(str(aer_file["path"]))
    proc = subprocess.Popen([str(paths.mystic_exe), str(aer_path)], cwd=str(paths.root))
    return {"pid": proc.pid, "tool": "mystic", "aer_path": str(aer_path), "run_id": run.get("run_id")}


def launch_warlock(demo_name: str | None = None, input_file: str | None = None) -> dict[str, Any]:
    paths = afsim_paths()
    if not paths.warlock_exe.exists():
        raise FileNotFoundError(f"warlock.exe not found: {paths.warlock_exe}")
    if not demo_name:
        proc = subprocess.Popen([str(paths.warlock_exe)], cwd=str(paths.root))
        return {"pid": proc.pid, "tool": "warlock", "mode": "start_dialog"}

    demo_dir = _safe_child(paths.demos_dir, demo_name)
    if not demo_dir.exists() or not demo_dir.is_dir():
        raise FileNotFoundError(f"AFSIM demo not found: {demo_name}")
    candidates = _candidate_inputs(demo_dir)
    if not candidates:
        raise FileNotFoundError(f"no runnable .txt input found in {demo_dir}")
    if input_file:
        input_path = _safe_child(demo_dir, input_file)
    else:
        input_path = candidates[0]
    if input_path.suffix.lower() != ".txt" or not input_path.exists():
        raise FileNotFoundError(f"input file not found: {input_path}")
    proc = subprocess.Popen([str(paths.warlock_exe), input_path.name], cwd=str(demo_dir))
    return {
        "pid": proc.pid,
        "tool": "warlock",
        "mode": "scenario",
        "demo_name": demo_name,
        "input_file": input_path.name,
        "working_dir": str(demo_dir),
    }


def launch_warlock_input(input_path: Path, mode: str = "scenario", metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    paths = afsim_paths()
    if not paths.warlock_exe.exists():
        raise FileNotFoundError(f"warlock.exe not found: {paths.warlock_exe}")
    if not input_path.exists() or input_path.suffix.lower() != ".txt":
        raise FileNotFoundError(f"input file not found: {input_path}")
    proc = subprocess.Popen([str(paths.warlock_exe), input_path.name], cwd=str(input_path.parent))
    result = {
        "pid": proc.pid,
        "tool": "warlock",
        "mode": mode,
        "input_file": input_path.name,
        "working_dir": str(input_path.parent),
        "scenario_path": str(input_path),
    }
    if metadata:
        result.update(metadata)
    return result


def launch_generated_warlock(scenario_id: str) -> dict[str, Any]:
    input_path = generated_input_path(scenario_id)
    return launch_warlock_input(input_path, mode="generated", metadata={"scenario_id": scenario_id})
