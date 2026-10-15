"""Cost limits on the public CV endpoints: a request that needs no token and no LLM call must not be able to
freeze the service (WeasyPrint laying out a huge profile used to run synchronously on the event loop), the model
can't be chosen by the caller, and the legacy form route is behind the same gate and size cap as the JSON API.
"""
import asyncio
import threading
import time

import pytest
from fastapi.testclient import TestClient

import src.service.app as app_module
from src.cv.schema import MAX_PROFILE_CHARS
from src.service.cv_store import CVStore
from tests.test_cv_pdf import _PROFILE

_PAYLOAD = _PROFILE.model_dump()


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "_cv_store", CVStore(db_path=str(tmp_path / "cv.db")))
    with TestClient(app_module.app) as c:
        yield c


# ── bounded profile size ──────────────────────────────────────────

def test_a_profile_over_the_total_cap_is_rejected_at_creation(client):
    big = {**_PAYLOAD, "experience": [
        {"title": "T", "organization": "O", "start": "2020", "end": "2021", "bullets": ["x" * 5000] * 10} for _ in range(20)]}  # 1M chars
    r = client.post("/cv", json=big)
    assert r.status_code == 422 and "too long" in r.text


def test_the_cap_is_far_above_a_real_cv(client):
    assert MAX_PROFILE_CHARS >= 30 * len(_PROFILE.model_dump_json())  # ~2KB real profile vs 60KB cap
    assert client.post("/cv", json=_PAYLOAD).status_code == 200


def test_just_under_the_cap_is_accepted(client):
    near = {**_PAYLOAD, "summary": "s" * 5000, "experience": [
        {"title": "T", "organization": "O", "start": "2020", "end": "2021", "bullets": ["x" * 4000] * 10} for _ in range(1)]}
    assert client.post("/cv", json=near).status_code == 200


# ── renders never block the event loop ────────────────────────────

def test_a_slow_pdf_render_does_not_freeze_other_requests(client, monkeypatch):
    cv = client.post("/cv", json=_PAYLOAD).json()["id"]
    started, release = threading.Event(), threading.Event()

    def slow_render(profile, template, photo=None):
        started.set(); release.wait(10)
        return b"%PDF-1.7 slow"

    monkeypatch.setattr(app_module, "render_cv_pdf", slow_render)
    results = {}

    def get_pdf():
        results["pdf"] = client.get(f"/cv/{cv}.pdf").status_code

    t = threading.Thread(target=get_pdf); t.start()
    assert started.wait(5)
    t0 = time.time()
    other = client.get(f"/cv/{cv}")  # a cheap request while the render is running
    assert other.status_code == 200 and time.time() - t0 < 2  # was blocked for the whole render before
    release.set(); t.join(10)
    assert results["pdf"] == 200


def test_a_render_that_exceeds_the_timeout_is_a_503_and_the_gate_is_freed_when_the_thread_ends(client, monkeypatch):
    cv = client.post("/cv", json=_PAYLOAD).json()["id"]
    monkeypatch.setattr(app_module, "_RENDER_TIMEOUT_SEC", 0.3)
    release = threading.Event()

    def stuck(profile, template, photo=None):
        release.wait(10)
        return b"%PDF-x"

    monkeypatch.setattr(app_module, "render_cv_pdf", stuck)
    assert client.get(f"/cv/{cv}.pdf").status_code == 503
    release.set(); time.sleep(0.3)
    assert app_module._render_gate._value == 2  # released only once the thread finished, then fully available again


def test_renders_are_limited_to_two_at_a_time(client, monkeypatch):
    cv = client.post("/cv", json=_PAYLOAD).json()["id"]
    live, peak, lock, release = [0], [0], threading.Lock(), threading.Event()

    def counted(profile, template, photo=None):
        with lock:
            live[0] += 1; peak[0] = max(peak[0], live[0])
        release.wait(5)
        with lock:
            live[0] -= 1
        return b"%PDF-x"

    monkeypatch.setattr(app_module, "render_cv_pdf", counted)
    ts = [threading.Thread(target=lambda: client.get(f"/cv/{cv}.pdf")) for _ in range(5)]
    [t.start() for t in ts]; time.sleep(1.0)
    assert peak[0] <= 2
    release.set(); [t.join(10) for t in ts]


def test_a_busy_render_pool_answers_503_not_a_hang(client, monkeypatch):
    cv = client.post("/cv", json=_PAYLOAD).json()["id"]
    gate = asyncio.Semaphore(0)  # nothing available
    monkeypatch.setattr(app_module, "_render_gate", gate)

    async def quick_timeout_wait(coro, timeout):  # don't actually wait 10s in a test
        coro.close()
        raise asyncio.TimeoutError

    monkeypatch.setattr(app_module.asyncio, "wait_for", quick_timeout_wait)
    assert client.get(f"/cv/{cv}.pdf").status_code == 503


# ── the legacy form route ─────────────────────────────────────────

def test_the_legacy_form_route_has_the_same_size_cap(client, monkeypatch):
    async def never(text, model_name): raise AssertionError("must not be called")
    monkeypatch.setattr(app_module, "extract_cv_profile", never)
    assert client.post("/cv-builder/from-text", data={"text": "x" * 20_001}).status_code == 422


def test_the_legacy_form_route_goes_through_the_llm_gate_and_releases_it(client, monkeypatch):
    async def ok(text, model_name): return _PROFILE
    monkeypatch.setattr(app_module, "extract_cv_profile", ok)
    assert client.post("/cv-builder/from-text", data={"text": "bio"}).status_code == 200
    assert not app_module._llm_gate.locked()

    async def boom(text, model_name): raise RuntimeError("model down")
    monkeypatch.setattr(app_module, "extract_cv_profile", boom)
    with pytest.raises(RuntimeError):
        client.post("/cv-builder/from-text", data={"text": "bio"})
    assert not app_module._llm_gate.locked()  # released even when the call blows up


def test_the_legacy_form_route_refuses_when_the_gate_is_full(client, monkeypatch):
    async def busy(gate): raise app_module.ServerBusy()
    monkeypatch.setattr(app_module, "_acquire_or_reject", busy)
    assert client.post("/cv-builder/from-text", data={"text": "bio"}).status_code == 503


# ── the model can't be chosen on any public route ─────────────────

@pytest.mark.parametrize("path,kw", [
    ("/cv/from-text", {"json": {"text": "Jordan Reyes jordan@example.com", "model_name": "deepseek/deepseek-reasoner"}}),
    ("/reports", {"json": {"url": "https://example.com", "kind": "website_audit", "model_name": "deepseek/deepseek-reasoner"}}),
    ("/cv/from-file", {"files": {"file": ("a.pdf", b"%PDF-1.4")}, "data": {"model_name": "deepseek/deepseek-reasoner"}}),
])
def test_an_unlisted_model_is_refused_on_every_public_route(client, monkeypatch, path, kw):
    async def never(*a, **k): raise AssertionError("the model must not be called")
    monkeypatch.setattr(app_module, "extract_cv_profile", never)
    assert client.post(path, **kw).status_code == 400


def test_the_default_model_and_no_model_are_always_fine():
    assert app_module._check_model(None) is None and app_module._check_model("  ") is None
    assert app_module._check_model(app_module._DEFAULT_CV_EXTRACTION_MODEL) == app_module._DEFAULT_CV_EXTRACTION_MODEL
