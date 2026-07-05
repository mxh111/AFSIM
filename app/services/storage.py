from __future__ import annotations

import json
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from typing import Any

from app.core.config import settings


SCHEMA = """
create table if not exists events (
  id integer primary key autoincrement,
  created_at real not null,
  category text not null,
  payload text not null
);
create table if not exists snapshots (
  id integer primary key autoincrement,
  created_at real not null,
  scenario_id text not null,
  sim_time real not null,
  payload text not null
);
create table if not exists reports (
  id integer primary key autoincrement,
  created_at real not null,
  scenario_id text not null,
  title text not null,
  payload text not null
);
"""


class Storage:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or settings.db_path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path)

    def _init_db(self) -> None:
        with closing(self._connect()) as conn:
            with conn:
                conn.executescript(SCHEMA)

    def add_event(self, category: str, payload: dict[str, Any]) -> None:
        with closing(self._connect()) as conn:
            with conn:
                conn.execute(
                    "insert into events(created_at, category, payload) values (?, ?, ?)",
                    (time.time(), category, json.dumps(payload, ensure_ascii=False)),
                )

    def add_snapshot(self, scenario_id: str, sim_time: float, payload: dict[str, Any]) -> None:
        with closing(self._connect()) as conn:
            with conn:
                conn.execute(
                    "insert into snapshots(created_at, scenario_id, sim_time, payload) values (?, ?, ?, ?)",
                    (time.time(), scenario_id, sim_time, json.dumps(payload, ensure_ascii=False)),
                )

    def add_report(self, scenario_id: str, title: str, payload: dict[str, Any]) -> int:
        with closing(self._connect()) as conn:
            with conn:
                cursor = conn.execute(
                    "insert into reports(created_at, scenario_id, title, payload) values (?, ?, ?, ?)",
                    (time.time(), scenario_id, title, json.dumps(payload, ensure_ascii=False)),
                )
                return int(cursor.lastrowid)

    def vacuum(self) -> None:
        with closing(self._connect()) as conn:
            conn.execute("vacuum")

    def list_events(self, limit: int = 100) -> list[dict[str, Any]]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "select created_at, category, payload from events order by id desc limit ?",
                (limit,),
            ).fetchall()
        return [
            {"created_at": created_at, "category": category, "payload": json.loads(payload)}
            for created_at, category, payload in rows
        ]

    def list_reports(self, limit: int = 20) -> list[dict[str, Any]]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "select id, created_at, scenario_id, title, payload from reports order by id desc limit ?",
                (limit,),
            ).fetchall()
        return [
            {
                "id": row_id,
                "created_at": created_at,
                "scenario_id": scenario_id,
                "title": title,
                "payload": json.loads(payload),
            }
            for row_id, created_at, scenario_id, title, payload in rows
        ]
