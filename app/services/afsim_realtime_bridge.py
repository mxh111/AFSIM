from __future__ import annotations

import asyncio
import csv
import io
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.services.afsim_replay import parse_csv_event_rows, parse_evt_event_text


REALTIME_SCHEMA_VERSION = "afsim-realtime-frame.v1"
REALTIME_SUFFIXES = {".evt", ".csv"}


@dataclass
class TailState:
    path: Path
    position: int = 0
    buffer: str = ""
    record_index: int = 1
    csv_header: list[str] | None = None


class RealtimeBridge:
    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        self._directories: set[Path] = set()
        self._tails: dict[str, TailState] = {}
        self._entities: dict[str, dict[str, Any]] = {}
        self._track_points: dict[str, list[dict[str, float]]] = {}
        self._frame_id = 0
        self._last_sim_time: float | None = None
        self._lock = threading.Lock()

    def add_paths(
        self,
        *,
        working_dir: str | Path | None = None,
        run_dir: str | Path | None = None,
        output_dir: str | Path | None = None,
    ) -> None:
        candidates: list[Path] = []
        if output_dir:
            candidates.append(Path(output_dir))
        if working_dir:
            candidates.append(Path(working_dir) / "output")
        if run_dir:
            candidates.append(Path(run_dir))
        with self._lock:
            for candidate in candidates:
                self._directories.add(candidate)

    def poll(self) -> dict[str, Any] | None:
        files = self._source_files()
        if not files:
            return unavailable_frame(
                self.run_id,
                message="No event_output or csv_event_output files are available for this run_id.",
            )

        events: list[dict[str, Any]] = []
        observations: list[dict[str, Any]] = []
        source = "afsim-event-output"
        source_files: list[dict[str, Any]] = []
        for path in files:
            suffix = path.suffix.lower()
            if suffix == ".csv":
                source = "afsim-csv-event-output"
            parsed_events, parsed_observations = self._read_increment(path)
            events.extend(parsed_events)
            observations.extend(parsed_observations)
            try:
                stat = path.stat()
            except OSError:
                continue
            source_files.append({"name": path.name, "path": str(path), "size": stat.st_size, "modified": stat.st_mtime})

        if not events and not observations:
            return None

        with self._lock:
            for event in events:
                event.setdefault("run_id", self.run_id)
            self._update_entities(observations)
            self._frame_id += 1
            sim_time = self._sim_time(events, observations)
            self._last_sim_time = sim_time
            entities = sorted(self._entities.values(), key=lambda item: str(item.get("id", "")))
            tracks = self._tracks()
            detections = [event for event in events if event.get("type") in {"detected", "detection_attempt"}]
            return {
                "schema_version": REALTIME_SCHEMA_VERSION,
                "type": "afsim_realtime",
                "status": "ok",
                "run_id": self.run_id,
                "frame_id": self._frame_id,
                "source": source,
                "authoritative": True,
                "sim_time": sim_time,
                "entity_count": len(entities),
                "event_count": len(events),
                "entities": entities,
                "events": events,
                "event_ids": [str(event.get("id")) for event in events if event.get("id")],
                "tracks": tracks,
                "detections": detections,
                "source_files": source_files,
            }

    def _source_files(self) -> list[Path]:
        with self._lock:
            directories = list(self._directories)
        files: list[Path] = []
        for directory in directories:
            if not directory.exists() or not directory.is_dir():
                continue
            for path in directory.iterdir():
                if path.is_file() and path.suffix.lower() in REALTIME_SUFFIXES:
                    files.append(path)
        return sorted(set(files), key=lambda item: (item.suffix.lower(), item.name.lower(), str(item)))

    def _tail_state(self, path: Path) -> TailState:
        key = str(path.resolve())
        with self._lock:
            state = self._tails.get(key)
            if not state:
                state = TailState(path=path)
                self._tails[key] = state
            return state

    def _read_increment(self, path: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        state = self._tail_state(path)
        try:
            size = path.stat().st_size
        except OSError:
            return [], []
        if size < state.position:
            state.position = 0
            state.buffer = ""
            state.csv_header = None
            state.record_index = 1
        if size == state.position:
            return [], []
        with path.open("rb") as handle:
            handle.seek(state.position)
            raw = handle.read()
            state.position = handle.tell()
        text = raw.decode("utf-8", errors="ignore")
        complete_text = self._complete_text(state, text)
        if not complete_text.strip():
            return [], []
        if path.suffix.lower() == ".csv":
            return self._parse_csv_increment(path, state, complete_text)
        events, observations = parse_evt_event_text(
            complete_text,
            path.name,
            start_index=state.record_index,
            include_observations=True,
        )
        state.record_index += len(events)
        return events, observations

    @staticmethod
    def _complete_text(state: TailState, text: str) -> str:
        combined = state.buffer + text
        state.buffer = ""
        if not combined:
            return ""
        if combined.endswith(("\n", "\r")):
            return combined
        lines = combined.splitlines(keepends=True)
        if not lines:
            return ""
        last = lines[-1]
        if last.endswith(("\n", "\r")):
            return combined
        state.buffer = last
        return "".join(lines[:-1])

    @staticmethod
    def _parse_csv_increment(path: Path, state: TailState, text: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        if state.csv_header is None:
            reader = csv.DictReader(io.StringIO(text))
            if reader.fieldnames:
                state.csv_header = [str(item) for item in reader.fieldnames]
            rows = list(reader)
        else:
            reader = csv.DictReader(io.StringIO(text), fieldnames=state.csv_header)
            rows = [
                row
                for row in reader
                if [str(row.get(field, "")) for field in state.csv_header] != state.csv_header
            ]
        events, observations = parse_csv_event_rows(
            rows,
            path.name,
            start_index=state.record_index,
            include_observations=True,
        )
        state.record_index += len(events)
        return events, observations

    def _update_entities(self, observations: list[dict[str, Any]]) -> None:
        for observation in sorted(observations, key=lambda item: float(item.get("time", 0.0))):
            for entity in observation.get("entities", []):
                entity_id = str(entity.get("id") or "")
                lat = entity.get("lat")
                lon = entity.get("lon")
                if not entity_id or lat is None or lon is None:
                    continue
                point = {
                    "lat": float(lat),
                    "lon": float(lon),
                    "alt_m": float(entity.get("alt_m", 0.0)),
                }
                track = self._track_points.setdefault(entity_id, [])
                if not track or abs(track[-1]["lat"] - point["lat"]) > 1e-7 or abs(track[-1]["lon"] - point["lon"]) > 1e-7:
                    track.append(point)
                entity_copy = dict(entity)
                entity_copy["route"] = track[-120:]
                entity_copy["source"] = entity_copy.get("source") or "afsim-realtime-bridge"
                self._entities[entity_id] = entity_copy

    def _tracks(self) -> list[dict[str, Any]]:
        tracks: list[dict[str, Any]] = []
        for entity_id, points in sorted(self._track_points.items()):
            entity = self._entities.get(entity_id, {})
            tracks.append(
                {
                    "id": f"rt_trk_{entity_id}",
                    "platform_id": entity_id,
                    "side": entity.get("side", "neutral"),
                    "kind": "realtime",
                    "points": points[-400:],
                    "history": points[-400:],
                    "predicted": [],
                    "quality": 0.8,
                    "layer_ids": ["dynamic.history_tracks"],
                }
            )
        return tracks

    def _sim_time(self, events: list[dict[str, Any]], observations: list[dict[str, Any]]) -> float:
        values = [float(item.get("time", 0.0)) for item in events]
        values.extend(float(item.get("time", 0.0)) for item in observations)
        if values:
            return round(max(values), 3)
        if self._last_sim_time is not None:
            return self._last_sim_time
        return 0.0


class RealtimeBridgeManager:
    def __init__(self) -> None:
        self._bridges: dict[str, RealtimeBridge] = {}
        self._lock = threading.Lock()

    def register_run(
        self,
        run_id: str,
        *,
        working_dir: str | Path | None = None,
        run_dir: str | Path | None = None,
        output_dir: str | Path | None = None,
    ) -> RealtimeBridge:
        bridge = self.get_or_create(run_id)
        bridge.add_paths(working_dir=working_dir, run_dir=run_dir, output_dir=output_dir)
        return bridge

    def get_or_create(self, run_id: str) -> RealtimeBridge:
        with self._lock:
            bridge = self._bridges.get(run_id)
            if not bridge:
                bridge = RealtimeBridge(run_id)
                self._bridges[run_id] = bridge
            return bridge

    def poll(self, run_id: str) -> dict[str, Any] | None:
        return self.get_or_create(run_id).poll()

    async def stream(self, run_id: str, *, interval_seconds: float = 0.75):
        sent_unavailable = False
        while True:
            frame = self.poll(run_id)
            if frame:
                if frame.get("status") == "unavailable":
                    if not sent_unavailable:
                        yield frame
                        sent_unavailable = True
                else:
                    sent_unavailable = False
                    yield frame
            await asyncio.sleep(max(0.1, interval_seconds))


def unavailable_frame(run_id: str, *, message: str) -> dict[str, Any]:
    return {
        "schema_version": REALTIME_SCHEMA_VERSION,
        "type": "afsim_realtime",
        "status": "unavailable",
        "run_id": run_id,
        "frame_id": 0,
        "source": "afsim-realtime-bridge",
        "authoritative": False,
        "sim_time": None,
        "entity_count": 0,
        "event_count": 0,
        "entities": [],
        "events": [],
        "tracks": [],
        "detections": [],
        "message": message,
        "generated_at": time.time(),
    }


realtime_bridge_manager = RealtimeBridgeManager()
