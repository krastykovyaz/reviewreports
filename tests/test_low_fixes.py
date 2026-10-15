"""Low-severity hardening in the CV/report service."""
import asyncio
import threading
import time
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import src.service.app as app_module
from src.cv.preview_card import RasterError
from src.service.cv_store import CVStore
from src.service.db import JobStore
from tests.helpers import _PROFILE, _CV_LINES, _cv_pdf, _docx, run


@pytest.fixture()
def client(tmp_path, monkeypatch):
    async def fake_extract(text, model_name): return _PROFILE
    monkeypatch.setattr(app_module, "extract_cv_profile", fake_extract)
    with TestClient(app_module.app) as c:
        yield c


def _up(client, data, name="cv.pdf"):
    return client.post("/cv/from-file", files={"file": (name, data, "application/octet-stream")}).json()


# ── preview locks ─────────────────────────────────────────────────

def test_the_lock_table_empties_itself_and_never_grows(client, monkeypatch):
    d = _up(client, _cv_pdf())
    for _ in range(3):
        client.get(f"/cv/{d['id']}/preview.jpg", params={"template": "classic"})
    import gc; gc.collect()
    assert len(app_module._preview_locks) == 0


def test_two_requests_for_the_same_uncached_image_render_it_once(client, monkeypatch):
    d = _up(client, _cv_pdf())
    renders = []
    real = app_module.rasterize_first_page

    def counted(pdf, *a, **k):
        renders.append(1); time.sleep(0.3); return real(pdf, *a, **k)

    monkeypatch.setattr(app_module, "rasterize_first_page", counted)
    out = []
    ts = [threading.Thread(target=lambda: out.append(client.get(f"/cv/{d['id']}/preview.jpg", params={"template": "compact"}).status_code)) for _ in range(2)]
    [t.start() for t in ts]; [t.join(20) for t in ts]
    assert out == [200, 200] and len(renders) == 1


def test_a_failing_holder_does_not_let_a_second_render_run_beside_a_waiter(client, monkeypatch):
    d = _up(client, _cv_pdf())
    live, peak, lock, n = [0], [0], threading.Lock(), [0]
    real = app_module.rasterize_first_page

    def flaky(pdf, *a, **k):
        with lock:
            n[0] += 1; first = n[0] == 1; live[0] += 1; peak[0] = max(peak[0], live[0])
        try:
            time.sleep(0.3)
            if first:
                raise RasterError("boom")
            return real(pdf, *a, **k)
        finally:
            with lock: live[0] -= 1

    monkeypatch.setattr(app_module, "rasterize_first_page", flaky)
    codes = []
    ts = [threading.Thread(target=lambda: codes.append(client.get(f"/cv/{d['id']}/preview.jpg", params={"template": "classic"}).status_code)) for _ in range(3)]
    [t.start() for t in ts]; [t.join(30) for t in ts]
    assert peak[0] == 1 and 200 in codes   # never two at once; one failed, the rest recovered


# ── original file ─────────────────────────────────────────────────

def test_removing_the_original_removes_its_share_image(client, tmp_path):
    d = _up(client, _cv_pdf())
    client.get(f"/cv/{d['id']}/original-preview.jpg")
    assert list(app_module._PREVIEW_DIR.glob(f"{d['id']}.original.*.jpg"))
    client.delete(f"/cv/{d['id']}/original", headers={"X-Edit-Token": d["edit_token"]})
    assert not list(app_module._PREVIEW_DIR.glob(f"{d['id']}.original.*.jpg"))


def test_the_pdf_original_page_does_not_load_the_file(client, monkeypatch):
    d = _up(client, _cv_pdf())

    async def never(cv_id): raise AssertionError("the 5 MB blob was loaded just to read its name")
    monkeypatch.setattr(app_module._cv_store, "get_original", never)
    r = client.get(f"/cv/{d['id']}/original.html")
    assert r.status_code == 200 and "cv.pdf" in r.text


def test_the_word_original_page_still_loads_the_file_for_its_preview(client):
    d = _up(client, _docx(_CV_LINES), "r.docx")
    assert "Northwind Systems" in client.get(f"/cv/{d['id']}/original.html").text


# ── retention marker ──────────────────────────────────────────────

def test_a_corrupt_marker_is_repaired_instead_of_restarting_the_grace_period_forever(tmp_path, monkeypatch):
    marker = tmp_path / ".retention_started"
    monkeypatch.setattr(app_module, "_retention_marker", lambda: marker)
    marker.write_text("not a date")
    run(app_module._cv_store.init()); run(app_module._store.init())
    now = datetime.now(timezone.utc)
    assert run(app_module._purge_expired(now))["skipped"] is True
    assert datetime.fromisoformat(marker.read_text().strip()) == now          # repaired with a real start
    later = now + timedelta(days=8)
    assert run(app_module._purge_expired(later))["skipped"] is False           # ... so the period does end


def test_an_empty_marker_is_repaired_too(tmp_path, monkeypatch):
    marker = tmp_path / ".retention_started"
    monkeypatch.setattr(app_module, "_retention_marker", lambda: marker)
    marker.write_text("")
    run(app_module._cv_store.init()); run(app_module._store.init())
    now = datetime.now(timezone.utc)
    run(app_module._purge_expired(now))
    assert run(app_module._purge_expired(now + timedelta(days=8)))["skipped"] is False


# ── the web form ──────────────────────────────────────────────────

_FORM = {"name": "Jordan Reyes", "email": "jordan@example.com", "role": "Engineer", "summary": "s"}


def test_a_form_field_over_its_limit_is_a_422_not_a_server_error(client):
    r = client.post("/cv-builder", data={**_FORM, "name": "x" * 201}, follow_redirects=False)
    assert r.status_code == 422 and "name" in r.text
    r = client.post("/cv-builder", data={**_FORM, "exp_title": ["T"], "exp_organization": ["O" * 201], "exp_start": ["2020"]}, follow_redirects=False)
    assert r.status_code == 422
    r = client.post("/cv-builder", data={**_FORM, "edu_degree": ["D" * 201], "edu_institution": ["I"]}, follow_redirects=False)
    assert r.status_code == 422


def test_a_valid_form_still_redirects_to_the_result(client):
    r = client.post("/cv-builder", data=_FORM, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/cv-builder/")


# ── public API reference ──────────────────────────────────────────

def test_the_api_reference_is_not_public_by_default(client):
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(path).status_code == 404, path


@pytest.mark.parametrize("value,expected", [("", False), ("0", False), ("no", False), ("1", True), ("true", True), ("YES", True)])
def test_docs_are_opt_in(monkeypatch, value, expected):
    monkeypatch.setenv("ENABLE_DOCS", value)
    assert app_module._docs_enabled() is expected


# ── report page ───────────────────────────────────────────────────

def _report_page(client, tmp_path, monkeypatch):
    store = JobStore(db_path=str(tmp_path / "jobs.db")); run(store.init())
    monkeypatch.setattr(app_module, "_store", store)

    from tests.fixtures.tsech_report import build_tsech_report

    async def make():
        j = await store.create_job("website_audit", "https://example.com")
        await store.mark_done(j, build_tsech_report().model_dump(mode="json")); return j
    return client.get(f"/view/{run(make())}").text


def test_the_third_party_script_is_pinned_and_integrity_checked(client, tmp_path, monkeypatch):
    page = _report_page(client, tmp_path, monkeypatch)
    import re
    tag = re.search(r"<script[^>]+marked[^>]+>", page).group(0)
    assert "marked@15.0.12" in tag and 'integrity="sha384-' in tag and 'crossorigin="anonymous"' in tag
    assert "/marked/marked.min.js" not in page   # the moving 'latest' URL is gone


def test_the_report_frame_is_sandboxed_and_only_the_pdf_tab_lifts_it(client, tmp_path, monkeypatch):
    page = _report_page(client, tmp_path, monkeypatch)
    assert 'id="reportFrame"' in page and 'sandbox="allow-popups allow-popups-to-escape-sandbox"' in page
    assert "frame.removeAttribute('sandbox')" in page and "frame.setAttribute('sandbox'" in page
    assert "allow-same-origin" not in page and "allow-scripts" not in page.split('id="reportFrame"')[1][:200]
