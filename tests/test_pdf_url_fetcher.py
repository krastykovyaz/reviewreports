"""WeasyPrint never fetches anything but data: URIs for the PDFs this service renders."""
import http.server
import threading

import pytest
import weasyprint

from src.cv.render import render_cv_pdf
from src.report.render.pdf import render_pdf
from src.report.schema import Pillar, Report, ReportMeta
from src.service.pdf_fetch import deny_all_url_fetcher
from tests.helpers import _PROFILE, _jpeg


@pytest.fixture()
def server():
    hits = []

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            hits.append(self.path)
            self.send_response(200); self.send_header("content-type", "text/css"); self.end_headers(); self.wfile.write(b"body{color:red}")
        def log_message(self, *a): pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv.server_address[1], hits
    srv.shutdown()


def _hostile_html(port, local_file):
    return (f'<html><head><link rel="stylesheet" href="http://127.0.0.1:{port}/evil.css"><style>@import url("http://127.0.0.1:{port}/import.css");'
            f'p{{background:url(http://127.0.0.1:{port}/bg.png)}}</style></head><body><p>hello</p><img src="http://127.0.0.1:{port}/pixel.png">'
            f'<img src="file://{local_file}"></body></html>')


def test_a_hostile_document_makes_no_request_and_still_renders(server, tmp_path):
    port, hits = server
    secret = tmp_path / "secret.png"; secret.write_bytes(b"not really an image")
    pdf = weasyprint.HTML(string=_hostile_html(port, secret), url_fetcher=deny_all_url_fetcher()).write_pdf()
    assert pdf.startswith(b"%PDF") and hits == []


def test_the_default_fetcher_would_have_made_those_requests(server, tmp_path):
    """The control: without the fetcher the same document does reach the server - so the test above proves something."""
    port, hits = server
    secret = tmp_path / "secret.png"; secret.write_bytes(b"x")
    weasyprint.HTML(string=_hostile_html(port, secret)).write_pdf()
    assert hits


def test_data_uris_still_load():
    import base64
    uri = "data:image/jpeg;base64," + base64.b64encode(_jpeg((40, 40))).decode()
    pdf = weasyprint.HTML(string=f'<html><body><img src="{uri}" width="40"></body></html>', url_fetcher=deny_all_url_fetcher()).write_pdf()
    assert b"/Subtype /Image" in pdf or b"/Image" in pdf


@pytest.mark.parametrize("url", ["file:///etc/passwd", "http://127.0.0.1:1/x", "https://example.com/x.css", "ftp://example.com/x", "FILE:///etc/passwd"])
def test_everything_but_data_raises(url):
    with pytest.raises(Exception):
        deny_all_url_fetcher()(url)


def test_both_renderers_pass_the_deny_all_fetcher(monkeypatch):
    seen = []
    real = weasyprint.HTML

    class Spy(real):
        def __init__(self, *a, **kw):
            seen.append(kw.get("url_fetcher"))
            super().__init__(*a, **kw)

    monkeypatch.setattr(weasyprint, "HTML", Spy)
    assert render_cv_pdf(_PROFILE, "modern").startswith(b"%PDF")
    assert render_pdf(Report(meta=ReportMeta(kind="website_audit", subject="https://example.com", lang="en"), pillars=[Pillar(name="SEO", score=6, summary="ok")]).finalize()).startswith(b"%PDF")
    assert len(seen) == 2 and all(f is not None for f in seen)
    for fetcher in seen:
        with pytest.raises(Exception):
            fetcher("file:///etc/passwd")
