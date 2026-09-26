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


@pytest.mark.asyncio
async def test_rendered_before_done_is_409(client):
    # Create the job record directly, without scheduling the background task,
    # so it stays "pending" without ever running (an actually-async-sleeping
    # background task would block TestClient, which runs tasks synchronously).
    job_id = await app_module._store.create_job(kind="website_audit", subject="https://example.com")
    resp = client.get(f"/reports/{job_id}.md")
    assert resp.status_code == 409
