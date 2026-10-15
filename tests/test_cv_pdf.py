"""Uploading an existing CV as a PDF.

The PDF is untrusted input: it has to really be a PDF, be within size/page limits, not be
encrypted, and contain real text (a scan has none). Only text comes out of it - it then goes
through the same extraction, storage, edit token and share link as pasted text.
"""
from io import BytesIO

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfReader, PdfWriter

import src.service.app as app_module
from src.cv import pdf_text
from src.cv.pdf_text import PdfError, extract_pdf_text
from src.cv.render import render_cv_pdf
from src.cv.schema import CVProfile
from src.service.cv_store import CVStore
from tests.helpers import _PROFILE, _cv_pdf, _blank_pdf, _encrypted_pdf









# ── extract_pdf_text ──────────────────────────────────────────────

def test_text_is_read_from_a_real_cv_pdf():
    text = extract_pdf_text(_cv_pdf())
    assert "Jordan Reyes" in text and "Northwind Systems" in text and "UC San Diego" in text


@pytest.mark.parametrize("raw", [b"", b"hello", b"<html></html>", b"MZ\x90\x00" + b"\0" * 200])
def test_non_pdf_is_rejected(raw):
    with pytest.raises(PdfError) as e:
        extract_pdf_text(raw)
    assert e.value.code == "not_pdf"


def test_a_pdf_with_no_text_is_reported_as_no_text_not_sent_on_empty():
    with pytest.raises(PdfError) as e:
        extract_pdf_text(_blank_pdf())  # stands in for a scan: pages, but nothing to read
    assert e.value.code == "no_text"


def test_encrypted_pdf_is_rejected():
    with pytest.raises(PdfError) as e:
        extract_pdf_text(_encrypted_pdf())
    assert e.value.code == "encrypted"


def test_a_permission_only_lock_with_an_empty_password_still_opens():
    w = PdfWriter(clone_from=PdfReader(BytesIO(_cv_pdf()))); w.encrypt("", owner_password="owner")
    b = BytesIO(); w.write(b)
    assert "Jordan Reyes" in extract_pdf_text(b.getvalue())


def test_too_many_pages_is_rejected_before_reading_them():
    with pytest.raises(PdfError) as e:
        extract_pdf_text(_blank_pdf(pdf_text.MAX_PAGES + 1))
    assert e.value.code == "too_many_pages"


def test_a_truncated_pdf_is_unreadable_not_a_crash():
    with pytest.raises(PdfError) as e:
        extract_pdf_text(_cv_pdf()[:200])
    assert e.value.code in {"unreadable", "no_text"}


def test_output_is_capped_to_the_same_limit_as_pasted_text(monkeypatch):
    monkeypatch.setattr(pdf_text, "MAX_CHARS", 100)
    assert len(extract_pdf_text(_cv_pdf())) == 100


# ── endpoint ──────────────────────────────────────────────────────

@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "_cv_store", CVStore(db_path=str(tmp_path / "cv.db")))
    seen = {}

    async def fake_extract(text, model_name):
        seen["text"] = text
        return _PROFILE

    monkeypatch.setattr(app_module, "extract_cv_profile", fake_extract)
    with TestClient(app_module.app) as c:
        c.seen = seen
        yield c


def _post(client, data, name="cv.pdf", **form):
    return client.post("/cv/from-file", files={"file": (name, data, "application/pdf")}, data=form)


def test_uploading_a_pdf_creates_a_cv_like_pasted_text_does(client):
    r = _post(client, _cv_pdf())
    assert r.status_code == 200
    d = r.json()
    assert d["profile"]["name"] == "Jordan Reyes" and d["template"] == "modern" and d["edit_token"]
    assert "Northwind Systems" in d["source_text"] and d["source_text"] == client.seen["text"]


def test_the_created_cv_is_viewable_shareable_and_in_every_format_by_its_id(client):
    d = _post(client, _cv_pdf()).json()
    assert client.get(f"/cv/{d['id']}").status_code == 200
    assert "Jordan Reyes" in client.get(f"/cv/{d['id']}.html", params={"template": "classic"}).text
    assert client.get(f"/cv/{d['id']}.pdf", params={"template": "compact"}).content.startswith(b"%PDF")


def test_the_created_cv_can_be_corrected_with_its_edit_token(client, monkeypatch):
    d = _post(client, _cv_pdf()).json()

    async def fake_edit(current, instructions, model_name):
        return current.model_copy(update={"skills": [*current.skills, "Rust"]})

    monkeypatch.setattr(app_module, "edit_cv_profile", fake_edit)
    ok = client.post(f"/cv/{d['id']}/edit", json={"instructions": "add Rust", "edit_token": d["edit_token"]})
    assert ok.status_code == 200 and "Rust" in ok.json()["profile"]["skills"]
    assert client.post(f"/cv/{d['id']}/edit", json={"instructions": "x", "edit_token": "wrong"}).status_code == 403


def test_template_choice_is_honoured_and_validated(client):
    assert _post(client, _cv_pdf(), template="compact").json()["template"] == "compact"
    assert _post(client, _cv_pdf(), template="nope").status_code == 400


@pytest.mark.parametrize("data,status,code", [
    (b"not a pdf at all", 400, "doc_unsupported"),
    (b"<html><script>alert(1)</script></html>", 400, "doc_unsupported"),
    (_blank_pdf(), 422, "doc_no_text"),
])
def test_bad_uploads_get_a_specific_code(client, data, status, code):
    r = _post(client, data)
    assert r.status_code == status and r.json()["detail"]["code"] == code


def test_encrypted_is_400_and_too_many_pages_is_400(client):
    assert _post(client, _encrypted_pdf()).json()["detail"]["code"] == "doc_encrypted"
    r = _post(client, _blank_pdf(pdf_text.MAX_PAGES + 1))
    assert r.status_code == 400 and r.json()["detail"]["code"] == "doc_too_many_pages"


def test_oversize_is_413_and_never_reaches_the_model(client, monkeypatch):
    monkeypatch.setattr(app_module, "CV_PDF_MAX_BYTES", 1000)
    r = _post(client, _cv_pdf())
    assert r.status_code == 413 and r.json()["detail"]["code"] == "doc_too_large" and "text" not in client.seen


def test_nothing_reaches_the_model_when_the_pdf_is_unusable(client):
    _post(client, _blank_pdf()); _post(client, b"junk")
    assert "text" not in client.seen


def test_a_model_that_finds_no_cv_in_the_text_is_422(client, monkeypatch):
    async def none(text, model_name): return None
    monkeypatch.setattr(app_module, "extract_cv_profile", none)
    r = _post(client, _cv_pdf())
    assert r.status_code == 422 and r.json()["detail"]["code"] == "doc_no_cv"


def test_gates_are_released_after_failures(client):
    for _ in range(5):
        _post(client, b"junk"); _post(client, _blank_pdf())
    assert not app_module._pdf_gate.locked() and not app_module._llm_gate.locked()
    assert _post(client, _cv_pdf()).status_code == 200


def test_a_busy_server_says_so_instead_of_queueing(client, monkeypatch):
    async def busy(gate): raise app_module.ServerBusy()
    monkeypatch.setattr(app_module, "_acquire_or_reject", busy)
    assert _post(client, _cv_pdf()).status_code == 503
