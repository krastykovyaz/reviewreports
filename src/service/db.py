"""Minimal jobs/reports store for the reviewreports service.

A dedicated table + aiosqlite wrapper, rather than the repo's existing
DatabaseService/ECP layer, since that layer is built for agent-directed
CRUD actions and jobs here are created/updated only by the service itself.
"""

import json
import os
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Optional

import aiosqlite


class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    subject TEXT NOT NULL,
    lang TEXT NOT NULL DEFAULT 'en',
    status TEXT NOT NULL,
    report_json TEXT,
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


class JobStore:
    def __init__(self, db_path: str):
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)

    async def init(self) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(_SCHEMA)
            # CREATE TABLE IF NOT EXISTS doesn't retrofit new columns onto a
            # database file created before they existed, so migrate explicitly.
            async with db.execute("PRAGMA table_info(jobs)") as cursor:
                existing_columns = {row[1] async for row in cursor}
            if "lang" not in existing_columns:
                await db.execute("ALTER TABLE jobs ADD COLUMN lang TEXT NOT NULL DEFAULT 'en'")
            await db.commit()

    async def create_job(self, kind: str, subject: str, lang: str = "en") -> str:
        job_id = uuid.uuid4().hex
        now = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT INTO jobs (id, kind, subject, lang, status, report_json, error, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, NULL, NULL, ?, ?)",
                (job_id, kind, subject, lang, JobStatus.PENDING.value, now, now),
            )
            await db.commit()
        return job_id

    async def mark_running(self, job_id: str) -> None:
        await self._update(job_id, status=JobStatus.RUNNING.value)

    async def mark_done(self, job_id: str, report: Dict[str, Any]) -> None:
        await self._update(job_id, status=JobStatus.DONE.value, report_json=json.dumps(report))

    async def mark_failed(self, job_id: str, error: str) -> None:
        await self._update(job_id, status=JobStatus.FAILED.value, error=error)

    async def _update(self, job_id: str, **fields: Any) -> None:
        fields["updated_at"] = datetime.now(timezone.utc).isoformat()
        set_clause = ", ".join(f"{k} = ?" for k in fields)
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(f"UPDATE jobs SET {set_clause} WHERE id = ?", (*fields.values(), job_id))
            await db.commit()

    async def get_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)) as cursor:
                row = await cursor.fetchone()
                return dict(row) if row else None
