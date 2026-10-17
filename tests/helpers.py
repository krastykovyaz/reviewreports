"""Shared test helpers. Test modules import from here, never from each other: renaming a helper used to break four modules."""
import asyncio
import zipfile
from io import BytesIO

from PIL import Image

from src.cv.render import render_cv_pdf
from src.cv.schema import CVProfile
from pypdf import PdfReader, PdfWriter


def run(coro):
    """Runs a coroutine to completion in a fresh loop that is CLOSED afterwards (asyncio.new_event_loop().run_until_complete
    leaked an open loop per call: the 'unclosed event loop' warnings)."""
    return asyncio.run(coro)


_PROFILE = CVProfile.model_validate({
    "name": "Jordan Reyes", "role": "Senior Backend Engineer",
    "contact": {"email": "jordan.reyes@email.com", "location": "San Francisco"},
    "experience": [{"title": "Senior Backend Engineer", "organization": "Northwind Systems", "start": "2021", "end": "Present",
                    "bullets": ["Redesigned the payments pipeline.", "Led a team of four engineers."]}],
    "education": [{"degree": "B.S. Computer Science", "institution": "UC San Diego", "year": "2018"}],
    "skills": ["Python", "Go", "PostgreSQL"],
})


def _cv_pdf() -> bytes:
    return render_cv_pdf(_PROFILE, "classic")


def _blank_pdf(pages=1) -> bytes:
    w = PdfWriter()
    for _ in range(pages):
        w.add_blank_page(width=200, height=200)
    b = BytesIO(); w.write(b); return b.getvalue()


def _encrypted_pdf(password="secret") -> bytes:
    w = PdfWriter(clone_from=PdfReader(BytesIO(_cv_pdf())))
    w.encrypt(password)
    b = BytesIO(); w.write(b); return b.getvalue()


_NS = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'


def _p(text):
    return f"<w:p><w:r><w:t>{text}</w:t></w:r></w:p>"


def _docx(body_paragraphs, header=(), footer=(), extra_parts=None) -> bytes:
    b = BytesIO()
    with zipfile.ZipFile(b, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("word/document.xml", f"<w:document {_NS}><w:body>{''.join(_p(t) for t in body_paragraphs)}</w:body></w:document>")
        if header:
            z.writestr("word/header1.xml", f"<w:hdr {_NS}>{''.join(_p(t) for t in header)}</w:hdr>")
        if footer:
            z.writestr("word/footer1.xml", f"<w:ftr {_NS}>{''.join(_p(t) for t in footer)}</w:ftr>")
        for name, data in (extra_parts or {}).items():
            z.writestr(name, data)
    return b.getvalue()


_BOMB_DTD = ('<!DOCTYPE w:document [<!ENTITY a "aaaaaaaaaa"><!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">'
             '<!ENTITY c "&b;&b;&b;&b;&b;&b;&b;&b;&b;&b;">]>')


def _docx_with_dtd(dtd=_BOMB_DTD, lines=None, part="word/document.xml", prolog='<?xml version="1.0" encoding="UTF-8" standalone="yes"?>') -> bytes:
    """A Word file whose part carries a DTD with nested internal entities (the expansion-bomb shape, kept tiny here: x1000).
    Word itself never writes a DOCTYPE. `part` picks which part carries it (the body unless a header is asked for)."""
    body = "".join(_p(t) for t in (lines or ["Jordan Reyes, senior engineer at Northwind Systems, jordan.reyes@email.com", "&c;"]))
    doc = f"{prolog}{dtd}<w:document {_NS}><w:body>{body}</w:body></w:document>"
    b = BytesIO()
    with zipfile.ZipFile(b, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        if part == "word/document.xml":
            z.writestr(part, doc)
        else:
            z.writestr("word/document.xml", f"<w:document {_NS}><w:body>{_p('Body text of the document')}</w:body></w:document>")
            z.writestr(part, doc.replace("w:document", "w:hdr").replace("<w:body>", "").replace("</w:body>", ""))
    return b.getvalue()


_CV_LINES = ["Jordan Reyes", "Senior Backend Engineer at Northwind Systems 2021 - Present", "Redesigned the payments pipeline and led a team of four.", "B.S. Computer Science, UC San Diego, 2018"]


def _jpeg(size=(800, 400), color=(200, 40, 40), **kw) -> bytes:
    b = BytesIO(); Image.new("RGB", size, color).save(b, "JPEG", **kw); return b.getvalue()


def _up(client, data, name="My CV.pdf"):
    return client.post("/cv/from-file", files={"file": (name, data, "application/octet-stream")}).json()


import httpx as _httpx
_REAL_ASYNC_CLIENT = _httpx.AsyncClient


def mock_http(monkeypatch, handler):
    """Every httpx.AsyncClient the audit code creates talks to `handler` through a real httpx.MockTransport (so redirects,
    streaming, closing and event hooks behave as in production), and DNS answers a public address for names while IP
    literals resolve to themselves (so the SSRF guard still sees 10.0.0.1 as 10.0.0.1)."""
    import ipaddress
    import httpx
    from src.audit import http_client

    real = _REAL_ASYNC_CLIENT  # not httpx.AsyncClient: a second mock_http in one test must replace the first, not wrap it

    class Mocked(real):
        def __init__(self, *a, **kw):
            kw.pop("transport", None)
            super().__init__(*a, transport=httpx.MockTransport(handler), **kw)

    def resolve(host):
        try:
            ipaddress.ip_address(host.strip("[]"))
            return [host.strip("[]")]
        except ValueError:
            return ["93.184.216.34"]

    monkeypatch.setattr(httpx, "AsyncClient", Mocked)
    monkeypatch.setattr(http_client, "_resolve_all_sync", resolve)
