import asyncio

import pytest
from fastapi.testclient import TestClient

import src.service.app as app_module
from src.report.schema import Pillar, Report, ReportMeta
from src.service.db import JobStore


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "_store", JobStore(db_path=str(tmp_path / "jobs.db")))
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


def test_get_rendered_pdf_unavailable_is_503(client, monkeypatch):
    def raise_import_error(report, include_footer=True):
        raise ImportError("cannot load library 'libgobject-2.0-0'")

    monkeypatch.setitem(app_module.RENDERERS, "pdf", raise_import_error)
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


def test_public_kinds_unset_leaves_every_kind_reachable(client):
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
