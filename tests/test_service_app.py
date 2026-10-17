import asyncio

import pytest
from fastapi.testclient import TestClient

import src.service.app as app_module
from src.report.schema import Pillar, Report, ReportMeta
from src.service.cv_store import CVStore
from src.service.db import JobStore
from tests.helpers import run


async def _noop_async(*args, **kwargs):
    return None


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "_store", JobStore(db_path=str(tmp_path / "jobs.db")))
    # Without these, every test run opened the real workdir/service/cv.db and ran the
    # real model_manager.initialize() against the developer's .env.
    monkeypatch.setattr(app_module, "_cv_store", CVStore(db_path=str(tmp_path / "cv.db")))
    monkeypatch.setattr(app_module.model_manager, "initialize", _noop_async)
    # Redirect uploads to a temp dir so tests never write into the real project workdir.
    monkeypatch.setattr(app_module, "_UPLOAD_DIR", str(tmp_path / "uploads"))
    # Assert the "unrestricted" baseline explicitly rather than assume
    # os.getenv("PUBLIC_KINDS") is unset: src.model.manager calls
    # load_dotenv() at import time (for an unrelated reason - the model
    # provider keys), which discovers this repo's real .env by walking up
    # from cwd and loads it into os.environ regardless of what the test
    # actually wants - including this repo's real, restrictive
    # PUBLIC_KINDS=website_audit once that line exists there for
    # production. Individual tests below still override this via their own
    # monkeypatch to exercise the restricted case.
    monkeypatch.setattr(app_module, "PUBLIC_KINDS", None)

    async def fake_generate_report(kind, input, model_name=None, lang="en"):
        return Report(
            meta=ReportMeta(kind=kind, subject=input, lang=lang),
            pillars=[Pillar(name="SEO", score=6, summary="ok")],
        ).finalize()

    monkeypatch.setattr(app_module, "generate_report", fake_generate_report)

    with TestClient(app_module.app) as c:
        yield c


def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_home_page_renders_form(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "reviewreports" in resp.text
    assert 'name="kind"' in resp.text
    assert "resume_review" in resp.text
    assert "cv-builder?lang=en" in resp.text  # the CV nav link renders; mount-prefix agnostic


def test_home_page_in_russian(client):
    resp = client.get("/?lang=ru")
    assert resp.status_code == 200
    assert '<html lang="ru">' in resp.text
    assert "Сформировать отчёт" in resp.text  # ui.generate
    assert "Тип отчёта" in resp.text  # ui.report_kind


def test_home_page_unknown_lang_falls_back_to_english(client):
    resp = client.get("/?lang=de")
    assert resp.status_code == 200
    assert '<html lang="en">' in resp.text


def test_submit_url_kind_redirects_to_view(client):
    resp = client.post("/submit", data={"kind": "website_audit", "input_value": "https://example.com"}, follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"].startswith("/view/")


def test_submit_then_view_shows_finished_report(client):
    resp = client.post("/submit", data={"kind": "website_audit", "input_value": "https://example.com"}, follow_redirects=True)
    assert resp.status_code == 200
    assert 'id="reportFrame"' in resp.text  # the report viewer only renders once the job is done
    assert "Download PDF" in resp.text
    assert "/reports/" in resp.text and ".pdf" in resp.text


def test_submit_with_lang_persists_and_localizes_view_and_report(client):
    resp = client.post("/submit", data={"kind": "website_audit", "input_value": "https://example.com", "lang": "fr"}, follow_redirects=True)
    assert resp.status_code == 200
    assert '<html lang="fr">' in resp.text
    assert "Audit de site (URL)" in resp.text  # localized meta title (ui.kind.website_audit)
    assert "Télécharger le PDF" in resp.text  # ui.download_pdf

    job_id = resp.url.path.split("/view/")[1]
    job = client.get(f"/reports/{job_id}").json()
    assert job["lang"] == "fr"
    assert job["report"]["meta"]["lang"] == "fr"


def test_submit_url_kind_without_input_is_rejected(client):
    resp = client.post("/submit", data={"kind": "website_audit", "input_value": ""})
    assert resp.status_code == 400


def test_submit_file_kind_without_file_is_rejected(client):
    resp = client.post("/submit", data={"kind": "resume_review"})
    assert resp.status_code == 400


def test_submit_file_kind_saves_upload_and_redirects(client, tmp_path):
    resp = client.post(
        "/submit",
        data={"kind": "resume_review"},
        files={"file": ("resume.txt", b"Jane Doe, Software Engineer", "text/plain")},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    job_id = resp.headers["location"].split("/view/")[1]

    # confirm the job's subject is a saved file path containing the original filename
    job = client.get(f"/reports/{job_id}").json()
    assert job["subject"].endswith("resume.txt")
    import os

    assert os.path.isfile(job["subject"])


def test_submit_unsupported_kind_is_rejected(client):
    resp = client.post("/submit", data={"kind": "video_review", "input_value": "x"})
    assert resp.status_code == 400


def test_view_unknown_job_is_404(client):
    resp = client.get("/view/does-not-exist")
    assert resp.status_code == 404


def test_create_and_poll_job_until_done(client):
    resp = client.post("/reports", json={"url": "https://example.com"})
    assert resp.status_code == 200
    job_id = resp.json()["id"]
    assert resp.json()["status"] in ("pending", "running", "done")

    # BackgroundTasks run synchronously within the TestClient's request/response cycle.
    resp = client.get(f"/reports/{job_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "done"
    assert body["report"]["overall_score"] == 6.0


def test_create_report_with_lang_persists_on_job_and_report(client):
    job_id = client.post("/reports", json={"url": "https://example.com", "lang": "ru"}).json()["id"]
    body = client.get(f"/reports/{job_id}").json()
    assert body["lang"] == "ru"
    assert body["report"]["meta"]["lang"] == "ru"


def test_get_rendered_markdown(client):
    job_id = client.post("/reports", json={"url": "https://example.com"}).json()["id"]
    resp = client.get(f"/reports/{job_id}.md")
    assert resp.status_code == 200
    assert "# Website Audit Report" in resp.text


def test_get_rendered_html(client):
    job_id = client.post("/reports", json={"url": "https://example.com"}).json()["id"]
    resp = client.get(f"/reports/{job_id}.html")
    assert resp.status_code == 200
    assert "<table>" in resp.text


def test_get_rendered_pdf(client, monkeypatch):
    # Mocks the renderer itself (rather than requiring weasyprint's system
    # libs in every test environment) to verify the endpoint's own plumbing:
    # binary response, correct media type, ImportError -> 503.
    monkeypatch.setitem(app_module.RENDERERS, "pdf", lambda report, include_footer=True: b"%PDF-1.7 fake pdf bytes")
    job_id = client.post("/reports", json={"url": "https://example.com"}).json()["id"]
    resp = client.get(f"/reports/{job_id}.pdf")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/pdf"
    assert resp.content == b"%PDF-1.7 fake pdf bytes"


@pytest.mark.parametrize("exc_type", [ImportError, OSError])
def test_get_rendered_pdf_unavailable_is_503(client, monkeypatch, exc_type):
    # WeasyPrint without Pango raises OSError (from its ffi loader), not ImportError;
    # this test used to raise ImportError and so passed while the real failure was a 500.
    def raise_unavailable(report, include_footer=True):
        raise exc_type("cannot load library 'libgobject-2.0-0'")

    monkeypatch.setitem(app_module.RENDERERS, "pdf", raise_unavailable)
    job_id = client.post("/reports", json={"url": "https://example.com"}).json()["id"]
    resp = client.get(f"/reports/{job_id}.pdf")
    assert resp.status_code == 503


def test_unknown_job_is_404(client):
    resp = client.get("/reports/does-not-exist")
    assert resp.status_code == 404


def test_unsupported_kind_is_rejected(client):
    resp = client.post("/reports", json={"url": "https://example.com", "kind": "video_review"})
    assert resp.status_code == 400


# ---- Concurrency gate: bounding how many audits can run at once ---------------
#
# This service has no auth and each website_audit launches a headless
# Chromium (~300MB RAM); unbounded, a burst of submissions (accidental or
# not) can OOM the box. The gate rejects fast rather than queuing, since
# queuing would just delay the same exhaustion rather than prevent it.


@pytest.mark.asyncio
async def test_acquire_or_reject_raises_once_the_gate_is_full():
    gate = asyncio.Semaphore(1)
    await app_module._acquire_or_reject(gate)  # takes the only slot
    with pytest.raises(app_module.ServerBusy):
        await app_module._acquire_or_reject(gate)
    gate.release()
    await app_module._acquire_or_reject(gate)  # slot freed, no longer raises
    gate.release()


@pytest.mark.asyncio
async def test_run_job_marks_failed_when_the_audit_gate_is_full(client, monkeypatch):
    monkeypatch.setattr(app_module, "_audit_gate", asyncio.Semaphore(1))
    await app_module._audit_gate.acquire()  # simulate another audit already running
    try:
        job_id = await app_module._store.create_job(kind="website_audit", subject="https://example.com")
        await app_module._run_job(job_id, "website_audit", "https://example.com", None, "en")
        job = await app_module._store.get_job(job_id)
        assert job["status"] == "failed"
        assert "busy" in job["error"].lower()
    finally:
        app_module._audit_gate.release()


@pytest.mark.asyncio
async def test_run_job_releases_the_gate_after_finishing(client, monkeypatch):
    monkeypatch.setattr(app_module, "_audit_gate", asyncio.Semaphore(1))
    job_id = await app_module._store.create_job(kind="website_audit", subject="https://example.com")
    await app_module._run_job(job_id, "website_audit", "https://example.com", None, "en")
    assert not app_module._audit_gate.locked()  # released, not leaked


# ---- PUBLIC_KINDS: restricting which kinds public routes accept ----------------
#
# code_review/app_review take a server-side directory path (or, for
# code_review, a git URL) as input - this service is proxied straight
# through to a public domain, so a deployment that doesn't want that surface
# reachable sets PUBLIC_KINDS. Unset (the fixture's default, and every test
# above) must keep every supported kind reachable - that's the regression
# these guard against as much as the restriction itself.


def test_public_kinds_unset_leaves_every_kind_reachable(client, monkeypatch):
    # Reachable by KIND. (Local paths from the network are refused by the input policy unless the operator allows the
    # directory, so this test allows /etc explicitly: it is about PUBLIC_KINDS, not about that policy.)
    monkeypatch.setenv("REVIEW_ALLOWED_ROOTS", "/etc")
    resp = client.get("/")
    assert "code_review" in resp.text and "app_review" in resp.text
    assert client.post("/reports", json={"url": "/etc", "kind": "app_review"}).status_code == 200


def test_public_kinds_restricts_home_page_dropdown(client, monkeypatch):
    monkeypatch.setattr(app_module, "PUBLIC_KINDS", {"website_audit"})
    resp = client.get("/")
    assert "website_audit" in resp.text
    assert "code_review" not in resp.text
    assert "app_review" not in resp.text


def test_public_kinds_rejects_disallowed_kind_on_reports(client, monkeypatch):
    monkeypatch.setattr(app_module, "PUBLIC_KINDS", {"website_audit"})
    resp = client.post("/reports", json={"url": "/etc/passwd", "kind": "app_review"})
    assert resp.status_code == 400


def test_public_kinds_rejects_disallowed_kind_on_submit(client, monkeypatch):
    monkeypatch.setattr(app_module, "PUBLIC_KINDS", {"website_audit"})
    resp = client.post("/submit", data={"kind": "code_review", "input_value": "/etc"})
    assert resp.status_code == 400


def test_public_kinds_still_allows_the_allowed_kind(client, monkeypatch):
    monkeypatch.setattr(app_module, "PUBLIC_KINDS", {"website_audit"})
    resp = client.post("/reports", json={"url": "https://example.com", "kind": "website_audit"})
    assert resp.status_code == 200


def test_parse_public_kinds_splits_and_trims_commas():
    assert app_module._parse_public_kinds("website_audit, resume_review") == {"website_audit", "resume_review"}


def test_parse_public_kinds_unset_or_blank_means_unrestricted():
    assert app_module._parse_public_kinds(None) is None
    assert app_module._parse_public_kinds("") is None
    assert app_module._parse_public_kinds("  ,  ") is None


@pytest.mark.asyncio
async def test_rendered_before_done_is_409(client):
    # Create the job record directly, without scheduling the background task,
    # so it stays "pending" without ever running (an actually-async-sleeping
    # background task would block TestClient, which runs tasks synchronously).
    job_id = await app_module._store.create_job(kind="website_audit", subject="https://example.com")
    resp = client.get(f"/reports/{job_id}.md")
    assert resp.status_code == 409


# ── a refused model must not leave a pending job (or an uploaded file) behind ──

def _job_count():
    import sqlite3
    with sqlite3.connect(app_module._store.db_path) as db:
        return db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]


def test_reports_with_a_refused_model_is_400_and_creates_no_job(client):
    r = client.post("/reports", json={"url": "https://example.com", "kind": "website_audit", "model_name": "deepseek/deepseek-reasoner"})
    assert r.status_code == 400 and _job_count() == 0


def test_submit_with_a_refused_model_is_400_and_creates_no_job(client):
    r = client.post("/submit", data={"kind": "website_audit", "input_value": "https://example.com", "model_name": "deepseek/deepseek-reasoner"}, follow_redirects=False)
    assert r.status_code == 400 and _job_count() == 0


def test_submit_of_a_file_with_a_refused_model_stores_neither_job_nor_file(client, tmp_path):
    r = client.post("/submit", data={"kind": "resume_review", "model_name": "deepseek/deepseek-reasoner"},
                    files={"file": ("cv.pdf", b"%PDF-1.4 hello", "application/pdf")}, follow_redirects=False)
    assert r.status_code == 400 and _job_count() == 0
    assert not list((tmp_path / "uploads").glob("*"))


def test_an_allowed_model_still_creates_the_job(client, monkeypatch):
    monkeypatch.setenv("PUBLIC_MODELS", "openai/gpt-4o")
    monkeypatch.setattr(app_module, "validate_network_input", _noop_async)
    r = client.post("/reports", json={"url": "https://example.com", "kind": "website_audit", "model_name": "openai/gpt-4o"})
    assert r.status_code == 200 and _job_count() == 1


# ── what a person sees of a failure is a short generic message; the detail stays in the log ──

_LEAKY = "[Errno 111] connect to /root/reviewreports/workdir/.env at 10.1.2.3:5432 failed: sk-deepseek-secret"


def _fail_sync(client, monkeypatch, exc, caplog):
    """Submits a job whose generation raises `exc`; the TestClient runs the background task before returning."""
    async def boom(kind, input, model_name=None, lang="en"):
        raise exc

    monkeypatch.setattr(app_module, "generate_report", boom)
    with caplog.at_level("ERROR", logger="reviewreports"):
        return client.post("/reports", json={"url": "https://example.com"}).json()["id"]


@pytest.mark.parametrize("exc", [RuntimeError(_LEAKY), OSError(_LEAKY), ValueError(_LEAKY), KeyError(_LEAKY)])
def test_a_failed_job_shows_a_generic_error_everywhere(client, monkeypatch, caplog, exc):
    job_id = _fail_sync(client, monkeypatch, exc, caplog)
    for body in (client.get(f"/reports/{job_id}").json()["error"], client.get(f"/view/{job_id}").text, client.get(f"/reports/{job_id}.md").text):
        assert "10.1.2.3" not in body and "/root/" not in body and "sk-deepseek" not in body and "Errno" not in body
    err = client.get(f"/reports/{job_id}").json()["error"]
    assert err == "The report could not be generated. Please try again in a few minutes."
    assert _LEAKY in caplog.text  # the detail went to the server log, with its traceback
    assert client.get(f"/reports/{job_id}.md").status_code == 422


def test_network_failures_read_the_same_whether_blocked_or_unreachable(client, monkeypatch, caplog):
    import httpx
    from src.audit.http_client import SSRFBlocked
    msgs = set()
    for exc in (SSRFBlocked("Refusing to fetch a non-public address: intranet.corp -> 10.0.0.5"), httpx.ConnectError("[Errno 111] Connection refused 10.0.0.5:8080"),
                httpx.ReadTimeout("timed out"), ConnectionResetError("reset by 10.0.0.5")):
        job_id = _fail_sync(client, monkeypatch, exc, caplog)
        msgs.add(client.get(f"/reports/{job_id}").json()["error"])
    assert len(msgs) == 1 and "10.0.0.5" not in msgs.pop()


@pytest.mark.parametrize("exc_type", [ImportError, OSError])
def test_pdf_503_does_not_echo_the_library_error(client, monkeypatch, exc_type):
    def raise_unavailable(report, include_footer=True):
        raise exc_type("cannot load library '/usr/lib/x86_64-linux-gnu/libgobject-2.0.so.0'")

    monkeypatch.setitem(app_module.RENDERERS, "pdf", raise_unavailable)
    job_id = client.post("/reports", json={"url": "https://example.com"}).json()["id"]
    resp = client.get(f"/reports/{job_id}.pdf")
    assert resp.status_code == 503 and "libgobject" not in resp.text and "/usr/lib" not in resp.text


def test_the_cv_pdf_503_does_not_echo_the_library_error(client, monkeypatch):
    def broken(profile, template, photo=None):
        raise OSError("cannot load library '/usr/lib/libpango-1.0.so.0'")

    monkeypatch.setattr(app_module, "render_cv_pdf", broken)
    cv = client.post("/cv", json={"name": "A B", "contact": {"email": "a@b.co"}}).json()["id"]
    resp = client.get(f"/cv/{cv}.pdf")
    assert resp.status_code == 503 and "libpango" not in resp.text


@pytest.mark.asyncio
async def test_a_code_review_url_rejection_does_not_name_the_resolved_address(monkeypatch):
    from src.audit import http_client
    from src.service.input_policy import InputRejected, validate_network_input
    monkeypatch.setattr(http_client, "_resolve_all_sync", lambda host: ["10.9.8.7"])
    with pytest.raises(InputRejected) as e:
        await validate_network_input("code_review", "https://intranet.corp.example/repo.git")
    assert "10.9.8.7" not in str(e.value) and "intranet" not in str(e.value)
