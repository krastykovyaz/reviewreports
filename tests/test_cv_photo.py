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


def test_the_pixel_limit_is_16_megapixels():
    assert photo_mod.MAX_PIXELS == 16_000_000
    b = BytesIO(); Image.new("L", (4001, 4000), 0).save(b, "PNG")  # one row over; a tiny file
    with pytest.raises(ValueError):
        process_cv_photo(b.getvalue())
    b = BytesIO(); Image.new("L", (4000, 4000), 0).save(b, "PNG")
    assert _open(process_cv_photo(b.getvalue())).size == (photo_mod.SIZE, photo_mod.SIZE)


@pytest.mark.parametrize("orientation", range(2, 9))
def test_orientation_is_baked_in_the_same_way_as_exif_transpose_does_it(orientation):
    from PIL import ImageDraw, ImageOps
    im = Image.new("RGB", (2400, 1600), (255, 255, 255))
    ImageDraw.Draw(im).rectangle((0, 0, 700, 500), fill=(255, 0, 0))  # a marker in one corner shows where the picture ended up
    exif = Image.Exif(); exif[0x0112] = orientation
    b = BytesIO(); im.save(b, "JPEG", exif=exif); raw = b.getvalue()  # 2400x1600: reduced by the JPEG decoder on the way in
    expect = ImageOps.fit(ImageOps.exif_transpose(Image.open(BytesIO(raw))), (photo_mod.SIZE, photo_mod.SIZE), Image.LANCZOS, centering=(0.5, 0.4))
    got = _open(process_cv_photo(raw)).convert("RGB")
    for xy in ((20, 20), (460, 20), (20, 460), (460, 460)):
        assert abs(got.getpixel(xy)[1] - expect.convert("RGB").getpixel(xy)[1]) < 40, (orientation, xy)  # red corner or white corner, same as the reference


_PEAK = r"""
import re, sys
sys.path.insert(0, ".")
raw = sys.stdin.buffer.read()
from src.cv.photo import process_cv_photo
process_cv_photo(raw)
# VmHWM, not getrusage: ru_maxrss of a child starts at its parent's size (this test process is big) and would hide the difference
print(int(re.search(r"VmHWM:\s+(\d+) kB", open("/proc/self/status").read()).group(1)) // 1024)
"""


@pytest.mark.parametrize("mode,fmt,ceiling_mb", [
    ("RGBA", "PNG", 180),   # the largest picture allowed; the old pipeline peaked at ~260 MB on it (a 6300x6300 one: ~620 MB)
    ("RGB", "JPEG", 70),    # the decoder scales a JPEG down while reading it: ~28 MB; the old pipeline decoded all 16 MP (~140 MB)
])
def test_the_largest_allowed_picture_does_not_balloon_memory(mode, fmt, ceiling_mb):
    import pathlib, subprocess, sys
    buf = BytesIO(); Image.new(mode, (4000, 4000), (30, 120, 200, 128) if mode == "RGBA" else (30, 120, 200)).save(buf, fmt)
    # measured in a fresh process (peak RSS only ever grows, so it can't be told apart from earlier tests' in this one)
    out = subprocess.run([sys.executable, "-c", _PEAK], input=buf.getvalue(), capture_output=True, timeout=60,
                         cwd=str(pathlib.Path(photo_mod.__file__).resolve().parents[2]))
    assert out.returncode == 0, out.stderr
    assert int(out.stdout.strip()) < ceiling_mb


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


# ── the upload endpoint decodes under a gate, like every other heavy job ──

def test_a_slow_decode_times_out_but_keeps_its_slot_until_the_thread_ends(client, monkeypatch):
    import threading, time
    created = _create(client)
    release = threading.Event()
    monkeypatch.setattr(app_module, "_DOC_TIMEOUT_SEC", 0.3)
    monkeypatch.setattr(app_module, "process_cv_photo", lambda raw: (release.wait(5), b"x")[1])
    before = app_module._photo_gate._value
    assert _upload(client, created).status_code == 400
    assert app_module._photo_gate._value == before - 1   # still occupied by the running decode
    release.set(); time.sleep(0.4)
    assert app_module._photo_gate._value == before       # freed when it finished


def test_photo_decodes_are_limited_to_two_at_a_time_and_the_rest_get_503(client, monkeypatch):
    import threading, time
    created = _create(client)
    release = threading.Event()
    monkeypatch.setattr(app_module, "_DOC_TIMEOUT_SEC", 0.2)
    monkeypatch.setattr(app_module, "process_cv_photo", lambda raw: (release.wait(5), b"x")[1])

    async def quick_timeout_wait(coro, timeout):  # a full gate: don't really wait the 10 s for a slot
        if getattr(coro, "__qualname__", "").startswith("Semaphore.acquire") and app_module._photo_gate.locked():
            coro.close()
            raise asyncio.TimeoutError
        return await real_wait_for(coro, timeout)

    real_wait_for = asyncio.wait_for
    monkeypatch.setattr(app_module.asyncio, "wait_for", quick_timeout_wait)
    codes = [_upload(client, created).status_code for _ in range(4)]
    release.set(); time.sleep(0.4)
    assert codes == [400, 400, 503, 503]


def test_the_decode_runs_off_the_event_loop(client, monkeypatch):
    import threading
    created = _create(client)
    seen = {}

    def spy(raw):
        seen["thread"] = threading.current_thread()
        return process_cv_photo(raw)

    monkeypatch.setattr(app_module, "process_cv_photo", spy)
    assert _upload(client, created).status_code == 200
    assert seen["thread"] is not threading.main_thread()
