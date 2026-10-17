"""Edit tokens are stored hashed: a copy of the database no longer contains credentials that can edit or delete a CV."""
import aiosqlite
import pytest
from fastapi.testclient import TestClient

import src.service.app as app_module
from src.service.cv_store import CVStore, hash_token
from tests.helpers import _PROFILE


async def _raw_token(path, cv_id):
    async with aiosqlite.connect(path) as db:
        async with db.execute("SELECT edit_token FROM cv_profiles WHERE id = ?", (cv_id,)) as cur:
            return (await cur.fetchone())[0]


@pytest.mark.asyncio
async def test_the_database_holds_a_hash_not_the_token(tmp_path):
    store = CVStore(db_path=str(tmp_path / "cv.db")); await store.init()
    cv_id, token = await store.create(_PROFILE, "modern")
    stored = await _raw_token(store.db_path, cv_id)
    assert stored != token and token not in stored and len(stored) == 64 and stored == hash_token(token)


@pytest.mark.asyncio
async def test_the_issued_token_still_verifies_and_nothing_else_does(tmp_path):
    store = CVStore(db_path=str(tmp_path / "cv.db")); await store.init()
    cv_id, token = await store.create(_PROFILE, "modern")
    row = await store.get(cv_id)
    assert store.verify_edit_token(row, token)
    assert not store.verify_edit_token(row, "wrong") and not store.verify_edit_token(row, "") and not store.verify_edit_token(row, None)


@pytest.mark.asyncio
async def test_what_is_in_the_database_cannot_be_used_as_the_token(tmp_path):
    """The whole point: reading the file must not hand over edit rights."""
    store = CVStore(db_path=str(tmp_path / "cv.db")); await store.init()
    cv_id, _ = await store.create(_PROFILE, "modern")
    stolen = await _raw_token(store.db_path, cv_id)
    assert not store.verify_edit_token(await store.get(cv_id), stolen)


@pytest.mark.asyncio
async def test_a_row_without_a_token_can_never_be_edited(tmp_path):
    store = CVStore(db_path=str(tmp_path / "cv.db")); await store.init()
    cv_id, token = await store.create(_PROFILE, "modern")
    async with aiosqlite.connect(store.db_path) as db:
        await db.execute("UPDATE cv_profiles SET edit_token = NULL WHERE id = ?", (cv_id,)); await db.commit()
    assert not store.verify_edit_token(await store.get(cv_id), token)


@pytest.mark.asyncio
async def test_existing_plaintext_tokens_are_hashed_on_startup_and_keep_working(tmp_path):
    path = str(tmp_path / "cv.db")
    store = CVStore(db_path=path); await store.init()
    cv_id, _ = await store.create(_PROFILE, "modern")
    legacy = "0123456789abcdef0123456789abcdef"      # what the old code stored: the token itself
    async with aiosqlite.connect(path) as db:
        await db.execute("UPDATE cv_profiles SET edit_token = ? WHERE id = ?", (legacy, cv_id)); await db.commit()
    store2 = CVStore(db_path=path); await store2.init()   # a restart
    assert await _raw_token(path, cv_id) == hash_token(legacy)
    assert store2.verify_edit_token(await store2.get(cv_id), legacy)       # the copy in the person's browser still works
    assert not store2.verify_edit_token(await store2.get(cv_id), hash_token(legacy))


@pytest.mark.asyncio
async def test_the_migration_is_idempotent(tmp_path):
    path = str(tmp_path / "cv.db")
    store = CVStore(db_path=path); await store.init()
    cv_id, token = await store.create(_PROFILE, "modern")
    for _ in range(3):
        await CVStore(db_path=path).init()
    assert await _raw_token(path, cv_id) == hash_token(token)   # not hashed again and again
    assert store.verify_edit_token(await store.get(cv_id), token)


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "_cv_store", CVStore(db_path=str(tmp_path / "cv.db")))

    async def fake_extract(text, model_name): return _PROFILE
    monkeypatch.setattr(app_module, "extract_cv_profile", fake_extract)
    with TestClient(app_module.app) as c:
        yield c


def test_the_api_flow_is_unchanged_for_the_person_holding_the_token(client):
    d = client.post("/cv", json=_PROFILE.model_dump()).json()
    assert client.post(f"/cv/{d['id']}/edit", json={"instructions": "x", "edit_token": "wrong"}).status_code == 403
    assert client.delete(f"/cv/{d['id']}/photo", headers={"X-Edit-Token": d["edit_token"]}).status_code == 200
    assert client.delete(f"/cv/{d['id']}", headers={"X-Edit-Token": d["edit_token"]}).status_code == 200


def test_no_response_ever_contains_the_token_or_its_hash(client):
    d = client.post("/cv", json=_PROFILE.model_dump()).json()
    for url in (f"/cv/{d['id']}", f"/cv/{d['id']}.html", f"/cv/{d['id']}.md", f"/cv-builder/{d['id']}"):
        body = client.get(url).text
        assert d["edit_token"] not in body and hash_token(d["edit_token"]) not in body, url
