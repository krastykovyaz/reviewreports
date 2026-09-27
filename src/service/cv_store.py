"""Storage for submitted CV profiles.

Deliberately not JobStore: CV rendering (HTML/PDF/Markdown, all in
src/cv/render.py) is synchronous and fast (no LLM call, no browser scan)
unlike the review kinds' minutes-long scans, so there's no job status to
track — just the profile itself, persisted so a template/format can be
picked or changed after submission without resending the whole profile.
"""

import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import aiosqlite

from src.cv.schema import CVProfile

_SCHEMA = """
CREATE TABLE IF NOT EXISTS cv_profiles (
    id TEXT PRIMARY KEY,
    profile_json TEXT NOT NULL,
    template TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


class CVStore:
    def __init__(self, db_path: str):
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)

    async def init(self) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(_SCHEMA)
            await db.commit()

    async def create(self, profile: CVProfile, template: str) -> str:
        cv_id = uuid.uuid4().hex
        now = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT INTO cv_profiles (id, profile_json, template, created_at) VALUES (?, ?, ?, ?)",
                (cv_id, profile.model_dump_json(), template, now),
            )
            await db.commit()
        return cv_id

    async def get(self, cv_id: str) -> Optional[Dict[str, Any]]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM cv_profiles WHERE id = ?", (cv_id,)) as cursor:
                row = await cursor.fetchone()
                return dict(row) if row else None

    async def get_profile(self, cv_id: str) -> Optional[CVProfile]:
        row = await self.get(cv_id)
        return CVProfile.model_validate(json.loads(row["profile_json"])) if row else None
