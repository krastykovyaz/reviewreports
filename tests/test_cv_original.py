"""The uploaded original (PDF/Word) is kept with the CV built from it so it can be shown as a
fourth card next to our templates.

Because it is the person's own file served back from our origin: it is exactly the bytes
uploaded, a PDF shows inline while a Word file only downloads, the Word preview is escaped by
construction (and locked down with a CSP anyway), names can't smuggle paths/headers, and only the
edit token holder can remove it.
"""
import zipfile
from io import BytesIO

import pytest
from fastapi.testclient import TestClient

import src.service.app as app_module
from src.cv.docx_preview import docx_to_html
from src.service.cv_store import CVStore
from tests.test_cv_docx import _CV_LINES, _NS, _docx
from tests.test_cv_pdf import _PROFILE, _cv_pdf


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "_cv_store", CVStore(db_path=str(tmp_path / "cv.db")))

    async def fake_extract(text, model_name):
        return _PROFILE

    monkeypatch.setattr(app_module, "extract_cv_profile", fake_extract)
    with TestClient(app_module.app) as c:
        yield c


def _up(client, data, name="My CV.pdf"):
    return client.post("/cv/from-file", files={"file": (name, data, "application/octet-stream")}).json()


# ── stored and served ─────────────────────────────────────────────

def test_the_original_is_kept_and_reported(client):
    pdf = _cv_pdf()
    d = _up(client, pdf)
    assert d["has_original"] is True and d["original_kind"] == "pdf" and d["original_name"] == "My CV.pdf"
    g = client.get(f"/cv/{d['id']}").json()
    assert g["has_original"] and g["original_kind"] == "pdf" and g["original_name"] == "My CV.pdf"


def test_a_pdf_original_is_served_byte_for_byte_and_inline(client):
    pdf = _cv_pdf(); d = _up(client, pdf)
    r = client.get(f"/cv/{d['id']}/original")
    assert r.status_code == 200 and r.content == pdf and r.headers["content-type"] == "application/pdf"
    assert r.headers["content-disposition"].startswith("inline") and r.headers["x-content-type-options"] == "nosniff"


def test_download_forces_an_attachment(client):
    d = _up(client, _cv_pdf())
    assert client.get(f"/cv/{d['id']}/original", params={"download": "1"}).headers["content-disposition"].startswith("attachment")


def test_a_word_original_is_only_ever_an_attachment(client):
    raw = _docx(_CV_LINES); d = _up(client, raw, "resume.docx")
    r = client.get(f"/cv/{d['id']}/original")
    assert r.content == raw and r.headers["content-disposition"].startswith("attachment") and "wordprocessingml" in r.headers["content-type"]


def test_a_cv_made_from_text_has_no_original(client):
    d = client.post("/cv/from-text", json={"text": "Jordan Reyes, engineer, jordan@example.com"}).json()
    assert d["has_original"] is False and d["original_kind"] is None
    assert client.get(f"/cv/{d['id']}/original").status_code == 404
    assert client.get(f"/cv/{d['id']}/original.html").status_code == 404


def test_unknown_cv_is_404(client):
    assert client.get("/cv/nope/original").status_code == 404


def test_editing_the_profile_keeps_the_original(client, monkeypatch):
    d = _up(client, _cv_pdf())

    async def fake_edit(current, instructions, model_name):
        return current.model_copy(update={"skills": [*current.skills, "Rust"]})

    monkeypatch.setattr(app_module, "edit_cv_profile", fake_edit)
    r = client.post(f"/cv/{d['id']}/edit", json={"instructions": "add Rust", "edit_token": d["edit_token"]}).json()
    assert r["has_original"] is True and r["original_name"] == "My CV.pdf"


def test_adding_a_photo_keeps_the_original_flag(client):
    from tests.test_cv_photo import _jpeg
    d = _up(client, _cv_pdf())
    r = client.post(f"/cv/{d['id']}/photo", files={"file": ("p.jpg", _jpeg(), "image/jpeg")}, data={"edit_token": d["edit_token"]}).json()
    assert r["has_photo"] is True and r["has_original"] is True


# ── removal ───────────────────────────────────────────────────────

def test_removing_needs_the_edit_token_and_keeps_the_cv(client):
    d = _up(client, _cv_pdf())
    assert client.delete(f"/cv/{d['id']}/original").status_code == 403
    assert client.delete(f"/cv/{d['id']}/original", headers={"X-Edit-Token": "wrong"}).status_code == 403
    assert client.get(f"/cv/{d['id']}/original").status_code == 200
    r = client.delete(f"/cv/{d['id']}/original", headers={"X-Edit-Token": d["edit_token"]})
    assert r.status_code == 200 and r.json()["has_original"] is False
    assert client.get(f"/cv/{d['id']}/original").status_code == 404
    assert client.get(f"/cv/{d['id']}.pdf").content.startswith(b"%PDF")  # the CV itself is untouched


def test_another_cvs_token_cannot_remove_it(client):
    a, b = _up(client, _cv_pdf()), _up(client, _cv_pdf())
    assert client.delete(f"/cv/{b['id']}/original", headers={"X-Edit-Token": a["edit_token"]}).status_code == 403


# ── names ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    ("My CV.pdf", "My CV.pdf"), ("../../etc/passwd.pdf", "passwd.pdf"), ("..\\..\\win\\cv.PDF", "cv.pdf"),
    ("cv\r\nSet-Cookie: x=1.pdf", "cvSet-Cookie x=1.pdf"), ('a<b>"c".pdf', "abc.pdf"), ("", "cv.pdf"), (None, "cv.pdf"),
    ("резюме Иван.pdf", "резюме Иван.pdf"), ("x" * 300 + ".pdf", "x" * 80 + ".pdf"), ("...pdf", "cv.pdf"),
])
def test_filenames_are_made_safe(raw, expected):
    assert app_module._safe_filename(raw, "pdf") == expected


def test_a_hostile_filename_cannot_inject_a_header(client):
    d = _up(client, _cv_pdf(), name="a\r\nX-Evil: 1.pdf")
    r = client.get(f"/cv/{d['id']}/original")
    assert "x-evil" not in {k.lower() for k in r.headers} and "\n" not in r.headers["content-disposition"]


def test_the_extension_follows_the_content_not_the_claimed_name(client):
    d = _up(client, _docx(_CV_LINES), name="resume.pdf")  # a Word file claiming to be a PDF
    assert d["original_kind"] == "docx" and d["original_name"] == "resume.docx"


# ── Word preview ──────────────────────────────────────────────────

def test_the_word_preview_shows_the_text_and_is_locked_down(client):
    d = _up(client, _docx(_CV_LINES), "resume.docx")
    r = client.get(f"/cv/{d['id']}/original.html")
    assert r.status_code == 200 and "Jordan Reyes" in r.text and "Northwind Systems" in r.text
    assert "default-src 'none'" in r.headers["content-security-policy"]


def test_a_pdf_original_gets_a_shareable_page_around_the_viewer(client):
    d = _up(client, _cv_pdf(), "My CV.pdf")
    r = client.get(f"/cv/{d['id']}/original.html")
    assert r.status_code == 200 and f'src="/audit/cv/{d["id"]}/original"' in r.text and "My CV.pdf" in r.text


def test_document_text_is_escaped_never_passed_through_as_markup():
    from xml.sax.saxutils import escape  # as Word itself stores text in <w:t>
    html = docx_to_html(_docx([escape(t) for t in ['<script>alert(1)</script>', '<img src=x onerror=alert(1)>', "Jordan & Sons > rivals"]]))
    assert "<script>" not in html and "<img" not in html and "&lt;script&gt;" in html and "Jordan &amp; Sons &gt; rivals" in html


def _doc_with(body_xml: str) -> bytes:
    b = BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        z.writestr("word/document.xml", f"<w:document {_NS}><w:body>{body_xml}</w:body></w:document>")
    return b.getvalue()


def test_headings_lists_formatting_and_tables_are_rendered():
    body = (
        '<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>Experience</w:t></w:r></w:p>'
        '<w:p><w:r><w:rPr><w:b/></w:rPr><w:t>Bold</w:t></w:r><w:r><w:rPr><w:i/></w:rPr><w:t>Ital</w:t></w:r></w:p>'
        '<w:p><w:pPr><w:numPr><w:ilvl w:val="0"/></w:numPr></w:pPr><w:r><w:t>one</w:t></w:r></w:p>'
        '<w:p><w:pPr><w:numPr><w:ilvl w:val="0"/></w:numPr></w:pPr><w:r><w:t>two</w:t></w:r></w:p>'
        '<w:tbl><w:tr><w:tc><w:p><w:r><w:t>Skills</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>Python</w:t></w:r></w:p></w:tc></w:tr></w:tbl>')
    html = docx_to_html(_doc_with(body))
    assert "<h2>" not in html and "<h1>Experience</h1>" in html and "<b>Bold</b>" in html and "<i>Ital</i>" in html
    assert html.count("<ul>") == 1 and "<li>one</li><li>two</li>" in html and "<td>Skills</td><td>Python</td>" in html


def test_bold_switched_off_is_not_bold():
    html = docx_to_html(_doc_with('<w:p><w:r><w:rPr><w:b w:val="0"/></w:rPr><w:t>plain</w:t></w:r></w:p>'))
    assert "<b>" not in html.split("<body>")[1]


def test_a_broken_docx_gives_422_not_a_crash(client):
    d = _up(client, _docx(_CV_LINES), "resume.docx")
    import asyncio
    bad = asyncio.new_event_loop().run_until_complete(app_module._cv_store.set_original(d["id"], b"PK\x03\x04garbage", "x.docx", "docx"))
    assert bad and client.get(f"/cv/{d['id']}/original.html").status_code == 422


# ── storage ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_database_from_before_originals_is_upgraded_in_place(tmp_path):
    import aiosqlite
    path = str(tmp_path / "old.db")
    async with aiosqlite.connect(path) as db:
        await db.execute("CREATE TABLE cv_profiles (id TEXT PRIMARY KEY, profile_json TEXT NOT NULL, template TEXT NOT NULL, created_at TEXT NOT NULL, edit_token TEXT, photo BLOB)")
        await db.execute("INSERT INTO cv_profiles VALUES ('old1', ?, 'modern', '2020-01-01', 'tok', NULL)", (_PROFILE.model_dump_json(),))
        await db.commit()
    store = CVStore(db_path=path); await store.init()
    row = await store.get("old1")
    assert row["has_original"] == 0 and row["original_name"] is None
    assert await store.set_original("old1", b"%PDF-x", "a.pdf", "pdf") and (await store.get_original("old1"))["name"] == "a.pdf"


@pytest.mark.asyncio
async def test_plain_reads_never_load_the_original_bytes(tmp_path):
    store = CVStore(db_path=str(tmp_path / "cv.db")); await store.init()
    cv_id, _ = await store.create(_PROFILE, "modern")
    await store.set_original(cv_id, b"%PDF-" + b"x" * 1000, "a.pdf", "pdf")
    row = await store.get(cv_id)
    assert row["has_original"] == 1 and "original" not in row


# ── share page: the Original card's Copy link button ──

def test_the_share_page_has_a_copy_link_button_for_a_pdf_original(client):
    d = _up(client, _cv_pdf())
    page = client.get(f"/cv-builder/{d['id']}").text
    assert f'data-path="/audit/cv/{d["id"]}/original.html?lang=en"' in page and 'class="copylink"' in page
    assert f"/audit/cv/{d['id']}/original?download=1" in page  # the download link is still there beside it


def test_a_word_originals_link_points_at_its_readable_preview(client):
    d = _up(client, _docx(_CV_LINES), "resume.docx")
    assert f'data-path="/audit/cv/{d["id"]}/original.html?lang=en"' in client.get(f"/cv-builder/{d['id']}").text


def test_no_original_card_or_copy_button_without_an_original(client):
    d = client.post("/cv/from-text", json={"text": "Jordan Reyes, engineer, jordan@example.com"}).json()
    page = client.get(f"/cv-builder/{d['id']}").text
    assert 'class="copylink"' not in page and "/original" not in page.split("<script>")[0]


# ── deleting the whole CV removes its original and photo too ──

def test_deleting_a_cv_removes_everything_stored_for_it(client):
    from tests.test_cv_photo import _jpeg
    d = _up(client, _cv_pdf()); i = d["id"]
    client.post(f"/cv/{i}/photo", files={"file": ("p.jpg", _jpeg(), "image/jpeg")}, data={"edit_token": d["edit_token"]})
    assert client.get(f"/cv/{i}/original").status_code == 200
    r = client.delete(f"/cv/{i}", headers={"X-Edit-Token": d["edit_token"]})
    assert r.status_code == 200 and r.json() == {"deleted": True}
    for url in (f"/cv/{i}", f"/cv/{i}/original", f"/cv/{i}/original.html", f"/cv/{i}.html", f"/cv/{i}.pdf", f"/cv/{i}.md", f"/cv-builder/{i}"):
        assert client.get(url).status_code == 404, url
    import asyncio
    store = app_module._cv_store
    loop = asyncio.new_event_loop()
    assert loop.run_until_complete(store.get_original(i)) is None and loop.run_until_complete(store.get_photo(i)) is None


def test_deleting_needs_the_edit_token(client):
    d = _up(client, _cv_pdf())
    assert client.delete(f"/cv/{d['id']}").status_code == 403
    assert client.delete(f"/cv/{d['id']}", headers={"X-Edit-Token": "wrong"}).status_code == 403
    assert client.get(f"/cv/{d['id']}/original").status_code == 200  # nothing was touched


def test_another_cvs_token_cannot_delete_it(client):
    a, b = _up(client, _cv_pdf()), _up(client, _cv_pdf())
    assert client.delete(f"/cv/{b['id']}", headers={"X-Edit-Token": a["edit_token"]}).status_code == 403
    assert client.get(f"/cv/{b['id']}").status_code == 200


def test_deleting_one_cv_leaves_the_others_alone(client):
    a, b = _up(client, _cv_pdf()), _up(client, _docx(_CV_LINES), "r.docx")
    client.delete(f"/cv/{a['id']}", headers={"X-Edit-Token": a["edit_token"]})
    assert client.get(f"/cv/{b['id']}").status_code == 200 and client.get(f"/cv/{b['id']}/original").status_code == 200


def test_deleting_twice_or_an_unknown_cv_is_404(client):
    d = _up(client, _cv_pdf())
    assert client.delete(f"/cv/{d['id']}", headers={"X-Edit-Token": d["edit_token"]}).status_code == 200
    assert client.delete(f"/cv/{d['id']}", headers={"X-Edit-Token": d["edit_token"]}).status_code == 404
    assert client.delete("/cv/nope").status_code == 404


def test_a_cv_with_no_stored_token_can_never_be_deleted_by_anyone(client):
    d = client.post("/cv/from-text", json={"text": "Jordan Reyes, engineer, jordan@example.com"}).json()
    import asyncio, aiosqlite
    async def strip():
        async with aiosqlite.connect(app_module._cv_store.db_path) as db:
            await db.execute("UPDATE cv_profiles SET edit_token = NULL WHERE id = ?", (d["id"],)); await db.commit()
    asyncio.new_event_loop().run_until_complete(strip())
    assert client.delete(f"/cv/{d['id']}", headers={"X-Edit-Token": ""}).status_code == 403
