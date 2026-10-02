"""Regression tests for the 2026-10-02 validation review's service-layer findings."""

import os

import pytest
from fastapi.testclient import TestClient

import src.service.app as app_module
from src.report.schema import Pillar, Report, ReportMeta
from src.service import input_policy
from src.service.cv_store import CVStore
from src.service.db import JobStatus, JobStore
from src.service.input_policy import InputRejected, validate_network_input


async def _noop_async(*args, **kwargs):
    return None


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "_store", JobStore(db_path=str(tmp_path / "jobs.db")))
    monkeypatch.setattr(app_module, "_cv_store", CVStore(db_path=str(tmp_path / "cv.db")))
    monkeypatch.setattr(app_module, "_UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setattr(app_module.model_manager, "initialize", _noop_async)
    monkeypatch.delenv("REVIEW_ALLOWED_ROOTS", raising=False)

    async def fake_generate_report(kind, input, model_name=None, lang="en"):
        return Report(meta=ReportMeta(kind=kind, subject=input, lang=lang), pillars=[Pillar(name="SEO", score=6, summary="ok")]).finalize()

    monkeypatch.setattr(app_module, "generate_report", fake_generate_report)
    with TestClient(app_module.app) as c:
        yield c


# ---- arbitrary server path read -------------------------------------------------


@pytest.mark.parametrize(
    "kind,url",
    [
        ("app_review", "/etc"),
        ("code_review", "/etc"),
        ("code_review", "../../etc"),
        ("resume_review", "/etc/passwd"),
        ("presentation_review", "/etc/hosts"),
        ("book_review", "/etc/hosts"),
    ],
)
def test_network_callers_cannot_point_kinds_at_server_paths(client, kind, url):
    resp = client.post("/reports", json={"url": url, "kind": kind})
    assert resp.status_code == 400


def test_rejection_does_not_reveal_whether_a_path_exists(client, tmp_path):
    real = client.post("/reports", json={"url": str(tmp_path), "kind": "app_review"})
    fake = client.post("/reports", json={"url": str(tmp_path / "nope"), "kind": "app_review"})
    assert real.status_code == fake.status_code == 400
    assert real.json()["detail"] == fake.json()["detail"]


@pytest.mark.parametrize("url", ["--upload-pack=touch /tmp/x;.git", "file:///etc/passwd", "ext::sh -c id .git", "/srv/private.git", "git@github.com:o/r.git"])
def test_code_review_rejects_everything_but_public_http_urls(client, url):
    assert client.post("/reports", json={"url": url, "kind": "code_review"}).status_code == 400


@pytest.mark.parametrize("url", ["http://127.0.0.1:9/x.git", "http://169.254.169.254/x.git", "http://10.0.0.5/x.git", "http://localhost/x.git"])
def test_code_review_rejects_internal_git_hosts(client, url):
    assert client.post("/reports", json={"url": url, "kind": "code_review"}).status_code == 400


def test_code_review_accepts_a_public_git_url(client, monkeypatch):
    monkeypatch.setattr(input_policy, "assert_public_url", _noop_async)
    resp = client.post("/reports", json={"url": "https://example.com/org/repo", "kind": "code_review"})
    assert resp.status_code == 200


def test_website_audit_is_still_accepted(client):
    # SSRF for this kind is enforced in the fetch layer (tests/test_http_client.py).
    assert client.post("/reports", json={"url": "https://example.com", "kind": "website_audit"}).status_code == 200


@pytest.mark.asyncio
async def test_operator_can_allow_a_directory(tmp_path, monkeypatch):
    allowed = tmp_path / "workspaces"
    (allowed / "app1").mkdir(parents=True)
    other = tmp_path / "other"
    other.mkdir()
    monkeypatch.setenv("REVIEW_ALLOWED_ROOTS", str(allowed))

    await validate_network_input("app_review", str(allowed / "app1"))  # inside the root: fine
    with pytest.raises(InputRejected):
        await validate_network_input("app_review", str(other))
    with pytest.raises(InputRejected):  # ".." out of the root is resolved before comparing
        await validate_network_input("app_review", str(allowed / "app1" / ".." / ".." / "other"))


@pytest.mark.asyncio
async def test_symlink_out_of_an_allowed_root_is_rejected(tmp_path, monkeypatch):
    allowed = tmp_path / "workspaces"
    allowed.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    os.symlink(outside, allowed / "link")
    monkeypatch.setenv("REVIEW_ALLOWED_ROOTS", str(allowed))
    with pytest.raises(InputRejected):
        await validate_network_input("app_review", str(allowed / "link"))


def test_submit_form_applies_the_same_rule(client):
    resp = client.post("/submit", data={"kind": "app_review", "input_value": "/etc"})
    assert resp.status_code == 400


# ---- uploads --------------------------------------------------------------------


def test_oversized_upload_is_rejected_and_not_written(client, tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "MAX_UPLOAD_BYTES", 100)
    resp = client.post("/submit", data={"kind": "resume_review"}, files={"file": ("cv.txt", b"x" * 500, "text/plain")}, follow_redirects=False)
    assert resp.status_code == 413
    assert not os.path.isdir(tmp_path / "uploads") or os.listdir(tmp_path / "uploads") == []


def test_upload_within_the_limit_is_accepted(client, monkeypatch):
    monkeypatch.setattr(app_module, "MAX_UPLOAD_BYTES", 100)
    resp = client.post("/submit", data={"kind": "resume_review"}, files={"file": ("cv.txt", b"x" * 50, "text/plain")}, follow_redirects=False)
    assert resp.status_code == 303


# ---- CV builder form ------------------------------------------------------------


@pytest.mark.parametrize("data", [{"name": "   ", "email": "a@b.c"}, {"name": "Alex", "email": "   "}, {"name": "", "email": ""}])
def test_cv_builder_rejects_blank_name_or_email(client, data):
    assert client.post("/cv-builder", data=data, follow_redirects=False).status_code == 422


# ---- lifespan -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unfinished_jobs_are_failed_on_startup(tmp_path):
    store = JobStore(db_path=str(tmp_path / "jobs.db"))
    await store.init()
    pending = await store.create_job(kind="website_audit", subject="https://a.example")
    running = await store.create_job(kind="website_audit", subject="https://b.example")
    await store.mark_running(running)
    done = await store.create_job(kind="website_audit", subject="https://c.example")
    await store.mark_done(done, {"ok": True})

    assert await store.fail_unfinished("restarted") == 2
    assert (await store.get_job(pending))["status"] == JobStatus.FAILED.value
    assert (await store.get_job(running))["error"] == "restarted"
    assert (await store.get_job(done))["status"] == JobStatus.DONE.value  # terminal jobs untouched


def test_lifespan_initializes_the_model_manager(tmp_path, monkeypatch):
    # Delete the initialize() call in app.py's lifespan and the suite used to stay
    # green while every real LLM call failed with "Model X not found. Available models: []".
    calls = []

    async def recording_initialize():
        calls.append(True)

    monkeypatch.setattr(app_module, "_store", JobStore(db_path=str(tmp_path / "jobs.db")))
    monkeypatch.setattr(app_module, "_cv_store", CVStore(db_path=str(tmp_path / "cv.db")))
    monkeypatch.setattr(app_module, "_UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setattr(app_module.model_manager, "initialize", recording_initialize)
    with TestClient(app_module.app):
        pass
    assert calls == [True]


def test_stuck_job_is_failed_when_the_service_starts(tmp_path, monkeypatch):
    import asyncio

    store = JobStore(db_path=str(tmp_path / "jobs.db"))
    asyncio.run(store.init())
    job_id = asyncio.run(store.create_job(kind="website_audit", subject="https://a.example"))
    monkeypatch.setattr(app_module, "_store", store)
    monkeypatch.setattr(app_module, "_cv_store", CVStore(db_path=str(tmp_path / "cv.db")))
    monkeypatch.setattr(app_module, "_UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setattr(app_module.model_manager, "initialize", _noop_async)
    with TestClient(app_module.app) as c:
        body = c.get(f"/reports/{job_id}").json()
    assert body["status"] == "failed"
    assert "restarted" in body["error"]
