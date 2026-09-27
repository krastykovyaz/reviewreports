import pytest
from fastapi.testclient import TestClient

import src.service.app as app_module
from src.service.cv_store import CVStore

_SAMPLE_PAYLOAD = {
    "name": "Jordan Reyes",
    "role": "Senior Backend Engineer",
    "contact": {"email": "jordan.reyes@email.com", "phone": "+1 415 555 0142"},
    "experience": [
        {"title": "Senior Backend Engineer", "organization": "Northwind Systems", "start": "2021", "end": "Present", "bullets": ["Redesigned the payments pipeline."]},
    ],
    "education": [{"degree": "B.S. Computer Science", "institution": "UC San Diego", "year": "2016"}],
    "skills": ["Python", "Go"],
}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "_cv_store", CVStore(db_path=str(tmp_path / "cv.db")))
    with TestClient(app_module.app) as c:
        yield c


def test_create_cv_defaults_to_modern_template(client):
    resp = client.post("/cv", json=_SAMPLE_PAYLOAD)
    assert resp.status_code == 200
    body = resp.json()
    assert body["template"] == "modern"
    assert body["profile"]["name"] == "Jordan Reyes"
    assert "id" in body


def test_create_cv_rejects_unknown_template(client):
    resp = client.post("/cv", json={**_SAMPLE_PAYLOAD, "template": "brutalist"})
    assert resp.status_code == 400


def test_create_cv_requires_name_and_contact(client):
    resp = client.post("/cv", json={"role": "Engineer"})
    assert resp.status_code == 422


def test_get_cv_roundtrips(client):
    cv_id = client.post("/cv", json={**_SAMPLE_PAYLOAD, "template": "classic"}).json()["id"]
    resp = client.get(f"/cv/{cv_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["template"] == "classic"
    assert body["profile"]["contact"]["email"] == "jordan.reyes@email.com"


def test_get_unknown_cv_is_404(client):
    resp = client.get("/cv/does-not-exist")
    assert resp.status_code == 404


def test_get_rendered_html_uses_stored_template(client):
    cv_id = client.post("/cv", json={**_SAMPLE_PAYLOAD, "template": "compact"}).json()["id"]
    resp = client.get(f"/cv/{cv_id}.html")
    assert resp.status_code == 200
    assert "Jordan Reyes" in resp.text
    assert "Northwind Systems" in resp.text


def test_get_rendered_html_can_override_template_via_query_param(client):
    cv_id = client.post("/cv", json={**_SAMPLE_PAYLOAD, "template": "modern"}).json()["id"]
    resp = client.get(f"/cv/{cv_id}.html", params={"template": "classic"})
    assert resp.status_code == 200
    assert "Georgia" in resp.text  # classic's serif font-family, absent from modern/compact


def test_get_rendered_html_rejects_unknown_template_override(client):
    cv_id = client.post("/cv", json=_SAMPLE_PAYLOAD).json()["id"]
    resp = client.get(f"/cv/{cv_id}.html", params={"template": "brutalist"})
    assert resp.status_code == 400


def test_get_rendered_markdown(client):
    cv_id = client.post("/cv", json=_SAMPLE_PAYLOAD).json()["id"]
    resp = client.get(f"/cv/{cv_id}.md")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/markdown")
    assert "# Jordan Reyes" in resp.text


def test_get_rendered_pdf(client, monkeypatch):
    # Mocks the renderer itself (rather than requiring weasyprint's system
    # libs in every test environment), mirroring test_service_app.py's
    # test_get_rendered_pdf for the report endpoint.
    monkeypatch.setattr(app_module, "render_cv_pdf", lambda profile, template: b"%PDF-1.7 fake cv pdf")
    cv_id = client.post("/cv", json=_SAMPLE_PAYLOAD).json()["id"]
    resp = client.get(f"/cv/{cv_id}.pdf")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/pdf"
    assert resp.content == b"%PDF-1.7 fake cv pdf"


def test_get_rendered_pdf_unavailable_is_503(client, monkeypatch):
    def raise_import_error(profile, template):
        raise ImportError("cannot load library 'libgobject-2.0-0'")

    monkeypatch.setattr(app_module, "render_cv_pdf", raise_import_error)
    cv_id = client.post("/cv", json=_SAMPLE_PAYLOAD).json()["id"]
    resp = client.get(f"/cv/{cv_id}.pdf")
    assert resp.status_code == 503


def test_get_rendered_unsupported_extension_is_400(client):
    cv_id = client.post("/cv", json=_SAMPLE_PAYLOAD).json()["id"]
    resp = client.get(f"/cv/{cv_id}.docx")
    assert resp.status_code == 400


def test_get_rendered_unknown_cv_is_404(client):
    resp = client.get("/cv/does-not-exist.html")
    assert resp.status_code == 404
