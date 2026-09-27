import pytest

from src.cv.schema import Contact, CVProfile
from src.service.cv_store import CVStore


def _sample_profile() -> CVProfile:
    return CVProfile(name="Jordan Reyes", role="Engineer", contact=Contact(email="jordan@example.com"))


@pytest.mark.asyncio
async def test_create_and_get_roundtrips_profile(tmp_path):
    store = CVStore(db_path=str(tmp_path / "cv.db"))
    await store.init()

    cv_id = await store.create(_sample_profile(), template="modern")
    row = await store.get(cv_id)
    assert row["template"] == "modern"

    profile = await store.get_profile(cv_id)
    assert profile.name == "Jordan Reyes"
    assert profile.contact.email == "jordan@example.com"


@pytest.mark.asyncio
async def test_get_unknown_id_returns_none(tmp_path):
    store = CVStore(db_path=str(tmp_path / "cv.db"))
    await store.init()
    assert await store.get("does-not-exist") is None
    assert await store.get_profile("does-not-exist") is None


@pytest.mark.asyncio
async def test_init_is_idempotent(tmp_path):
    store = CVStore(db_path=str(tmp_path / "cv.db"))
    await store.init()
    await store.init()
    cv_id = await store.create(_sample_profile(), template="classic")
    assert (await store.get(cv_id))["template"] == "classic"
