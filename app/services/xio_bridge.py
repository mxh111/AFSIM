from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.core.config import PROJECT_ROOT


XIO_SESSION_ROOT = PROJECT_ROOT / "runtime" / "xio_sessions"
XIO_FRAME_SCHEMA_VERSION = "afsim-xio-frame.v1"


@dataclass
class XioSession:
    run_id: str
    host: str
    port: int
    session_dir: Path
    started_at: float = field(default_factory=time.time)
    stopped_at: float | None = None
    last_error: str | None = None


def _safe_run_id(run_id: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", run_id).strip("._")
    return safe or "unknown_run"


class XioBridge:
    def __init__(self, session_root: str | Path = XIO_SESSION_ROOT) -> None:
        self.session_root = Path(session_root)
        self._sessions: dict[str, XioSession] = {}
        self._lock = threading.Lock()

    def start(self, run_id: str, host: str, port: int) -> dict[str, Any]:
        session_dir = self.session_root / _safe_run_id(run_id)
        session_dir.mkdir(parents=True, exist_ok=True)
        session = XioSession(run_id=run_id, host=host, port=int(port), session_dir=session_dir)
        with self._lock:
            self._sessions[run_id] = session
        return self._session_status(session, status="unsupported")

    def stop(self, run_id: str) -> dict[str, Any]:
        with self._lock:
            session = self._sessions.get(run_id)
        if not session:
            return {
                "run_id": run_id,
                "status": "stopped",
                "active": False,
                "message": "XIO bridge session is not running.",
            }
        session.stopped_at = time.time()
        return self._session_status(session, status="stopped")

    def frames(self, run_id: str) -> list[dict[str, Any]]:
        with self._lock:
            session = self._sessions.get(run_id)
        host = session.host if session else None
        port = session.port if session else None
        return [
            {
                "schema_version": XIO_FRAME_SCHEMA_VERSION,
                "type": "xio",
                "status": "unsupported",
                "run_id": run_id,
                "source": "afsim-xio-interface",
                "authoritative": False,
                "host": host,
                "port": port,
                "entities": [],
                "events": [],
                "tracks": [],
                "detections": [],
                "message": "XIO bridge endpoint is reserved, but XIO message format has not been confirmed or parsed.",
            }
        ]

    def status(self) -> dict[str, Any]:
        with self._lock:
            sessions = [self._session_status(session, status="unsupported") for session in self._sessions.values()]
        return {
            "status": "unsupported",
            "available": False,
            "source": "afsim-xio-interface",
            "transport": "udp-or-tcp-pending-format",
            "parser": "not-implemented",
            "authoritative": False,
            "session_root": str(self.session_root),
            "message": "XIO bridge API is reserved; no authoritative XIO frames are emitted until the data format is confirmed.",
            "sessions": sessions,
        }

    @staticmethod
    def _session_status(session: XioSession, *, status: str) -> dict[str, Any]:
        return {
            "run_id": session.run_id,
            "status": status,
            "active": False,
            "host": session.host,
            "port": session.port,
            "session_dir": str(session.session_dir),
            "started_at": session.started_at,
            "stopped_at": session.stopped_at,
            "last_error": session.last_error,
            "message": "XIO session is recorded for future integration; parser/listener is unsupported in this phase.",
        }


xio_bridge = XioBridge()
