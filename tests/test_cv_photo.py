"""A CV's profile photo.

What matters here: the upload is re-encoded (never stored as received: EXIF stripped,
always a square JPEG we made), it needs the same edit token as editing the CV (a CV's id
is also its public share link), it's merged into the HTML/PDF at render time rather than
living in the profile that goes to the model, and adding one must never push a one-page
CV onto a second page.
"""
import asyncio
from io import BytesIO

import pytest
from fastapi.testclient import TestClient
from PIL import Image

import src.service.app as app_module
from src.cv import photo as photo_mod
from src.cv.photo import DATA_URI_PREFIX, process_cv_photo, to_data_uri
from src.cv.render import TEMPLATES, render_cv_html, render_cv_pdf
from src.cv.schema import Contact, CVProfile, Education, Experience
from src.service.cv_store import CVStore
from tests.helpers import _jpeg

_PAYLOAD = {
    "name": "Jordan Reyes", "role": "Senior Backend Engineer",
    "contact": {"email": "jordan.reyes@email.com", "phone": "+1 415 555 0142", "location": "San Francisco"},
    "experience": [
        {"title": "Senior Backend Engineer", "organization": "Northwind Systems", "start": "2021", "end": "Present",
         "bullets": ["Redesigned the payments pipeline.", "Led a team of four.", "Cut p99 latency by 3x."]},
        {"title": "Backend Engineer", "organization": "Acme Corp", "start": "2018", "end": "2021", "bullets": ["Built microservices."]},
    ],
    "education": [{"degree": "B.S. Computer Science", "institution": "UC San Diego", "year": "2018"}],
    "skills": ["Python", "Go", "PostgreSQL", "Kubernetes"], "languages": ["English (native)", "Spanish (fluent)"],
}




def _open(data: bytes) -> Image.Image:
    return Image.open(BytesIO(data))


def _profile() -> CVProfile:
    return CVProfile.model_validate(_PAYLOAD)


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "_cv_store", CVStore(db_path=str(tmp_path / "cv.db")))
    with TestClient(app_module.app) as c:
        yield c


def _create(client):
    return client.post("/cv", json=_PAYLOAD).json()


def _upload(client, created, data=None, token=None, name="me.jpg"):
    return client.post(f"/cv/{created['id']}/photo", files={"file": (name, data if data is not None else _jpeg(), "image/jpeg")},
                       data={"edit_token": token if token is not None else created["edit_token"]})


# ── process_cv_photo ──────────────────────────────────────────────

def test_output_is_a_square_jpeg_whatever_the_input_shape():
    for size in ((800, 400), (400, 800), (3000, 2000), (50, 50)):
        out = process_cv_photo(_jpeg(size))
        im = _open(out)
        assert im.format == "JPEG" and im.size == (photo_mod.SIZE, photo_mod.SIZE)


def test_exif_is_applied_then_dropped():
    exif = Image.Exif(); exif[0x0112] = 6; exif[0x010F] = "SecretPhoneMaker"
    out = process_cv_photo(_jpeg((800, 400), exif=exif))
    assert len(_open(out).getexif()) == 0 and b"SecretPhoneMaker" not in out


def test_transparent_png_is_flattened_onto_white():
    b = BytesIO(); Image.new("RGBA", (200, 200), (0, 0, 0, 0)).save(b, "PNG")
    px = _open(process_cv_photo(b.getvalue())).getpixel((5, 5))
    assert all(v > 240 for v in px)  # not black: a CV page is white


@pytest.mark.parametrize("raw", [b"", b"nope", b"<svg xmlns='http://www.w3.org/2000/svg' onload='alert(1)'/>", b"<html><script>1</script></html>"])
def test_non_images_are_rejected(raw):
    with pytest.raises(ValueError):
        process_cv_photo(raw)


def test_formats_outside_the_allowed_four_are_rejected():
    b = BytesIO(); Image.new("RGB", (10, 10)).save(b, "BMP")
    with pytest.raises(ValueError):
        process_cv_photo(b.getvalue())


def test_pixel_bomb_is_rejected_before_decoding():
    b = BytesIO(); Image.new("L", (7000, 7000), 0).save(b, "PNG")  # 49M pixels, tiny file
    with pytest.raises(ValueError):
        process_cv_photo(b.getvalue())


# ── rendering ─────────────────────────────────────────────────────

@pytest.mark.parametrize("template", TEMPLATES)
def test_photo_is_rendered_into_every_template(template):
    uri = to_data_uri(process_cv_photo(_jpeg()))
    with_photo = render_cv_html(_profile(), template, uri)
    assert f'<img class="photo" src="{uri}"' in with_photo
    assert 'class="photo"' not in render_cv_html(_profile(), template)  # and nothing without one


@pytest.mark.parametrize("bad", ["https://evil.example/x.png", "javascript:alert(1)", "data:text/html;base64,PHNjcmlwdD4=", '"><script>alert(1)</script>'])
def test_only_our_own_data_uri_is_ever_accepted_as_a_photo(bad):
    # The renderer is the last line: nothing but a data URI we produced can reach the page.
    for template in TEMPLATES:
        html = render_cv_html(_profile(), template, bad)
        assert 'class="photo"' not in html and "evil.example" not in html and "alert(1)" not in html


@pytest.mark.parametrize("template", TEMPLATES)
def test_a_photo_does_not_push_a_one_page_cv_onto_a_second_page(template):
    from weasyprint import HTML
    uri = to_data_uri(process_cv_photo(_jpeg()))
    pages = lambda photo: len(HTML(string=render_cv_html(_profile(), template, photo)).render().pages)
    assert pages(uri) == pages(None) == 1


def test_pdf_with_a_photo_is_a_valid_pdf_and_larger_than_without():
    uri = to_data_uri(process_cv_photo(_jpeg()))
    with_photo, without = render_cv_pdf(_profile(), "modern", uri), render_cv_pdf(_profile(), "modern")
    assert with_photo.startswith(b"%PDF") and len(with_photo) > len(without)


# ── storage ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_store_set_get_clear_and_the_row_never_carries_the_bytes(tmp_path):
    store = CVStore(db_path=str(tmp_path / "cv.db")); await store.init()
    cv_id, _ = await store.create(_profile(), "modern")
    assert (await store.get(cv_id))["has_photo"] == 0 and await store.get_photo(cv_id) is None
    jpeg = process_cv_photo(_jpeg())
    assert await store.set_photo(cv_id, jpeg)
    row = await store.get(cv_id)
    assert row["has_photo"] == 1 and "photo" not in row  # a flag, not 40KB copied into every render/GET
    assert await store.get_photo(cv_id) == jpeg
    await store.set_photo(cv_id, None)
    assert (await store.get(cv_id))["has_photo"] == 0


@pytest.mark.asyncio
async def test_a_database_from_before_photos_existed_is_upgraded_in_place(tmp_path):
    import aiosqlite
    path = str(tmp_path / "old.db")
    async with aiosqlite.connect(path) as db:
        await db.execute("CREATE TABLE cv_profiles (id TEXT PRIMARY KEY, profile_json TEXT NOT NULL, template TEXT NOT NULL, created_at TEXT NOT NULL, edit_token TEXT)")
        await db.execute("INSERT INTO cv_profiles VALUES ('old1', ?, 'modern', '2020-01-01', 'tok')", (_profile().model_dump_json(),))
        await db.commit()
    store = CVStore(db_path=path); await store.init()
    assert (await store.get("old1"))["has_photo"] == 0
    assert await store.set_photo("old1", b"x")


# ── endpoints ─────────────────────────────────────────────────────

def test_upload_sets_the_photo_and_it_shows_in_every_template_and_the_pdf(client):
    created = _create(client)
    assert created["has_photo"] is False
    r = _upload(client, created)
    assert r.status_code == 200 and r.json()["has_photo"] is True
    assert client.get(f"/cv/{created['id']}").json()["has_photo"] is True
    for template in TEMPLATES:
        html = client.get(f"/cv/{created['id']}.html", params={"template": template}).text
        assert DATA_URI_PREFIX in html and 'class="photo"' in html
    assert client.get(f"/cv/{created['id']}.pdf").content.startswith(b"%PDF")


def test_the_photo_bytes_are_never_returned_by_json_or_markdown(client):
    created = _create(client); _upload(client, created)
    assert "base64" not in client.get(f"/cv/{created['id']}").text
    assert "base64" not in client.get(f"/cv/{created['id']}.md").text


def test_a_cv_without_a_photo_renders_exactly_as_before(client):
    created = _create(client)
    assert 'class="photo"' not in client.get(f"/cv/{created['id']}.html").text


def test_wrong_token_is_403_and_changes_nothing(client):
    created = _create(client)
    assert _upload(client, created, token="wrong").status_code == 403
    assert client.get(f"/cv/{created['id']}").json()["has_photo"] is False


def test_missing_token_is_422(client):
    created = _create(client)
    r = client.post(f"/cv/{created['id']}/photo", files={"file": ("a.jpg", _jpeg(), "image/jpeg")})
    assert r.status_code == 422


def test_another_cvs_token_does_not_work(client):
    a, b = _create(client), _create(client)
    assert _upload(client, b, token=a["edit_token"]).status_code == 403


def test_unknown_cv_is_404(client):
    r = client.post("/cv/nope/photo", files={"file": ("a.jpg", _jpeg(), "image/jpeg")}, data={"edit_token": "x"})
    assert r.status_code == 404


def test_a_non_image_is_400_and_leaves_the_existing_photo_alone(client):
    created = _create(client); _upload(client, created)
    r = _upload(client, created, data=b"<svg onload=alert(1)>")
    assert r.status_code == 400 and r.json()["detail"]["code"] == "bad_image"
    assert client.get(f"/cv/{created['id']}").json()["has_photo"] is True


def test_oversize_is_413(client, monkeypatch):
    monkeypatch.setattr(app_module, "CV_PHOTO_MAX_BYTES", 500)
    r = _upload(client, _create(client), data=_jpeg((400, 400)))
    assert r.status_code == 413 and r.json()["detail"]["code"] == "photo_too_large"


def test_a_new_photo_replaces_the_old_one(client):
    created = _create(client)
    _upload(client, created, data=_jpeg(color=(255, 0, 0)))
    first = asyncio.run(app_module._cv_store.get_photo(created["id"]))
    _upload(client, created, data=_jpeg(color=(0, 0, 255)))
    second = asyncio.run(app_module._cv_store.get_photo(created["id"]))
    assert first != second


def test_remove_takes_the_token_in_a_header_and_clears_the_photo(client):
    created = _create(client); _upload(client, created)
    assert client.delete(f"/cv/{created['id']}/photo", headers={"X-Edit-Token": "wrong"}).status_code == 403
    assert client.get(f"/cv/{created['id']}").json()["has_photo"] is True
    r = client.delete(f"/cv/{created['id']}/photo", headers={"X-Edit-Token": created["edit_token"]})
    assert r.status_code == 200 and r.json()["has_photo"] is False
    assert 'class="photo"' not in client.get(f"/cv/{created['id']}.html").text
    assert client.delete(f"/cv/{created['id']}/photo").status_code == 403  # no header at all


def test_editing_the_profile_keeps_the_photo(client, monkeypatch):
    created = _create(client); _upload(client, created)

    async def fake_edit(current, instructions, model_name):
        return current.model_copy(update={"skills": [*current.skills, "Rust"]})

    monkeypatch.setattr(app_module, "edit_cv_profile", fake_edit)
    r = client.post(f"/cv/{created['id']}/edit", json={"instructions": "add Rust", "edit_token": created["edit_token"]})
    assert r.status_code == 200 and r.json()["has_photo"] is True
    assert DATA_URI_PREFIX in client.get(f"/cv/{created['id']}.html").text
