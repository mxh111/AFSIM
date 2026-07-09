from __future__ import annotations

import json
import re
import socket
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.core.config import PROJECT_ROOT
from app.services.xio_bridge import xio_bridge


DIS_PACKET_ROOT = PROJECT_ROOT / "runtime" / "dis_packets"
DIS_FRAME_SCHEMA_VERSION = "afsim-dis-frame.v1"
DIS_ENTITY_STATE_PDU_TYPE = 1


@dataclass
class DisSession:
    run_id: str
    bind_host: str
    bind_port: int
    socket: socket.socket
    packet_dir: Path
    stop_event: threading.Event = field(default_factory=threading.Event)
    frames: deque[dict[str, Any]] = field(default_factory=lambda: deque(maxlen=400))
    packet_count: int = 0
    started_at: float = field(default_factory=time.time)
    stopped_at: float | None = None
    last_error: str | None = None
    thread: threading.Thread | None = None


def _safe_run_id(run_id: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", run_id).strip("._")
    return safe or "unknown_run"


def _dis_header(packet: bytes) -> dict[str, Any] | None:
    if len(packet) < 12:
        return None
    length = int.from_bytes(packet[8:10], byteorder="big", signed=False)
    return {
        "protocol_version": packet[0],
        "exercise_id": packet[1],
        "pdu_type": packet[2],
        "protocol_family": packet[3],
        "timestamp": int.from_bytes(packet[4:8], byteorder="big", signed=False),
        "length": length,
        "padding": int.from_bytes(packet[10:12], byteorder="big", signed=False),
    }


def parse_entity_state_pdu(packet: bytes) -> dict[str, Any] | None:
    header = _dis_header(packet)
    if not header or header["pdu_type"] != DIS_ENTITY_STATE_PDU_TYPE:
        return None
    return {
        "status": "unsupported",
        "reason": "Entity State PDU parsing is reserved for the DIS bridge implementation phase.",
        "header": header,
        "entities": [],
        "tracks": [],
        "detections": [],
    }


class DisBridge:
    def __init__(self, packet_root: str | Path = DIS_PACKET_ROOT) -> None:
        self.packet_root = Path(packet_root)
        self._sessions: dict[str, DisSession] = {}
        self._lock = threading.Lock()

    def start(self, run_id: str, bind_host: str, port: int) -> dict[str, Any]:
        with self._lock:
            existing = self._sessions.get(run_id)
            if existing and not existing.stop_event.is_set():
                return self._session_status(existing)

            packet_dir = self.packet_root / _safe_run_id(run_id)
            packet_dir.mkdir(parents=True, exist_ok=True)
            udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            udp_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            udp_socket.bind((bind_host, int(port)))
            udp_socket.settimeout(0.2)
            bound_host, bound_port = udp_socket.getsockname()[:2]
            session = DisSession(
                run_id=run_id,
                bind_host=str(bound_host),
                bind_port=int(bound_port),
                socket=udp_socket,
                packet_dir=packet_dir,
            )
            thread = threading.Thread(target=self._listen, args=(session,), name=f"dis-bridge-{_safe_run_id(run_id)}", daemon=True)
            session.thread = thread
            self._sessions[run_id] = session
            thread.start()
            return self._session_status(session)

    def stop(self, run_id: str) -> dict[str, Any]:
        with self._lock:
            session = self._sessions.get(run_id)
        if not session:
            return {"run_id": run_id, "status": "stopped", "active": False, "message": "DIS bridge session is not running."}

        session.stop_event.set()
        try:
            session.socket.close()
        except OSError:
            pass
        if session.thread and session.thread.is_alive():
            session.thread.join(timeout=2.0)
        session.stopped_at = time.time()
        return self._session_status(session)

    def frames(self, run_id: str) -> list[dict[str, Any]]:
        with self._lock:
            session = self._sessions.get(run_id)
            if not session:
                return [
                    {
                        "schema_version": DIS_FRAME_SCHEMA_VERSION,
                        "type": "dis",
                        "status": "unsupported",
                        "run_id": run_id,
                        "source": "dis-udp",
                        "authoritative": False,
                        "message": "DIS bridge session is not running; no Entity State PDU frames are available.",
                        "entities": [],
                        "tracks": [],
                        "detections": [],
                    }
                ]
            return [dict(frame) for frame in session.frames]

    def status(self) -> dict[str, Any]:
        with self._lock:
            sessions = [self._session_status(session) for session in self._sessions.values()]
        return {
            "status": "unsupported",
            "available": False,
            "transport": "udp-listener",
            "capture_available": True,
            "parser": "entity-state-pdu-skeleton",
            "packet_root": str(self.packet_root),
            "message": "DIS UDP packet capture is available, but Entity State PDU decoding is not fully implemented.",
            "sessions": sessions,
        }

    def _listen(self, session: DisSession) -> None:
        while not session.stop_event.is_set():
            try:
                packet, address = session.socket.recvfrom(65535)
            except socket.timeout:
                continue
            except OSError as exc:
                if not session.stop_event.is_set():
                    session.last_error = str(exc)
                break
            self._record_packet(session, packet, address)

    def _record_packet(self, session: DisSession, packet: bytes, address: tuple[str, int]) -> None:
        with self._lock:
            session.packet_count += 1
            sequence = session.packet_count
        packet_path = session.packet_dir / f"packet_{sequence:06d}_{time.time_ns()}.bin"
        packet_path.write_bytes(packet)
        parsed = parse_entity_state_pdu(packet)
        header = _dis_header(packet)
        if parsed is None:
            parsed = {
                "status": "unsupported",
                "reason": "Only Entity State PDU capture is recognized in this skeleton.",
                "header": header,
                "entities": [],
                "tracks": [],
                "detections": [],
            }
        frame = {
            "schema_version": DIS_FRAME_SCHEMA_VERSION,
            "type": "dis",
            "status": parsed.get("status", "unsupported"),
            "run_id": session.run_id,
            "frame_id": sequence,
            "source": "dis-udp",
            "authoritative": False,
            "received_at": time.time(),
            "remote": {"host": address[0], "port": address[1]},
            "packet_path": str(packet_path),
            "packet_size": len(packet),
            "header": parsed.get("header", header),
            "message": parsed.get("reason", "DIS packet captured; parser is not fully implemented."),
            "entities": parsed.get("entities", []),
            "tracks": parsed.get("tracks", []),
            "detections": parsed.get("detections", []),
        }
        metadata_path = packet_path.with_suffix(".json")
        metadata_path.write_text(json.dumps(frame, ensure_ascii=False, indent=2), encoding="utf-8")
        with self._lock:
            session.frames.append(frame)

    @staticmethod
    def _session_status(session: DisSession) -> dict[str, Any]:
        active = not session.stop_event.is_set() and session.thread is not None and session.thread.is_alive()
        return {
            "run_id": session.run_id,
            "status": "running" if active else "stopped",
            "active": active,
            "bind_host": session.bind_host,
            "bind_port": session.bind_port,
            "packet_dir": str(session.packet_dir),
            "packet_count": session.packet_count,
            "started_at": session.started_at,
            "stopped_at": session.stopped_at,
            "last_error": session.last_error,
        }


def bridge_capabilities() -> dict[str, Any]:
    return {
        "schema_version": "afsim-bridge-capabilities.v1",
        "event_output": {
            "status": "available",
            "available": True,
            "source": "afsim-event-output",
            "message": "Realtime bridge can tail output/*.evt files produced by event_output.",
        },
        "csv_event_output": {
            "status": "available",
            "available": True,
            "source": "afsim-csv-event-output",
            "message": "Realtime bridge can tail output/*.csv files produced by csv_event_output.",
        },
        "dis": dis_bridge.status(),
        "xio": xio_bridge.status(),
        "notes": [
            "No third-party DIS parser is required for this skeleton.",
            "Future candidates can be evaluated separately, such as Open-DIS compatible Python bindings, before adding dependencies.",
        ],
    }


dis_bridge = DisBridge()
