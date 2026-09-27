import asyncio

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
    assert len(body["edit_token"]) >= 16  # opaque, but must actually be present and non-trivial


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


def test_get_cv_never_exposes_the_edit_token(client):
    # A CV's id is also its public share link - GET must not be a second way
    # to fetch the credential that /edit checks, or the id alone would be
    # enough to both read AND rewrite it, defeating the whole point.
    cv_id = client.post("/cv", json=_SAMPLE_PAYLOAD).json()["id"]
    assert "edit_token" not in client.get(f"/cv/{cv_id}").json()
    assert "edit_token" not in client.get(f"/cv/{cv_id}.html").text


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


def test_get_rendered_html_includes_og_meta_for_direct_sharing(client):
    # People share a /cv/{id}.html link directly, not just the
    # /cv-builder/{id} comparison page - it needs its own real preview card.
    cv_id = client.post("/cv", json={**_SAMPLE_PAYLOAD, "template": "classic"}).json()["id"]
    resp = client.get(f"/cv/{cv_id}.html", params={"template": "compact"})
    assert resp.status_code == 200
    assert '<meta property="og:title" content="Jordan Reyes — CV">' in resp.text
    assert '<meta property="og:description" content="Senior Backend Engineer' in resp.text
    assert '<meta property="og:image" content="https://tsech.online/logo.png">' in resp.text
    # Reflects the actually-requested template, not the CV's stored default.
    assert f'og:url" content="https://tsech.online/audit/cv/{cv_id}.html?template=compact">' in resp.text


def test_get_rendered_markdown_has_no_og_meta(client):
    # Meta injection only applies to the html branch; md/pdf are downloaded
    # documents, not link-preview targets.
    cv_id = client.post("/cv", json=_SAMPLE_PAYLOAD).json()["id"]
    resp = client.get(f"/cv/{cv_id}.md")
    assert "og:title" not in resp.text


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
    # The profile's name now appears in the page's own title/og:title (for a
    # real link preview in Telegram/WhatsApp/Facebook), but the actual CV
    # body - job history, etc. - is still only inside the iframes, never
    # duplicated into the outer page.
    assert 'og:title" content="Jordan Reyes' in resp.text
    assert "Northwind Systems" not in resp.text
    for template in ("Modern", "Classic", "Compact"):
        assert template in resp.text
    assert "/cv/" in resp.text and ".pdf?template=" in resp.text


def test_cv_builder_result_page_share_meta_includes_role(client):
    resp = client.post("/cv-builder", data=_FORM_PAYLOAD, follow_redirects=True)
    assert 'og:title" content="Jordan Reyes — CV"' in resp.text
    assert 'og:description" content="Senior Backend Engineer' in resp.text
    assert 'og:image" content="https://tsech.online/logo.png"' in resp.text
    assert f'og:url" content="https://tsech.online/audit/cv-builder/' in resp.text


def test_cv_builder_result_page_share_meta_falls_back_without_role_or_summary(client):
    data = {k: v for k, v in _FORM_PAYLOAD.items() if k != "role"}
    del data["summary"]
    resp = client.post("/cv-builder", data=data, follow_redirects=True)
    assert 'og:title" content="Jordan Reyes — CV"' in resp.text
    assert 'og:description" content="Create with tsech.online"' in resp.text


def test_cv_builder_result_page_localizes(client):
    resp = client.post("/cv-builder", data={**_FORM_PAYLOAD, "lang": "fr"}, follow_redirects=True)
    assert resp.status_code == 200
    assert '<html lang="fr">' in resp.text
    assert "La version Markdown est identique" in resp.text  # ui.cv.download_md_note


def test_cv_builder_result_page_has_no_back_link_or_heading(client):
    # This page is what a shared /cv-builder/{id} link opens to - it should
    # read as "here is the CV", not as a builder-tool screen with its own
    # navigation chrome back into the app.
    resp = client.post("/cv-builder", data=_FORM_PAYLOAD, follow_redirects=True)
    assert "Build another CV" not in resp.text
    assert "Compare templates" not in resp.text
    assert "<h1>" not in resp.text


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
    assert len(body["edit_token"]) >= 16
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
    created = client.post("/cv", json={**_SAMPLE_PAYLOAD, "template": "classic"}).json()
    cv_id, edit_token = created["id"], created["edit_token"]

    async def fake_edit(current, instructions, model_name):
        assert current.name == "Jordan Reyes"
        assert instructions == "Add Kubernetes to skills"
        assert model_name == "deepseek/deepseek-chat"
        return current.model_copy(update={"skills": [*current.skills, "Kubernetes"]})

    monkeypatch.setattr(app_module, "edit_cv_profile", fake_edit)
    resp = client.post(f"/cv/{cv_id}/edit", json={"instructions": "Add Kubernetes to skills", "edit_token": edit_token})
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == cv_id  # same id/link, not a new CV
    assert body["template"] == "classic"  # untouched
    assert body["profile"]["skills"] == ["Python", "Go", "Kubernetes"]

    # persisted, not just returned
    assert client.get(f"/cv/{cv_id}").json()["profile"]["skills"] == ["Python", "Go", "Kubernetes"]


def test_edit_cv_forwards_model_name(client, monkeypatch):
    created = client.post("/cv", json=_SAMPLE_PAYLOAD).json()

    async def fake_edit(current, instructions, model_name):
        assert model_name == "openai/gpt-4o"
        return current

    monkeypatch.setattr(app_module, "edit_cv_profile", fake_edit)
    resp = client.post(f"/cv/{created['id']}/edit", json={
        "instructions": "tighten the summary", "model_name": "openai/gpt-4o", "edit_token": created["edit_token"],
    })
    assert resp.status_code == 200


def test_edit_cv_unknown_id_is_404_without_calling_the_model(client, monkeypatch):
    called = []

    async def fake_edit(current, instructions, model_name):
        called.append(instructions)
        return current

    monkeypatch.setattr(app_module, "edit_cv_profile", fake_edit)
    resp = client.post("/cv/does-not-exist/edit", json={"instructions": "add a skill", "edit_token": "anything"})
    assert resp.status_code == 404
    assert called == []


def test_edit_cv_rejects_blank_instructions_without_calling_the_model(client, monkeypatch):
    created = client.post("/cv", json=_SAMPLE_PAYLOAD).json()
    called = []

    async def fake_edit(current, instructions, model_name):
        called.append(instructions)
        return current

    monkeypatch.setattr(app_module, "edit_cv_profile", fake_edit)
    resp = client.post(f"/cv/{created['id']}/edit", json={"instructions": "   ", "edit_token": created["edit_token"]})
    assert resp.status_code == 422
    assert called == []


def test_edit_cv_failure_is_422_and_does_not_overwrite_stored_profile(client, monkeypatch):
    created = client.post("/cv", json=_SAMPLE_PAYLOAD).json()
    cv_id = created["id"]

    async def fake_edit(current, instructions, model_name):
        return None

    monkeypatch.setattr(app_module, "edit_cv_profile", fake_edit)
    resp = client.post(f"/cv/{cv_id}/edit", json={"instructions": "add a skill", "edit_token": created["edit_token"]})
    assert resp.status_code == 422
    assert client.get(f"/cv/{cv_id}").json()["profile"]["skills"] == ["Python", "Go"]


# ---- edit_token enforcement (a CV's share link must not double as an edit key) --


def test_edit_cv_wrong_token_is_403_without_calling_the_model(client, monkeypatch):
    cv_id = client.post("/cv", json=_SAMPLE_PAYLOAD).json()["id"]
    called = []

    async def fake_edit(current, instructions, model_name):
        called.append(instructions)
        return current

    monkeypatch.setattr(app_module, "edit_cv_profile", fake_edit)
    resp = client.post(f"/cv/{cv_id}/edit", json={"instructions": "add a skill", "edit_token": "wrong-token"})
    assert resp.status_code == 403
    assert called == []
    # and the profile is untouched
    assert client.get(f"/cv/{cv_id}").json()["profile"]["skills"] == ["Python", "Go"]


def test_edit_cv_missing_token_field_is_422(client):
    cv_id = client.post("/cv", json=_SAMPLE_PAYLOAD).json()["id"]
    resp = client.post(f"/cv/{cv_id}/edit", json={"instructions": "add a skill"})
    assert resp.status_code == 422


def test_edit_cv_a_cvs_own_token_does_not_work_on_a_different_cv(client, monkeypatch):
    created_a = client.post("/cv", json=_SAMPLE_PAYLOAD).json()
    created_b = client.post("/cv", json={**_SAMPLE_PAYLOAD, "name": "Someone Else"}).json()

    async def fake_edit(current, instructions, model_name):
        return current

    monkeypatch.setattr(app_module, "edit_cv_profile", fake_edit)
    resp = client.post(f"/cv/{created_b['id']}/edit", json={"instructions": "add a skill", "edit_token": created_a["edit_token"]})
    assert resp.status_code == 403


# ---- LLM concurrency gate + input-length caps ----------------------------------
#
# Unlike POST /cv, /cv/from-text and /cv/{id}/edit are paid LLM calls on an
# unauthenticated endpoint - both are bounded the same way audits are.


def test_cv_from_text_returns_503_when_llm_gate_is_full(client, monkeypatch):
    monkeypatch.setattr(app_module, "_llm_gate", asyncio.Semaphore(1))
    asyncio.new_event_loop().run_until_complete(app_module._llm_gate.acquire())
    try:
        resp = client.post("/cv/from-text", json={"text": "Jordan Reyes, jordan@example.com"})
        assert resp.status_code == 503
    finally:
        app_module._llm_gate.release()


def test_edit_cv_returns_503_when_llm_gate_is_full(client, monkeypatch):
    created = client.post("/cv", json=_SAMPLE_PAYLOAD).json()
    monkeypatch.setattr(app_module, "_llm_gate", asyncio.Semaphore(1))
    asyncio.new_event_loop().run_until_complete(app_module._llm_gate.acquire())
    try:
        resp = client.post(f"/cv/{created['id']}/edit", json={"instructions": "add a skill", "edit_token": created["edit_token"]})
        assert resp.status_code == 503
    finally:
        app_module._llm_gate.release()


def test_cv_from_text_releases_the_gate_after_finishing(client, monkeypatch):
    monkeypatch.setattr(app_module, "_llm_gate", asyncio.Semaphore(1))

    async def fake_extract(text, model_name):
        return CVProfile(name="X", contact=Contact(email="x@example.com"))

    monkeypatch.setattr(app_module, "extract_cv_profile", fake_extract)
    resp = client.post("/cv/from-text", json={"text": "some bio"})
    assert resp.status_code == 200
    assert not app_module._llm_gate.locked()  # released, not leaked


def test_cv_from_text_rejects_overly_long_text(client):
    resp = client.post("/cv/from-text", json={"text": "a" * 20_001})
    assert resp.status_code == 422


def test_edit_cv_rejects_overly_long_instructions(client):
    created = client.post("/cv", json=_SAMPLE_PAYLOAD).json()
    resp = client.post(f"/cv/{created['id']}/edit", json={"instructions": "a" * 2_001, "edit_token": created["edit_token"]})
    assert resp.status_code == 422


# ---- Field length limits on POST /cv itself ------------------------------------
#
# Unlike /cv/from-text and /edit, POST /cv never touches an LLM - these bound
# storage/render size on a public, unmetered endpoint rather than model cost.


def test_create_cv_rejects_overly_long_name(client):
    resp = client.post("/cv", json={**_SAMPLE_PAYLOAD, "name": "a" * 201})
    assert resp.status_code == 422


def test_create_cv_rejects_overly_long_summary(client):
    resp = client.post("/cv", json={**_SAMPLE_PAYLOAD, "summary": "a" * 5_001})
    assert resp.status_code == 422


def test_create_cv_rejects_too_many_skills(client):
    resp = client.post("/cv", json={**_SAMPLE_PAYLOAD, "skills": [f"skill{i}" for i in range(101)]})
    assert resp.status_code == 422


def test_create_cv_rejects_too_many_experience_entries(client):
    row = {"title": "Engineer", "organization": "Co", "start": "2020"}
    resp = client.post("/cv", json={**_SAMPLE_PAYLOAD, "experience": [row] * 51})
    assert resp.status_code == 422


def test_create_cv_accepts_a_realistic_full_profile(client):
    # The limits above shouldn't be so tight they reject a real, detailed CV.
    resp = client.post("/cv", json={
        **_SAMPLE_PAYLOAD,
        "summary": "a" * 4000,
        "skills": [f"skill{i}" for i in range(40)],
        "experience": [{"title": "Engineer", "organization": "Co", "start": "2020", "bullets": ["did things"] * 10}] * 10,
    })
    assert resp.status_code == 200
