"""Uploading an existing CV as a Word (.docx) file, and typing notes alongside any file.

A .docx is a zip of XML read with the standard library. Like the PDF path: untrusted input,
content-sniffed (not trusted by name/type), bounded, and only text is kept.
"""
import zipfile
from io import BytesIO

import pytest
from fastapi.testclient import TestClient

import src.service.app as app_module
from src.cv import doc_text
from src.cv.doc_text import DocError, extract_document_text
from src.cv.schema import CVProfile
from src.service.cv_store import CVStore
from tests.helpers import _PROFILE, _cv_pdf, _NS, _p, _docx, _docx_with_dtd, _CV_LINES









# ── extract_document_text ─────────────────────────────────────────

def test_text_is_read_from_a_docx_in_order():
    text = extract_document_text(_docx(_CV_LINES))
    assert text.splitlines()[0] == "Jordan Reyes" and "Northwind Systems" in text and "UC San Diego" in text


def test_header_and_footer_text_is_included_because_contact_details_live_there():
    text = extract_document_text(_docx(_CV_LINES, header=["jordan.reyes@email.com | +1 415 555 0142"], footer=["San Francisco, CA"]))
    assert "jordan.reyes@email.com" in text and "San Francisco, CA" in text


def test_tables_and_tabs_and_breaks_are_read():
    body = (f"<w:tbl><w:tr><w:tc>{_p('Skills')}</w:tc><w:tc>{_p('Python, Go, PostgreSQL, Kubernetes, Docker and more things')}</w:tc></w:tr></w:tbl>"
            "<w:p><w:r><w:t>Role</w:t><w:tab/><w:t>Engineer</w:t><w:br/><w:t>Second line of the same paragraph for length purposes</w:t></w:r></w:p>")
    b = BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        z.writestr("word/document.xml", f"<w:document {_NS}><w:body>{body}</w:body></w:document>")
    text = extract_document_text(b.getvalue())
    assert "Python, Go" in text and "Role Engineer" in text and "Second line" in text


def test_a_text_box_stored_twice_is_not_duplicated():
    line = "Jordan Reyes - Senior Backend Engineer - jordan.reyes@email.com - San Francisco"
    text = extract_document_text(_docx([line, line, *_CV_LINES[1:]]))
    assert text.count("Jordan Reyes - Senior") == 1


def test_a_docx_with_no_real_text_is_no_text():
    with pytest.raises(DocError) as e:
        extract_document_text(_docx(["hi"]))
    assert e.value.code == "no_text"


def test_a_zip_that_is_not_a_word_document_is_unsupported():
    b = BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        z.writestr("hello.txt", "x" * 500)
    with pytest.raises(DocError) as e:
        extract_document_text(b.getvalue())
    assert e.value.code == "unsupported"


def test_legacy_doc_and_password_protected_docx_get_the_legacy_code():
    with pytest.raises(DocError) as e:
        extract_document_text(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\0" * 600)
    assert e.value.code == "legacy"


@pytest.mark.parametrize("raw", [b"", b"plain text, not a document at all", b"<html></html>", b"MZ\x90\x00" + b"\0" * 100])
def test_anything_else_is_unsupported(raw):
    with pytest.raises(DocError) as e:
        extract_document_text(raw)
    assert e.value.code == "unsupported"


def test_a_corrupt_docx_is_unreadable_not_a_crash():
    good = _docx(_CV_LINES)
    with pytest.raises(DocError) as e:
        extract_document_text(good[: len(good) // 2])
    assert e.value.code == "unreadable"
    with pytest.raises(DocError) as e2:
        extract_document_text(_docx(_CV_LINES, extra_parts={"word/header1.xml": "<not-closed"}))
    assert e2.value.code == "unreadable"


def test_a_part_that_inflates_beyond_the_cap_is_refused_before_reading(monkeypatch):
    monkeypatch.setattr(doc_text, "MAX_XML_BYTES", 200)
    with pytest.raises(DocError) as e:
        extract_document_text(_docx(_CV_LINES))
    assert e.value.code == "unreadable"


def test_a_zip_with_a_huge_entry_count_is_refused(monkeypatch):
    monkeypatch.setattr(doc_text, "MAX_ZIP_ENTRIES", 2)
    with pytest.raises(DocError) as e:
        extract_document_text(_docx(_CV_LINES, header=["x"], footer=["y"]))
    assert e.value.code == "unreadable"


def test_the_kind_is_decided_by_content_not_by_name_or_claimed_type():
    # A PDF is still read as a PDF whatever it's called.
    assert "Jordan Reyes" in extract_document_text(_cv_pdf())


def test_output_is_capped(monkeypatch):
    monkeypatch.setattr(doc_text, "MAX_CHARS", 60)
    assert len(extract_document_text(_docx(_CV_LINES))) == 60


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


def _post(client, data, name="cv.docx", **form):
    return client.post("/cv/from-file", files={"file": (name, data, "application/octet-stream")}, data=form)


def test_a_docx_creates_a_cv(client):
    r = _post(client, _docx(_CV_LINES))
    assert r.status_code == 200 and r.json()["profile"]["name"] == "Jordan Reyes" and r.json()["edit_token"]
    assert "Northwind Systems" in client.seen["text"] and r.json()["source_text"] == client.seen["text"]


def test_a_pdf_and_a_docx_go_through_the_same_endpoint(client):
    assert _post(client, _cv_pdf(), name="cv.pdf").status_code == 200
    assert _post(client, _docx(_CV_LINES)).status_code == 200


def test_the_old_pdf_endpoint_name_still_works(client):
    r = client.post("/cv/from-pdf", files={"file": ("cv.pdf", _cv_pdf(), "application/pdf")})
    assert r.status_code == 200


def test_typed_notes_are_added_for_the_model_and_shown_in_the_source(client):
    r = _post(client, _docx(_CV_LINES), extra_text="Also fluent in Spanish; open to relocation.")
    assert r.status_code == 200
    assert "fluent in Spanish" in client.seen["text"] and "Northwind Systems" in client.seen["text"]
    assert "fluent in Spanish" in r.json()["source_text"]


def test_notes_are_length_limited(client):
    assert _post(client, _docx(_CV_LINES), extra_text="x" * 5_001).status_code == 422


def test_the_file_name_is_not_trusted(client):
    assert _post(client, _docx(_CV_LINES), name="resume.pdf").status_code == 200
    assert _post(client, b"MZ" + b"\0" * 300, name="resume.docx").json()["detail"]["code"] == "doc_unsupported"


@pytest.mark.parametrize("data,status,code", [
    (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\0" * 600, 400, "doc_legacy"),
    (_docx(["hi"]), 422, "doc_no_text"),
    (b"just text", 400, "doc_unsupported"),
])
def test_bad_documents_get_a_specific_code(client, data, status, code):
    r = _post(client, data)
    assert r.status_code == status and r.json()["detail"]["code"] == code
    assert "text" not in client.seen  # nothing unusable reaches the model


def test_the_created_cv_is_the_same_kind_of_object_as_any_other(client):
    d = _post(client, _docx(_CV_LINES)).json()
    assert client.get(f"/cv/{d['id']}.pdf").content.startswith(b"%PDF")
    assert client.post(f"/cv/{d['id']}/edit", json={"instructions": "x", "edit_token": "wrong"}).status_code == 403


# ── a zip header's declared size is not trusted ───────────────────

def _lying_docx(inflated_bytes: int, declared: int = 1000) -> bytes:
    """A .docx whose document.xml really inflates to `inflated_bytes` but whose headers claim `declared`."""
    import re
    import struct
    body = b'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>' + \
           b"<w:p><w:r><w:t>A</w:t></w:r></w:p>" * (inflated_bytes // 34) + b"</w:body></w:document>"
    b = BytesIO()
    with zipfile.ZipFile(b, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("word/document.xml", body)
    buf = bytearray(b.getvalue())
    for m in re.finditer(re.escape(b"word/document.xml"), bytes(buf)):
        for hdr_len, size_off in ((30, 22), (46, 24)):  # local header / central directory: uncompressed-size field
            h = m.start() - hdr_len
            if h >= 0 and bytes(buf[h:h + 4]) in (b"PK\x03\x04", b"PK\x01\x02"):
                buf[h + size_off:h + size_off + 4] = struct.pack("<I", declared)
    return bytes(buf)


def _peak_rss_mb_of(code: str) -> int:
    import subprocess, sys
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120, cwd=".")
    return int(out.stdout.strip().splitlines()[-1])


def test_a_member_that_inflates_far_beyond_its_declared_size_is_rejected():
    raw = _lying_docx(30 * 1024 * 1024)  # ~30MB of XML claiming to be 1000 bytes
    with pytest.raises(DocError) as e:
        extract_document_text(raw)
    assert e.value.code == "unreadable"


def test_the_lying_member_does_not_cost_its_inflated_size_in_memory(tmp_path):
    """Measured in a child process so imports and the test runner don't muddy the number."""
    f = tmp_path / "liar.docx"; f.write_bytes(_lying_docx(120 * 1024 * 1024))
    code = (
        "import resource, sys; sys.path.insert(0, '.'); "
        "from src.cv import doc_text; "
        "rss = lambda: resource.getrusage(resource.RUSAGE_SELF).ru_maxrss // 1024; base = rss(); "
        f"raw = open(r'{f}', 'rb').read(); "
        "\ntry: doc_text.extract_document_text(raw)\nexcept Exception: pass\n"
        "print(rss() - base)"
    )
    assert _peak_rss_mb_of(code) < 60  # unbounded this was ~260MB for a 136MB member


def test_the_preview_path_is_bounded_the_same_way():
    from src.cv.docx_preview import docx_to_html
    with pytest.raises(Exception):
        docx_to_html(_lying_docx(30 * 1024 * 1024))


def test_too_many_header_and_footer_parts_is_refused():
    b = BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        z.writestr("word/document.xml", f'<w:document {_NS}><w:body>{_p("x" * 100)}</w:body></w:document>')
        for i in range(doc_text.MAX_EXTRA_PARTS + 1):
            z.writestr(f"word/header{i}.xml", f"<w:hdr {_NS}>{_p('h')}</w:hdr>")
    with pytest.raises(DocError) as e:
        extract_document_text(b.getvalue())
    assert e.value.code == "unreadable"


def test_the_whole_file_shares_one_inflate_budget(monkeypatch):
    monkeypatch.setattr(doc_text, "MAX_XML_TOTAL_BYTES", 600)
    b = BytesIO()
    big = f"<w:hdr {_NS}>" + _p("h" * 200) + "</w:hdr>"
    with zipfile.ZipFile(b, "w") as z:
        z.writestr("word/header1.xml", big); z.writestr("word/header2.xml", big)
        z.writestr("word/document.xml", f'<w:document {_NS}><w:body>{_p("x" * 100)}</w:body></w:document>')
    with pytest.raises(DocError):
        extract_document_text(b.getvalue())  # each part is small, together they exceed the budget


def test_ordinary_documents_with_headers_and_footers_still_read_fine():
    text = extract_document_text(_docx(_CV_LINES, header=["jordan.reyes@email.com | +1 415 555 0142"], footer=["San Francisco"]))
    assert "jordan.reyes@email.com" in text and "San Francisco" in text and "Northwind Systems" in text


# ── XML entity expansion ("billion laughs"): Word never writes a DTD, so a part with one is refused unread ──

def test_a_part_with_a_dtd_is_rejected_by_text_extraction():
    with pytest.raises(DocError) as e:
        extract_document_text(_docx_with_dtd())
    assert e.value.code == "unreadable"


def test_a_part_with_a_dtd_is_rejected_by_the_preview():
    from src.cv.docx_preview import docx_to_html
    with pytest.raises(DocError):
        docx_to_html(_docx_with_dtd())


def test_a_dtd_in_a_header_part_is_rejected_too():
    with pytest.raises(DocError):
        extract_document_text(_docx_with_dtd(part="word/header1.xml"))


@pytest.mark.parametrize("dtd", [
    '<!doctype w:document [<!entity a "x">]>',                      # lower case
    '<!DOCTYPE w:document SYSTEM "file:///etc/passwd">',            # external, no entities
    '<!  ENTITY a "x">',                                            # declaration without a DOCTYPE wrapper
    '<!DOCTYPE w:document [<!ENTITY a "x">]>',
])
def test_every_spelling_of_a_dtd_is_rejected(dtd):
    with pytest.raises(DocError):
        extract_document_text(_docx_with_dtd(dtd=dtd, lines=["Jordan Reyes, senior backend engineer at Northwind Systems since 2021, contact jordan.reyes@email.com"]))


def test_a_dtd_hidden_behind_another_encoding_is_rejected_not_parsed():
    # The raw-bytes check can only see ASCII: UTF-16 (NUL bytes) and a declared exotic charset (here EBCDIC, which expat
    # would decode through Python's codecs) would otherwise carry the DOCTYPE past it.
    for enc in ("utf-16", "cp037"):
        part = ('<?xml version="1.0" encoding="%s"?><!DOCTYPE d [<!ENTITY a "x">]><w:document %s><w:body>%s</w:body></w:document>'
                % (enc, _NS, _p("x" * 200))).encode(enc)
        b = BytesIO()
        with zipfile.ZipFile(b, "w") as z:
            z.writestr("word/document.xml", part)
        with pytest.raises(DocError):
            extract_document_text(b.getvalue())


def test_a_normal_docx_with_an_xml_declaration_still_parses():
    raw = _docx_with_dtd(dtd="", lines=_CV_LINES)  # same builder, same XML declaration, no DTD
    text = extract_document_text(raw)
    assert "Northwind Systems" in text


def test_text_kept_from_one_part_is_capped(monkeypatch):
    monkeypatch.setattr(doc_text, "MAX_PART_CHARS", 500)
    lines = doc_text._paragraph_lines(_docx_part([f"paragraph number {i} of many" for i in range(1000)]))
    assert 0 < len("".join(lines)) < 700  # stops soon after the cap, not after all 1000 paragraphs
    one_big = doc_text._paragraph_lines(_docx_part(["word " * 5000]))
    assert len(one_big[0]) < 700  # and one enormous paragraph is cut off inside the paragraph


def test_the_preview_caps_what_it_builds_from_one_part(monkeypatch):
    from src.cv import docx_preview
    monkeypatch.setattr(docx_preview, "MAX_PART_CHARS", 500)
    page = docx_preview.docx_to_html(_docx([f"paragraph number {i} of many" for i in range(1000)]))
    assert 0 < page.count("<p>") < 60
    one_big = docx_preview.docx_to_html(_docx(["word " * 5000]))
    assert len(one_big) < 3000


def _docx_part(paragraphs) -> bytes:
    """Just the document.xml of a docx made from these paragraphs."""
    return f"<w:document {_NS}><w:body>{''.join(_p(t) for t in paragraphs)}</w:body></w:document>".encode()
