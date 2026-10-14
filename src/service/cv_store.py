"""Storage for submitted CV profiles.

Deliberately not JobStore: CV rendering (HTML/PDF/Markdown, all in
src/cv/render.py) is synchronous and fast (no LLM call, no browser scan)
unlike the review kinds' minutes-long scans, so there's no job status to
track — just the profile itself, persisted so a template/format can be
picked or changed after submission without resending the whole profile.
"""

import hmac
import json
import os
import secrets
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
    created_at TEXT NOT NULL,
    edit_token TEXT,
    photo BLOB,
    original BLOB,
    original_name TEXT,
    original_kind TEXT,
    updated_at TEXT
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class CVStore:
    def __init__(self, db_path: str):
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)

    async def init(self) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(_SCHEMA)
            # A database created before edit_token existed won't get it from
            # CREATE TABLE IF NOT EXISTS - add it if missing so an upgrade in
            # place doesn't crash on the first read/write instead of forcing
            # a fresh db file.
            cols = {row[1] async for row in await db.execute("PRAGMA table_info(cv_profiles)")}
            if "edit_token" not in cols:
                await db.execute("ALTER TABLE cv_profiles ADD COLUMN edit_token TEXT")
            if "photo" not in cols:
                await db.execute("ALTER TABLE cv_profiles ADD COLUMN photo BLOB")
            for col, typ in (("original", "BLOB"), ("original_name", "TEXT"), ("original_kind", "TEXT"), ("updated_at", "TEXT")):
                if col not in cols:
                    await db.execute(f"ALTER TABLE cv_profiles ADD COLUMN {col} {typ}")
            # "Last changed" is what retention counts from; rows from before it existed start at their creation time.
            await db.execute("UPDATE cv_profiles SET updated_at = created_at WHERE updated_at IS NULL")
            await db.commit()

    async def create(self, profile: CVProfile, template: str) -> tuple:
        """Returns (cv_id, edit_token). The token is generated once here and
        never resurfaced by any GET - only the creation response includes
        it - so a CV's share link (which is just its id) grants read/render
        access but not edit access; that requires whoever created it to have
        kept the token."""
        cv_id = uuid.uuid4().hex
        edit_token = secrets.token_hex(16)
        now = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT INTO cv_profiles (id, profile_json, template, created_at, edit_token, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                (cv_id, profile.model_dump_json(), template, now, edit_token, now),
            )
            await db.commit()
        return cv_id, edit_token

    def verify_edit_token(self, row: Dict[str, Any], token: Optional[str]) -> bool:
        """Constant-time comparison so a wrong guess can't be timed to learn
        how many leading characters matched. A row with no stored token (a
        CV created before this existed, or through a path that never issues
        one) can never be edited - fails closed rather than treating a
        missing token as "no check needed"."""
        stored = row.get("edit_token")
        return bool(stored and token and hmac.compare_digest(stored, token))

    async def get(self, cv_id: str) -> Optional[Dict[str, Any]]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            # Not SELECT *: every render/GET goes through here, and none of them want the photo's
            # bytes. has_photo says whether there is one; get_photo() fetches it when needed.
            async with db.execute(
                "SELECT id, profile_json, template, created_at, updated_at, edit_token, (photo IS NOT NULL) AS has_photo, "
                "(original IS NOT NULL) AS has_original, original_name, original_kind "
                "FROM cv_profiles WHERE id = ?", (cv_id,),
            ) as cursor:
                row = await cursor.fetchone()
                return dict(row) if row else None

    async def get_profile(self, cv_id: str) -> Optional[CVProfile]:
        row = await self.get(cv_id)
        return CVProfile.model_validate(json.loads(row["profile_json"])) if row else None

    async def get_photo(self, cv_id: str) -> Optional[bytes]:
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("SELECT photo FROM cv_profiles WHERE id = ?", (cv_id,)) as cursor:
                row = await cursor.fetchone()
                return bytes(row[0]) if row and row[0] else None

    async def set_photo(self, cv_id: str, jpeg: Optional[bytes]) -> bool:
        """Replaces the CV's photo (None removes it). Template, profile and token are untouched."""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("UPDATE cv_profiles SET photo = ?, updated_at = ? WHERE id = ?", (jpeg, _now(), cv_id))
            await db.commit()
            return cursor.rowcount > 0

    async def purge_expired(self, cutoff_iso: str) -> list:
        """Deletes every CV not changed since cutoff (profile, photo and original go with the row) and
        returns their ids so callers can remove anything derived from them. Viewing doesn't count as a
        change: a share link that is only ever opened still expires."""
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("SELECT id FROM cv_profiles WHERE updated_at < ?", (cutoff_iso,)) as cursor:
                ids = [r[0] async for r in cursor]
            if ids:
                await db.execute("DELETE FROM cv_profiles WHERE updated_at < ?", (cutoff_iso,))
                await db.commit()
                await db.execute("VACUUM")  # hand the freed photo/original pages back to the disk
        return ids

    async def delete(self, cv_id: str) -> bool:
        """Removes the whole CV: profile, photo and the uploaded original go with the row."""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("DELETE FROM cv_profiles WHERE id = ?", (cv_id,))
            await db.commit()
            return cursor.rowcount > 0

    async def get_original(self, cv_id: str) -> Optional[Dict[str, Any]]:
        """The file this CV was built from (bytes, name, kind 'pdf'|'docx'), if one was uploaded."""
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("SELECT original, original_name, original_kind FROM cv_profiles WHERE id = ?", (cv_id,)) as cursor:
                row = await cursor.fetchone()
                return {"data": bytes(row[0]), "name": row[1], "kind": row[2]} if row and row[0] else None

    async def set_original(self, cv_id: str, data: Optional[bytes], name: Optional[str] = None, kind: Optional[str] = None) -> bool:
        """Keeps (or, with None, removes) the uploaded original next to the CV built from it."""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "UPDATE cv_profiles SET original = ?, original_name = ?, original_kind = ?, updated_at = ? WHERE id = ?",
                (data, name if data else None, kind if data else None, _now(), cv_id),
            )
            await db.commit()
            return cursor.rowcount > 0

    async def update_profile(self, cv_id: str, profile: CVProfile) -> bool:
        """Overwrites an existing CV's profile in place (its template and
        created_at are untouched) — used by the "describe changes" edit flow,
        which revises a profile without minting a new id/link."""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "UPDATE cv_profiles SET profile_json = ?, updated_at = ? WHERE id = ?",
                (profile.model_dump_json(), _now(), cv_id),
            )
            await db.commit()
            return cursor.rowcount > 0
