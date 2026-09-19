import aiosqlite
import pytest

from src.service.db import JobStore


@pytest.mark.asyncio
async def test_init_migrates_pre_lang_database(tmp_path):
    """A jobs.db created before the `lang` column existed must not crash on
    init — CREATE TABLE IF NOT EXISTS silently no-ops on an existing table,
    so the missing column has to be added explicitly (this is exactly what
    broke against the real dev database)."""
    db_path = str(tmp_path / "jobs.db")
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            """CREATE TABLE jobs (
                id TEXT PRIMARY KEY, kind TEXT NOT NULL, subject TEXT NOT NULL, status TEXT NOT NULL,
                report_json TEXT, error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            )"""
        )
        await db.commit()

    store = JobStore(db_path=db_path)
    await store.init()  # must not raise

    job_id = await store.create_job(kind="website_audit", subject="https://example.com", lang="ru")
    job = await store.get_job(job_id)
    assert job["lang"] == "ru"


@pytest.mark.asyncio
async def test_init_is_idempotent_on_fresh_database(tmp_path):
    store = JobStore(db_path=str(tmp_path / "jobs.db"))
    await store.init()
    await store.init()  # second call must not error (column already present)
    job_id = await store.create_job(kind="website_audit", subject="https://example.com")
    job = await store.get_job(job_id)
    assert job["lang"] == "en"
