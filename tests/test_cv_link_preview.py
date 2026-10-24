"""Link previews: a shared link to a CV's HTML, its share page or its original shows a real card (a 1200x630
image of the CV plus name and role) in Telegram, WhatsApp, LinkedIn, Facebook and X - not the generic logo.
"""
import re
from io import BytesIO

import pytest
from fastapi.testclient import TestClient
from PIL import Image

import src.service.app as app_module
from src.cv.preview_card import RasterError, compose_card, rasterize_first_page
from src.service.cv_store import CVStore
from tests.helpers import _PROFILE, _cv_pdf, _docx, _CV_LINES, _jpeg

CRAWLERS = ["facebookexternalhit/1.1", "WhatsApp/2.23.20.0", "TelegramBot (like TwitterBot)", "LinkedInBot/1.0", "Twitterbot/1.0"]


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


def _meta(html, key):
    m = re.search(rf'<meta (?:property|name)="{re.escape(key)}" content="([^"]*)"', html)
    return m.group(1) if m else None


# ── the image ─────────────────────────────────────────────────────

@pytest.mark.parametrize("template", ["modern", "classic", "compact"])
def test_each_template_has_a_1200x630_jpeg_small_enough_for_whatsapp(client, template):
    d = _up(client, _cv_pdf())
    r = client.get(f"/cv/{d['id']}/preview.jpg", params={"template": template})
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
    im = Image.open(BytesIO(r.content)); assert im.format == "JPEG" and im.size == (1200, 630)
    assert len(r.content) < 250 * 1024 and r.headers["cache-control"].startswith("public")


def test_unknown_template_is_400_and_unknown_cv_is_404(client):
    d = _up(client, _cv_pdf())
    assert client.get(f"/cv/{d['id']}/preview.jpg", params={"template": "nope"}).status_code == 400
    assert client.get("/cv/nope/preview.jpg").status_code == 404
    assert client.get("/cv/nope/original-preview.jpg").status_code == 404


def test_the_image_is_cached_and_regenerated_when_the_cv_changes(client, tmp_path):
    d = _up(client, _cv_pdf()); previews = tmp_path / "previews"
    a = client.get(f"/cv/{d['id']}/preview.jpg").content
    files = list(previews.glob(f"{d['id']}.modern.*.jpg")); assert len(files) == 1
    assert client.get(f"/cv/{d['id']}/preview.jpg").content == a and list(previews.glob(f"{d['id']}.modern.*.jpg")) == files
    client.post(f"/cv/{d['id']}/photo", files={"file": ("p.jpg", _jpeg(), "image/jpeg")}, data={"edit_token": d["edit_token"]})
    b = client.get(f"/cv/{d['id']}/preview.jpg").content
    new = list(previews.glob(f"{d['id']}.modern.*.jpg"))
    assert b != a and len(new) == 1 and new != files  # the stale image was replaced, not left behind


def test_the_preview_shows_the_name_and_page(client):
    d = _up(client, _cv_pdf())
    im = Image.open(BytesIO(client.get(f"/cv/{d['id']}/preview.jpg").content)).convert("RGB")
    left, right = im.crop((0, 0, 600, 630)), im.crop((600, 0, 1200, 630))
    assert len({p for p in left.getdata()}) > 50 and len({p for p in right.getdata()}) > 20  # both halves have real content


def test_deleting_the_cv_removes_its_preview_images(client, tmp_path):
    d = _up(client, _cv_pdf())
    client.get(f"/cv/{d['id']}/preview.jpg"); client.get(f"/cv/{d['id']}/original-preview.jpg")
    assert list((tmp_path / "previews").glob(f"{d['id']}.*.jpg"))
    client.delete(f"/cv/{d['id']}", headers={"X-Edit-Token": d["edit_token"]})
    assert not list((tmp_path / "previews").glob(f"{d['id']}.*.jpg"))
    assert client.get(f"/cv/{d['id']}/preview.jpg").status_code == 404


def test_the_original_preview_is_the_uploaded_pdfs_own_page(client):
    d = _up(client, _cv_pdf())
    orig = client.get(f"/cv/{d['id']}/original-preview.jpg")
    assert orig.status_code == 200 and Image.open(BytesIO(orig.content)).size == (1200, 630)


def test_a_word_original_falls_back_to_our_render_for_its_card(client):
    d = _up(client, _docx(_CV_LINES), "resume.docx")
    assert client.get(f"/cv/{d['id']}/original-preview.jpg").status_code == 200


def test_an_unrenderable_original_pdf_still_gets_a_card(client, monkeypatch):
    d = _up(client, _cv_pdf())
    real = app_module.rasterize_first_page
    calls = {"n": 0}

    def flaky(pdf, *a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RasterError("boom")
        return real(pdf, *a, **k)

    monkeypatch.setattr(app_module, "rasterize_first_page", flaky)
    assert client.get(f"/cv/{d['id']}/original-preview.jpg").status_code == 200


def test_a_render_failure_is_503_not_a_crash(client, monkeypatch):
    d = _up(client, _cv_pdf())
    monkeypatch.setattr(app_module, "rasterize_first_page", lambda *a, **k: (_ for _ in ()).throw(RasterError("x")))
    assert client.get(f"/cv/{d['id']}/preview.jpg").status_code == 503


# ── the tags ──────────────────────────────────────────────────────

@pytest.mark.parametrize("ua", CRAWLERS)
def test_the_html_link_carries_a_large_card_for_every_crawler(client, ua):
    d = _up(client, _cv_pdf()); i = d["id"]
    r = client.get(f"/cv/{i}.html", params={"template": "classic"}, headers={"User-Agent": ua})
    assert r.status_code == 200
    assert _meta(r.text, "og:image") == f"https://tsech.online/audit/cv/{i}/preview.jpg?template=classic"
    assert _meta(r.text, "og:image:width") == "1200" and _meta(r.text, "og:image:height") == "630"
    assert _meta(r.text, "twitter:card") == "summary_large_image" and _meta(r.text, "twitter:image") == _meta(r.text, "og:image")
    assert _meta(r.text, "og:title") and _meta(r.text, "og:description") and _meta(r.text, "og:type") == "website"
    assert _meta(r.text, "og:url") == f"https://tsech.online/audit/cv/{i}.html?template=classic"


def test_the_share_page_carries_the_per_cv_image(client):
    i = _up(client, _cv_pdf())["id"]
    page = client.get(f"/cv-builder/{i}").text
    assert _meta(page, "og:image") == f"https://tsech.online/audit/cv/{i}/preview.jpg?template=modern"
    assert _meta(page, "twitter:card") == "summary_large_image" and _meta(page, "og:image:width") == "1200"


@pytest.mark.parametrize("name,data", [("cv.pdf", None), ("cv.docx", "docx")])
def test_the_original_link_has_its_own_page_with_a_card(client, name, data):
    d = _up(client, _docx(_CV_LINES) if data else _cv_pdf(), name); i = d["id"]
    r = client.get(f"/cv/{i}/original.html")
    assert r.status_code == 200
    assert _meta(r.text, "og:image") == f"https://tsech.online/audit/cv/{i}/original-preview.jpg"
    assert _meta(r.text, "og:url") == f"https://tsech.online/audit/cv/{i}/original.html"
    assert "Jordan Reyes" in _meta(r.text, "og:title") and _meta(r.text, "twitter:card") == "summary_large_image"
    assert "default-src 'none'" in r.headers["content-security-policy"]


def test_the_pdf_page_frames_only_our_own_file_and_escapes_a_hostile_name(client):
    d = _up(client, _cv_pdf(), name='<script>alert(1)</script>"><img src=x>.pdf'); i = d["id"]
    page = client.get(f"/cv/{i}/original.html").text.split("</head>")[1]
    assert "<script>" not in page and "<img src=x" not in page and f'src="/audit/cv/{i}/original"' in page
    assert "frame-src 'self'" in client.get(f"/cv/{i}/original.html").headers["content-security-policy"]


def test_tag_values_are_escaped(client):
    i = _up(client, _cv_pdf())["id"]
    html = app_module._inject_og_meta("<head></head>", title='A "quoted" <b>name</b>', description="x & y", url="https://t/x?a=1&b=2", image="https://t/i.jpg?a=1&b=2")
    assert "<b>" not in html and "&quot;quoted&quot;" in html and "x &amp; y" in html and "a=1&amp;b=2" in html


def test_without_an_image_the_logo_card_is_used():
    html = app_module._inject_og_meta("<head></head>", title="t", description="d", url="https://t/")
    assert _meta(html, "og:image") == "https://tsech.online/logo.png" and _meta(html, "twitter:card") == "summary"


def test_a_cv_without_an_original_has_no_original_page_or_image(client):
    d = client.post("/cv/from-text", json={"text": "Jordan Reyes, engineer, jordan@example.com"}).json()
    assert client.get(f"/cv/{d['id']}/original.html").status_code == 404
    assert client.get(f"/cv/{d['id']}/original-preview.jpg").status_code == 404


# ── the card builder itself ───────────────────────────────────────

def test_long_names_and_roles_are_wrapped_and_cut_not_overflowed():
    png = rasterize_first_page(_cv_pdf())
    card = compose_card(png, "Aleksandrovich " * 20, "Principal Staff Distinguished Engineer of Everything " * 10)
    assert Image.open(BytesIO(card)).size == (1200, 630) and len(card) < 250 * 1024


def test_the_rasterizer_refuses_garbage_without_taking_the_service_down():
    for bad in (b"", b"%PDF-1.4 not really", b"\x00" * 100):
        with pytest.raises(RasterError):
            rasterize_first_page(bad)
