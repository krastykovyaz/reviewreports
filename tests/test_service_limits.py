"""Limits that must hold when things go wrong: a worker thread that outlives its timeout still occupies its slot, one audit
can't hold a slot forever, a purge only VACUUMs when it is worth it, and database waits are long enough for it.
"""
import asyncio
import threading
import time

import aiosqlite
import pytest
from fastapi.testclient import TestClient

import src.service.app as app_module
from src.service import cv_store as cv_store_module, db as db_module
from src.service.cv_store import CVStore
from src.service.db import JobStatus, JobStore
from src.service.sqlite_utils import BUSY_TIMEOUT_SEC, maybe_vacuum
from tests.helpers import _PROFILE, _docx, _CV_LINES


# ── a slot is held until the thread really ends ───────────────────

@pytest.mark.asyncio
async def test_the_gate_is_released_when_the_thread_finishes_not_when_the_wait_times_out():
    gate = asyncio.Semaphore(1)
    await gate.acquire()
    release = threading.Event()

    def stuck():
        release.wait(5)
        return "done"

    with pytest.raises(asyncio.TimeoutError):
        await app_module._run_releasing_on_finish(gate, 0.2, stuck)
    assert gate.locked()                      # the caller gave up, but the work is still running: the slot is still taken
    release.set()
    for _ in range(50):
        await asyncio.sleep(0.02)
        if not gate.locked(): break
    assert not gate.locked()                  # and is freed once the thread has actually ended


@pytest.mark.asyncio
async def test_the_gate_is_released_when_the_work_raises():
    gate = asyncio.Semaphore(1)
    await gate.acquire()

    def boom():
        raise ValueError("bad file")

    with pytest.raises(ValueError):
        await app_module._run_releasing_on_finish(gate, 5, boom)
    await asyncio.sleep(0.05)
    assert not gate.locked()


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "_cv_store", CVStore(db_path=str(tmp_path / "cv.db")))

    async def fake_extract(text, model_name): return _PROFILE
    monkeypatch.setattr(app_module, "extract_cv_profile", fake_extract)
    with TestClient(app_module.app) as c:
        yield c


def test_a_slow_document_parse_times_out_but_keeps_its_slot_until_it_ends(client, monkeypatch):
    release = threading.Event()
    monkeypatch.setattr(app_module, "_DOC_TIMEOUT_SEC", 0.3)
    monkeypatch.setattr(app_module, "extract_document_text", lambda raw: (release.wait(5), "x" * 200)[1])
    before = app_module._pdf_gate._value
    r = client.post("/cv/from-file", files={"file": ("a.docx", _docx(_CV_LINES), "application/octet-stream")})
    assert r.status_code == 400 and r.json()["detail"]["code"] == "doc_unreadable"
    assert app_module._pdf_gate._value == before - 1      # still occupied by the running parse
    release.set(); time.sleep(0.4)
    assert app_module._pdf_gate._value == before          # freed when it finished


def test_many_stuck_parses_cannot_exceed_the_gate(client, monkeypatch):
    """Each timed-out upload used to free its slot at once, so a stream of bad files stacked up threads without bound."""
    release = threading.Event()
    monkeypatch.setattr(app_module, "_DOC_TIMEOUT_SEC", 0.2)
    monkeypatch.setattr(app_module, "extract_document_text", lambda raw: (release.wait(5), "x" * 200)[1])
    codes = [client.post("/cv/from-file", files={"file": ("a.docx", _docx(_CV_LINES), "application/octet-stream")}).status_code for _ in range(5)]
    release.set(); time.sleep(0.4)
    assert codes[:2] == [400, 400] and set(codes[2:]) == {503}   # two slots; the rest are refused while they're held


def test_the_stored_word_preview_is_bounded_like_a_render(client, monkeypatch):
    d = client.post("/cv/from-file", files={"file": ("a.docx", _docx(_CV_LINES), "application/octet-stream")}).json()
    release = threading.Event()
    monkeypatch.setattr(app_module, "_RENDER_TIMEOUT_SEC", 0.2)
    monkeypatch.setattr(app_module, "docx_to_html", lambda raw: (release.wait(5), "<html></html>")[1])
    r = client.get(f"/cv/{d['id']}/original.html")
    release.set()
    assert r.status_code == 503  # busy/too slow is not "could not preview this file" (422)


# ── one audit can't hold a slot forever ───────────────────────────

@pytest.mark.asyncio
async def test_an_audit_that_hangs_is_stopped_and_frees_its_slot(tmp_path, monkeypatch):
    store = JobStore(db_path=str(tmp_path / "jobs.db")); await store.init()
    monkeypatch.setattr(app_module, "_store", store)
    monkeypatch.setattr(app_module, "_JOB_TIMEOUT_SEC", 0.3)
    cancelled = []

    async def hangs(**kw):
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            cancelled.append(True)   # the browser session's cleanup runs on cancellation
            raise

    monkeypatch.setattr(app_module, "generate_report", hangs)
    job = await store.create_job("website_audit", "https://example.com")
    before = app_module._audit_gate._value
    await app_module._run_job(job, "website_audit", "https://example.com", None, "en")
    j = await store.get_job(job)
    assert j["status"] == JobStatus.FAILED.value and "took longer" in j["error"] and cancelled
    assert app_module._audit_gate._value == before


@pytest.mark.asyncio
async def test_a_normal_audit_is_unaffected_by_the_timeout(tmp_path, monkeypatch):
    store = JobStore(db_path=str(tmp_path / "jobs.db")); await store.init()
    monkeypatch.setattr(app_module, "_store", store)

    class _Done:
        def model_dump(self, mode=None): return {"summary": "ok"}

    async def quick(**kw):
        return _Done()

    monkeypatch.setattr(app_module, "generate_report", quick)
    job = await store.create_job("website_audit", "https://example.com")
    await app_module._run_job(job, "website_audit", "https://example.com", None, "en")
    assert (await store.get_job(job))["status"] == JobStatus.DONE.value


# ── VACUUM and waits ──────────────────────────────────────────────

async def _db_with_free_pages(path, free_mb):
    async with aiosqlite.connect(path) as db:
        await db.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, b BLOB)")
        for i in range(free_mb * 5):
            await db.execute("INSERT INTO t (b) VALUES (?)", (b"x" * 200_000,))
        await db.commit()
        await db.execute("DELETE FROM t"); await db.commit()


@pytest.mark.asyncio
async def test_vacuum_runs_only_when_enough_space_is_free(tmp_path):
    import os
    big, small = str(tmp_path / "big.db"), str(tmp_path / "small.db")
    await _db_with_free_pages(big, 20); await _db_with_free_pages(small, 2)
    size_big, size_small = os.path.getsize(big), os.path.getsize(small)
    async with aiosqlite.connect(big) as db:
        assert await maybe_vacuum(db, min_free_bytes=10 * 1024 * 1024) is True
    async with aiosqlite.connect(small) as db:
        assert await maybe_vacuum(db, min_free_bytes=10 * 1024 * 1024) is False
    assert os.path.getsize(big) < size_big / 4 and os.path.getsize(small) == size_small   # shrunk only where it was worth it


@pytest.mark.asyncio
async def test_a_purge_that_frees_little_does_not_rewrite_the_database(tmp_path, monkeypatch):
    store = CVStore(db_path=str(tmp_path / "cv.db")); await store.init()
    cv_id, _ = await store.create(_PROFILE, "modern")
    ran = []
    real = cv_store_module.maybe_vacuum

    async def spy(db, *a, **k):
        ran.append(await real(db, *a, **k)); return ran[-1]

    monkeypatch.setattr(cv_store_module, "maybe_vacuum", spy)
    assert await store.purge_expired("9999-01-01T00:00:00+00:00") == [cv_id]
    assert ran == [False]  # one tiny CV deleted: nothing worth vacuuming for


@pytest.mark.asyncio
@pytest.mark.parametrize("module,make", [(cv_store_module, lambda p: CVStore(db_path=p)), (db_module, lambda p: JobStore(db_path=p))])
async def test_every_connection_waits_long_enough_for_a_busy_database(tmp_path, monkeypatch, module, make):
    seen = []
    real = aiosqlite.connect
    monkeypatch.setattr(module.aiosqlite, "connect", lambda *a, **k: (seen.append(k.get("timeout")), real(*a, **k))[1])
    store = make(str(tmp_path / "x.db")); await store.init()
    if isinstance(store, CVStore):
        i, _ = await store.create(_PROFILE, "modern"); await store.get(i); await store.get_photo(i); await store.purge_expired("0")
    else:
        j = await store.create_job("website_audit", "u"); await store.get_job(j); await store.purge_expired("0")
    assert seen and all(t == BUSY_TIMEOUT_SEC for t in seen) and BUSY_TIMEOUT_SEC >= 30
