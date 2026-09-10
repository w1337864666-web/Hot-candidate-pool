from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .config import get_database_path


SCHEMA = """
CREATE TABLE IF NOT EXISTS chat_sessions (
    session_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS chat_messages (
    message_id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    plan_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS chat_tasks (
    task_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    message_id INTEGER,
    status TEXT NOT NULL,
    plan_json TEXT NOT NULL,
    fallback_notice TEXT NOT NULL DEFAULT '',
    progress INTEGER NOT NULL DEFAULT 0,
    progress_label TEXT NOT NULL DEFAULT '',
    run_id INTEGER,
    result_json TEXT NOT NULL DEFAULT '{}',
    error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    started_at TEXT NOT NULL DEFAULT '',
    completed_at TEXT NOT NULL DEFAULT ''
);
"""


class ChatStore:
    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path is not None else get_database_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path, timeout=30)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA busy_timeout = 30000")
        self.connection.executescript(SCHEMA)
        self.connection.commit()

    def __enter__(self) -> "ChatStore":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def create_session(self, session_id: str | None = None) -> str:
        session_id = session_id or f"session-{uuid.uuid4().hex[:16]}"
        now = datetime.now(timezone.utc).isoformat()
        self.connection.execute(
            "INSERT OR IGNORE INTO chat_sessions(session_id, created_at, updated_at) VALUES (?, ?, ?)",
            (session_id, now, now),
        )
        self.connection.execute(
            "UPDATE chat_sessions SET updated_at = ? WHERE session_id = ?",
            (now, session_id),
        )
        self.connection.commit()
        return session_id

    def save_message(
        self,
        session_id: str,
        role: str,
        content: str,
        plan: dict[str, object] | None = None,
    ) -> int:
        if role not in {"user", "assistant"}:
            raise ValueError("unsupported chat message role")
        now = datetime.now(timezone.utc).isoformat()
        cursor = self.connection.execute(
            """INSERT INTO chat_messages(session_id, role, content, plan_json, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (session_id, role, content.strip(), json.dumps(plan or {}, ensure_ascii=False), now),
        )
        self.connection.execute(
            "UPDATE chat_sessions SET updated_at = ? WHERE session_id = ?",
            (now, session_id),
        )
        self.connection.commit()
        return int(cursor.lastrowid)

    def list_messages(self, session_id: str, limit: int = 20) -> list[dict[str, object]]:
        rows = self.connection.execute(
            """SELECT message_id, session_id, role, content, plan_json, created_at
               FROM chat_messages WHERE session_id = ? ORDER BY message_id DESC LIMIT ?""",
            (session_id, max(1, min(100, limit))),
        ).fetchall()
        messages = []
        for row in reversed(rows):
            item = dict(row)
            item["plan"] = json.loads(item.pop("plan_json") or "{}")
            messages.append(item)
        return messages

    def create_task(
        self,
        session_id: str,
        message_id: int,
        plan: dict[str, object],
        fallback_notice: str = "",
    ) -> str:
        task_id = f"task-{uuid.uuid4().hex[:16]}"
        now = datetime.now(timezone.utc).isoformat()
        self.connection.execute(
            """INSERT INTO chat_tasks(
                 task_id, session_id, message_id, status, plan_json, fallback_notice,
                 progress, progress_label, created_at
               ) VALUES (?, ?, ?, 'awaiting_confirmation', ?, ?, 0, ?, ?)""",
            (
                task_id, session_id, message_id,
                json.dumps(plan, ensure_ascii=False), fallback_notice,
                "等待确认执行", now,
            ),
        )
        self.connection.commit()
        return task_id

    def get_task(self, task_id: str) -> dict[str, object] | None:
        row = self.connection.execute(
            "SELECT * FROM chat_tasks WHERE task_id = ?", (task_id,)
        ).fetchone()
        if not row:
            return None
        item = dict(row)
        item["plan"] = json.loads(item.pop("plan_json") or "{}")
        item["result"] = json.loads(item.pop("result_json") or "{}")
        return item

    def claim_task(self, task_id: str) -> bool:
        now = datetime.now(timezone.utc).isoformat()
        cursor = self.connection.execute(
            """UPDATE chat_tasks SET status = 'queued', progress = 5,
                 progress_label = '已确认，等待执行', started_at = ?
               WHERE task_id = ? AND status = 'awaiting_confirmation'""",
            (now, task_id),
        )
        self.connection.commit()
        return cursor.rowcount == 1

    def update_task(
        self,
        task_id: str,
        *,
        status: str | None = None,
        progress: int | None = None,
        progress_label: str | None = None,
        run_id: int | None = None,
        result: dict[str, object] | None = None,
        error: str | None = None,
        started_at: str | None = None,
        completed_at: str | None = None,
    ) -> None:
        assignments: list[str] = []
        values: list[object] = []
        if status is not None:
            assignments.append("status = ?")
            values.append(status)
        if progress is not None:
            assignments.append("progress = ?")
            values.append(max(0, min(100, int(progress))))
        if progress_label is not None:
            assignments.append("progress_label = ?")
            values.append(progress_label[:200])
        if run_id is not None:
            assignments.append("run_id = ?")
            values.append(run_id)
        if result is not None:
            assignments.append("result_json = ?")
            values.append(json.dumps(result, ensure_ascii=False))
        if error is not None:
            assignments.append("error = ?")
            values.append(error[:1000])
        if started_at is not None:
            assignments.append("started_at = ?")
            values.append(started_at)
        if completed_at is not None:
            assignments.append("completed_at = ?")
            values.append(completed_at)
        if not assignments:
            return
        values.append(task_id)
        self.connection.execute(
            f"UPDATE chat_tasks SET {', '.join(assignments)} WHERE task_id = ?",
            tuple(values),
        )
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()
