import pytest
from fastapi.testclient import TestClient

import src.service.app as app_module
from src.cv.schema import Contact, CVProfile, Education, Experience
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


# ---- Web flow (GET /cv-builder -> POST /cv-builder -> GET /cv-builder/{id}) ----

_FORM_PAYLOAD = {
    "name": "Jordan Reyes",
    "role": "Senior Backend Engineer",
    "summary": "Backend engineer with 8 years building distributed systems.",
    "email": "jordan.reyes@email.com",
    "phone": "+1 415 555 0142",
    "location": "San Francisco, CA",
    "links": "linkedin.com/in/jordanreyes, github.com/jreyes",
    "skills": "Python, Go, PostgreSQL",
    "languages": "English (native), Spanish (fluent)",
}


def test_cv_builder_form_renders(client):
    resp = client.get("/cv-builder")
    assert resp.status_code == 200
    assert "CV builder" in resp.text
    assert 'name="name"' in resp.text
    assert 'name="email"' in resp.text


def test_cv_builder_form_localizes(client):
    resp = client.get("/cv-builder?lang=ru")
    assert resp.status_code == 200
    assert '<html lang="ru">' in resp.text
    assert "Конструктор резюме" in resp.text  # ui.cv.builder_title


def test_cv_builder_submit_requires_name_and_email(client):
    resp = client.post("/cv-builder", data={"role": "Engineer"})
    assert resp.status_code == 422


def test_cv_builder_submit_redirects_to_result_page(client):
    resp = client.post("/cv-builder", data=_FORM_PAYLOAD, follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"].startswith("/cv-builder/")


def test_cv_builder_submit_parses_scalar_and_csv_fields(client):
    resp = client.post("/cv-builder", data=_FORM_PAYLOAD, follow_redirects=False)
    cv_id = resp.headers["location"].split("/cv-builder/")[1].split("?")[0]

    profile = client.get(f"/cv/{cv_id}").json()["profile"]
    assert profile["name"] == "Jordan Reyes"
    assert profile["contact"]["email"] == "jordan.reyes@email.com"
    assert profile["contact"]["links"] == ["linkedin.com/in/jordanreyes", "github.com/jreyes"]
    assert profile["skills"] == ["Python", "Go", "PostgreSQL"]
    assert profile["languages"] == ["English (native)", "Spanish (fluent)"]


def test_cv_builder_submit_parses_experience_and_education_rows(client):
    data = {
        **_FORM_PAYLOAD,
        "exp_title": ["Senior Backend Engineer", "Backend Engineer"],
        "exp_organization": ["Northwind Systems", "Faircloud"],
        "exp_location": ["San Francisco, CA", ""],
        "exp_start": ["2021", "2017"],
        "exp_end": ["Present", "2021"],
        "exp_bullets": ["Redesigned the payments pipeline.\nMentored 4 engineers.", "Built the billing service."],
        "edu_degree": ["B.S. Computer Science"],
        "edu_institution": ["UC San Diego"],
        "edu_year": ["2016"],
    }
    resp = client.post("/cv-builder", data=data, follow_redirects=False)
    cv_id = resp.headers["location"].split("/cv-builder/")[1].split("?")[0]

    profile = client.get(f"/cv/{cv_id}").json()["profile"]
    assert len(profile["experience"]) == 2
    assert profile["experience"][0]["organization"] == "Northwind Systems"
    assert profile["experience"][0]["bullets"] == ["Redesigned the payments pipeline.", "Mentored 4 engineers."]
    assert profile["experience"][1]["end"] == "2021"
    assert profile["education"][0]["institution"] == "UC San Diego"


def test_cv_builder_submit_skips_blank_experience_and_education_rows(client):
    # Mirrors an untouched "+ Add another job/degree" row (all fields blank) -
    # must not create an empty Experience/Education entry.
    data = {
        **_FORM_PAYLOAD,
        "exp_title": ["Senior Backend Engineer", ""],
        "exp_organization": ["Northwind Systems", ""],
        "exp_location": ["", ""],
        "exp_start": ["2021", ""],
        "exp_end": ["Present", ""],
        "exp_bullets": ["", ""],
        "edu_degree": ["", "B.S. Computer Science"],
        "edu_institution": ["", "UC San Diego"],
        "edu_year": ["", "2016"],
    }
    resp = client.post("/cv-builder", data=data, follow_redirects=False)
    cv_id = resp.headers["location"].split("/cv-builder/")[1].split("?")[0]

    profile = client.get(f"/cv/{cv_id}").json()["profile"]
    assert len(profile["experience"]) == 1
    assert len(profile["education"]) == 1
    assert profile["education"][0]["institution"] == "UC San Diego"


def test_cv_builder_result_page_shows_all_three_templates(client):
    resp = client.post("/cv-builder", data=_FORM_PAYLOAD, follow_redirects=True)
    assert resp.status_code == 200
    assert "Jordan Reyes" not in resp.text  # embedded via iframe src, not inlined
    for template in ("Modern", "Classic", "Compact"):
        assert template in resp.text
    assert "/cv/" in resp.text and ".pdf?template=" in resp.text


def test_cv_builder_result_page_localizes(client):
    resp = client.post("/cv-builder", data={**_FORM_PAYLOAD, "lang": "fr"}, follow_redirects=True)
    assert resp.status_code == 200
    assert '<html lang="fr">' in resp.text
    assert "Comparer les modèles" in resp.text  # ui.cv.result_title


def test_cv_builder_result_page_unknown_id_is_404(client):
    resp = client.get("/cv-builder/does-not-exist")
    assert resp.status_code == 404


# ---- Paste-your-info mode (POST /cv-builder/from-text) ------------------------


def test_from_text_prefills_manual_form_on_success(client, monkeypatch):
    profile = CVProfile(
        name="Jordan Reyes",
        role="Senior Backend Engineer",
        contact=Contact(email="jordan.reyes@email.com"),
        experience=[Experience(title="Senior Backend Engineer", organization="Northwind Systems", start="2021", end="Present", bullets=["Redesigned the payments pipeline."])],
        education=[Education(degree="B.S. Computer Science", institution="UC San Diego", year="2016")],
        skills=["Python", "Go"],
    )

    async def fake_extract(text, model_name):
        assert model_name == "deepseek/deepseek-chat"
        return profile

    monkeypatch.setattr(app_module, "extract_cv_profile", fake_extract)
    resp = client.post("/cv-builder/from-text", data={"text": "Jordan Reyes is a senior backend engineer..."})
    assert resp.status_code == 200
    assert 'value="Jordan Reyes"' in resp.text
    assert 'value="jordan.reyes@email.com"' in resp.text
    assert "Northwind Systems" in resp.text  # seeded into the experience row via JS init data
    assert "review and correct" in resp.text.lower() or "Extracted from your text" in resp.text


def test_from_text_uses_provided_model_name(client, monkeypatch):
    async def fake_extract(text, model_name):
        assert model_name == "openai/gpt-4o"
        return CVProfile(name="Alex Kim", contact=Contact(email="alex@example.com"))

    monkeypatch.setattr(app_module, "extract_cv_profile", fake_extract)
    resp = client.post("/cv-builder/from-text", data={"text": "some bio", "model_name": "openai/gpt-4o"})
    assert resp.status_code == 200


def test_from_text_shows_error_on_extraction_failure(client, monkeypatch):
    async def fake_extract(text, model_name):
        return None

    monkeypatch.setattr(app_module, "extract_cv_profile", fake_extract)
    resp = client.post("/cv-builder/from-text", data={"text": "unparseable garbage"})
    assert resp.status_code == 422
    assert "Could not extract a CV" in resp.text
    assert "unparseable garbage" in resp.text  # pasted text preserved for retry


def test_from_text_requires_text(client):
    resp = client.post("/cv-builder/from-text", data={})
    assert resp.status_code == 422


# ---- JSON counterpart (POST /cv/from-text), used by tsech's "Create CV" mode ----


def test_cv_from_text_json_creates_and_stores(client, monkeypatch):
    async def fake_extract(text, model_name):
        assert model_name == "deepseek/deepseek-chat"
        return CVProfile(name="Jordan Reyes", role="Senior Backend Engineer", contact=Contact(email="jordan.reyes@email.com"), skills=["Python"])

    monkeypatch.setattr(app_module, "extract_cv_profile", fake_extract)
    resp = client.post("/cv/from-text", json={"text": "Jordan Reyes is a senior backend engineer..."})
    assert resp.status_code == 200
    body = resp.json()
    assert body["template"] == "modern"
    assert body["profile"]["name"] == "Jordan Reyes"
    assert client.get(f"/cv/{body['id']}").json()["profile"]["contact"]["email"] == "jordan.reyes@email.com"


def test_cv_from_text_json_forwards_model_and_template(client, monkeypatch):
    async def fake_extract(text, model_name):
        assert model_name == "openai/gpt-4o"
        return CVProfile(name="Alex Kim", contact=Contact(email="alex@example.com"))

    monkeypatch.setattr(app_module, "extract_cv_profile", fake_extract)
    resp = client.post("/cv/from-text", json={"text": "some bio", "model_name": "openai/gpt-4o", "template": "compact"})
    assert resp.status_code == 200
    assert resp.json()["template"] == "compact"


def test_cv_from_text_json_extraction_failure_is_422(client, monkeypatch):
    async def fake_extract(text, model_name):
        return None

    monkeypatch.setattr(app_module, "extract_cv_profile", fake_extract)
    resp = client.post("/cv/from-text", json={"text": "unparseable garbage"})
    assert resp.status_code == 422


def test_cv_from_text_json_rejects_blank_text_without_calling_the_model(client, monkeypatch):
    called = []

    async def fake_extract(text, model_name):
        called.append(text)
        return CVProfile(name="X", contact=Contact(email="x@example.com"))

    monkeypatch.setattr(app_module, "extract_cv_profile", fake_extract)
    assert client.post("/cv/from-text", json={"text": "   "}).status_code == 422
    assert called == []


def test_cv_from_text_json_rejects_unknown_template(client):
    resp = client.post("/cv/from-text", json={"text": "some bio", "template": "brutalist"})
    assert resp.status_code == 400


# ---- Describe-changes edit flow (POST /cv/{id}/edit) --------------------------


def test_edit_cv_applies_change_and_keeps_id_and_template(client, monkeypatch):
    cv_id = client.post("/cv", json={**_SAMPLE_PAYLOAD, "template": "classic"}).json()["id"]

    async def fake_edit(current, instructions, model_name):
        assert current.name == "Jordan Reyes"
        assert instructions == "Add Kubernetes to skills"
        assert model_name == "deepseek/deepseek-chat"
        return current.model_copy(update={"skills": [*current.skills, "Kubernetes"]})

    monkeypatch.setattr(app_module, "edit_cv_profile", fake_edit)
    resp = client.post(f"/cv/{cv_id}/edit", json={"instructions": "Add Kubernetes to skills"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == cv_id  # same id/link, not a new CV
    assert body["template"] == "classic"  # untouched
    assert body["profile"]["skills"] == ["Python", "Go", "Kubernetes"]

    # persisted, not just returned
    assert client.get(f"/cv/{cv_id}").json()["profile"]["skills"] == ["Python", "Go", "Kubernetes"]


def test_edit_cv_forwards_model_name(client, monkeypatch):
    cv_id = client.post("/cv", json=_SAMPLE_PAYLOAD).json()["id"]

    async def fake_edit(current, instructions, model_name):
        assert model_name == "openai/gpt-4o"
        return current

    monkeypatch.setattr(app_module, "edit_cv_profile", fake_edit)
    resp = client.post(f"/cv/{cv_id}/edit", json={"instructions": "tighten the summary", "model_name": "openai/gpt-4o"})
    assert resp.status_code == 200


def test_edit_cv_unknown_id_is_404_without_calling_the_model(client, monkeypatch):
    called = []

    async def fake_edit(current, instructions, model_name):
        called.append(instructions)
        return current

    monkeypatch.setattr(app_module, "edit_cv_profile", fake_edit)
    resp = client.post("/cv/does-not-exist/edit", json={"instructions": "add a skill"})
    assert resp.status_code == 404
    assert called == []


def test_edit_cv_rejects_blank_instructions_without_calling_the_model(client, monkeypatch):
    cv_id = client.post("/cv", json=_SAMPLE_PAYLOAD).json()["id"]
    called = []

    async def fake_edit(current, instructions, model_name):
        called.append(instructions)
        return current

    monkeypatch.setattr(app_module, "edit_cv_profile", fake_edit)
    resp = client.post(f"/cv/{cv_id}/edit", json={"instructions": "   "})
    assert resp.status_code == 422
    assert called == []


def test_edit_cv_failure_is_422_and_does_not_overwrite_stored_profile(client, monkeypatch):
    cv_id = client.post("/cv", json=_SAMPLE_PAYLOAD).json()["id"]

    async def fake_edit(current, instructions, model_name):
        return None

    monkeypatch.setattr(app_module, "edit_cv_profile", fake_edit)
    resp = client.post(f"/cv/{cv_id}/edit", json={"instructions": "add a skill"})
    assert resp.status_code == 422
    assert client.get(f"/cv/{cv_id}").json()["profile"]["skills"] == ["Python", "Go"]
