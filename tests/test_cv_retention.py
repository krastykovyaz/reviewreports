"""Retention: everything stored for a CV or an audit report is deleted RETENTION_DAYS (7) after it last
changed, with its photo, uploaded original and preview images; and a new deployment doesn't wipe data that
already existed - its clock starts at first run.
"""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import src.service.app as app_module
from src.service.cv_store import CVStore
from src.service.db import JobStore
from tests.test_cv_original import _up, _cv_pdf, _docx, _CV_LINES  # noqa: F401
from tests.test_cv_pdf import _PROFILE
from tests.test_cv_photo import _jpeg

DAY = timedelta(days=1)


def _iso(dt):
    return dt.astimezone(timezone.utc).isoformat()


@pytest.fixture()
def stores(tmp_path, monkeypatch):
    cv, jobs = CVStore(db_path=str(tmp_path / "cv.db")), JobStore(db_path=str(tmp_path / "jobs.db"))
    monkeypatch.setattr(app_module, "_cv_store", cv)
    monkeypatch.setattr(app_module, "_store", jobs)
    monkeypatch.setattr(app_module, "_UPLOAD_DIR", str(tmp_path / "uploads"))
    (tmp_path / "uploads").mkdir()
    return cv, jobs


async def _age(store, cv_id, when):
    import aiosqlite
    async with aiosqlite.connect(store.db_path) as db:
        await db.execute("UPDATE cv_profiles SET updated_at = ? WHERE id = ?", (_iso(when), cv_id)); await db.commit()


# ── the store ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_purge_deletes_only_what_is_older_than_the_cutoff_with_its_photo_and_original(stores):
    cv, _ = stores; await cv.init()
    old, new = (await cv.create(_PROFILE, "modern"))[0], (await cv.create(_PROFILE, "modern"))[0]
    await cv.set_photo(old, b"jpeg"); await cv.set_original(old, b"%PDF-x", "a.pdf", "pdf")
    await _age(cv, old, datetime.now(timezone.utc) - 8 * DAY)
    assert await cv.purge_expired(_iso(datetime.now(timezone.utc) - 7 * DAY)) == [old]
    assert await cv.get(old) is None and await cv.get_photo(old) is None and await cv.get_original(old) is None
    assert await cv.get(new) is not None


@pytest.mark.asyncio
async def test_every_change_restarts_the_clock_but_reading_does_not(stores):
    cv, _ = stores; await cv.init()
    i = (await cv.create(_PROFILE, "modern"))[0]
    for change in (lambda: cv.set_photo(i, b"x"), lambda: cv.set_original(i, b"%PDF-x", "a.pdf", "pdf"), lambda: cv.update_profile(i, _PROFILE)):
        await _age(cv, i, datetime.now(timezone.utc) - 6 * DAY)
        await change()
        assert datetime.fromisoformat((await cv.get(i))["updated_at"]) > datetime.now(timezone.utc) - timedelta(minutes=1)
    await _age(cv, i, datetime.now(timezone.utc) - 6 * DAY)
    before = (await cv.get(i))["updated_at"]
    await cv.get(i); await cv.get_profile(i); await cv.get_photo(i); await cv.get_original(i)
    assert (await cv.get(i))["updated_at"] == before  # a link that is only ever opened still expires


@pytest.mark.asyncio
async def test_rows_from_before_updated_at_existed_start_at_their_creation_time(tmp_path):
    import aiosqlite
    path = str(tmp_path / "old.db")
    async with aiosqlite.connect(path) as db:
        await db.execute("CREATE TABLE cv_profiles (id TEXT PRIMARY KEY, profile_json TEXT NOT NULL, template TEXT NOT NULL, created_at TEXT NOT NULL, edit_token TEXT)")
        await db.execute("INSERT INTO cv_profiles VALUES ('old1', ?, 'modern', '2020-01-01T00:00:00+00:00', 'tok')", (_PROFILE.model_dump_json(),)); await db.commit()
    store = CVStore(db_path=path); await store.init()
    assert (await store.get("old1"))["updated_at"] == "2020-01-01T00:00:00+00:00"


@pytest.mark.asyncio
async def test_job_purge_returns_subjects_and_keeps_recent_jobs(stores):
    _, jobs = stores; await jobs.init()
    a, b = await jobs.create_job("website_audit", "https://a.example"), await jobs.create_job("website_audit", "https://b.example")
    import aiosqlite
    async with aiosqlite.connect(jobs.db_path) as db:
        await db.execute("UPDATE jobs SET updated_at = ? WHERE id = ?", (_iso(datetime.now(timezone.utc) - 9 * DAY), a)); await db.commit()
    assert await jobs.purge_expired(_iso(datetime.now(timezone.utc) - 7 * DAY)) == [(a, "https://a.example")]
    assert await jobs.get_job(a) is None and await jobs.get_job(b) is not None


# ── the service-level purge ───────────────────────────────────────

@pytest.mark.asyncio
async def test_nothing_is_deleted_during_the_first_retention_period_after_deploy(stores, tmp_path):
    cv, jobs = stores; await cv.init(); await jobs.init()
    i = (await cv.create(_PROFILE, "modern"))[0]
    await _age(cv, i, datetime.now(timezone.utc) - 30 * DAY)  # long-lived data that already existed
    now = datetime.now(timezone.utc)
    r = await app_module._purge_expired(now)
    assert r["skipped"] is True and await cv.get(i) is not None
    assert (tmp_path / ".retention_started").exists()
    assert (await app_module._purge_expired(now + 6 * DAY))["skipped"] is True and await cv.get(i) is not None
    r = await app_module._purge_expired(now + 7 * DAY + timedelta(minutes=1))
    assert r["skipped"] is False and r["cvs"] == 1 and await cv.get(i) is None


@pytest.mark.asyncio
async def test_a_purge_removes_previews_reports_and_only_files_inside_the_uploads_dir(stores, tmp_path):
    cv, jobs = stores; await cv.init(); await jobs.init()
    (tmp_path / ".retention_started").write_text(_iso(datetime.now(timezone.utc) - 10 * DAY))
    i = (await cv.create(_PROFILE, "modern"))[0]; await _age(cv, i, datetime.now(timezone.utc) - 8 * DAY)
    previews = tmp_path / "previews"; previews.mkdir()
    (previews / f"{i}.modern.abc.jpg").write_bytes(b"x"); (previews / f"{i}.original.abc.jpg").write_bytes(b"x")
    inside, outside = tmp_path / "uploads" / "u1_cv.pdf", tmp_path / "precious.txt"
    inside.write_bytes(b"x"); outside.write_text("keep me")
    j1, j2 = await jobs.create_job("cv_review", str(inside)), await jobs.create_job("cv_review", str(outside))
    import aiosqlite
    async with aiosqlite.connect(jobs.db_path) as db:
        await db.execute("UPDATE jobs SET updated_at = ?", (_iso(datetime.now(timezone.utc) - 8 * DAY),)); await db.commit()
    r = await app_module._purge_expired()
    assert r["cvs"] == 1 and r["reports"] == 2
    assert not list(previews.glob("*.jpg")) and not inside.exists()
    assert outside.exists() and outside.read_text() == "keep me"  # a subject pointing outside uploads is never touched


@pytest.mark.asyncio
async def test_orphaned_preview_images_are_swept_even_during_the_grace_period(stores, tmp_path):
    cv, jobs = stores; await cv.init(); await jobs.init()
    keep = (await cv.create(_PROFILE, "modern"))[0]
    previews = tmp_path / "previews"; previews.mkdir()
    (previews / f"{keep}.modern.k.jpg").write_bytes(b"x"); (previews / "deadbeef.modern.k.jpg").write_bytes(b"x")
    await app_module._purge_expired()
    assert (previews / f"{keep}.modern.k.jpg").exists() and not (previews / "deadbeef.modern.k.jpg").exists()


def test_the_period_is_configurable_and_defaults_to_seven_days(monkeypatch):
    assert app_module._retention_days() == 7
    monkeypatch.setenv("RETENTION_DAYS", "3"); assert app_module._retention_days() == 3
    monkeypatch.setenv("RETENTION_DAYS", "junk"); assert app_module._retention_days() == 7


def test_the_background_loop_is_off_under_tests():
    import os
    assert os.environ["DISABLE_RETENTION_LOOP"] == "1"


# ── what the API tells the client ─────────────────────────────────

@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "_cv_store", CVStore(db_path=str(tmp_path / "cv2.db")))

    async def fake_extract(text, model_name):
        return _PROFILE
    monkeypatch.setattr(app_module, "extract_cv_profile", fake_extract)
    with TestClient(app_module.app) as c:
        yield c


def _days_left(iso):
    return (datetime.fromisoformat(iso) - datetime.now(timezone.utc)).total_seconds() / 86400


def test_responses_say_when_the_cv_will_be_deleted(client):
    d = _up(client, _cv_pdf())
    assert 6.99 < _days_left(d["expires_at"]) < 7.01
    assert 6.99 < _days_left(client.get(f"/cv/{d['id']}").json()["expires_at"]) < 7.01
    t = client.post("/cv/from-text", json={"text": "Jordan Reyes, engineer, jordan@example.com"}).json()
    assert 6.99 < _days_left(t["expires_at"]) < 7.01


def test_a_change_pushes_the_expiry_out_again(client, monkeypatch):
    d = _up(client, _cv_pdf())
    import asyncio
    asyncio.new_event_loop().run_until_complete(_age(app_module._cv_store, d["id"], datetime.now(timezone.utc) - 5 * DAY))
    assert 1.9 < _days_left(client.get(f"/cv/{d['id']}").json()["expires_at"]) < 2.1
    r = client.post(f"/cv/{d['id']}/photo", files={"file": ("p.jpg", _jpeg(), "image/jpeg")}, data={"edit_token": d["edit_token"]}).json()
    assert 6.99 < _days_left(r["expires_at"]) < 7.01
    assert 6.99 < _days_left(client.get(f"/cv/{d['id']}").json()["expires_at"]) < 7.01
