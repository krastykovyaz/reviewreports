import pytest

from src.cv.render import OUTPUT_FORMATS, TEMPLATES, render_cv_html, render_cv_markdown, render_cv_pdf
from src.cv.schema import Contact, CVProfile, Education, Experience


def _sample_profile() -> CVProfile:
    return CVProfile(
        name="Jordan Reyes",
        role="Senior Backend Engineer",
        summary="Backend engineer with 8 years building distributed systems.",
        contact=Contact(email="jordan.reyes@email.com", phone="+1 415 555 0142", location="San Francisco, CA", links=["linkedin.com/in/jordanreyes"]),
        experience=[
            Experience(title="Senior Backend Engineer", organization="Northwind Systems", location="San Francisco, CA", start="2021", end="Present", bullets=["Redesigned the payments pipeline.", "Mentored 4 engineers."]),
            Experience(title="Backend Engineer", organization="Faircloud", start="2017", end="2021", bullets=["Built the billing service."]),
        ],
        education=[Education(degree="B.S. Computer Science", institution="UC San Diego", year="2016")],
        skills=["Python", "Go", "PostgreSQL"],
        languages=["English (native)", "Spanish (fluent)"],
    )


def test_templates_tuple_has_three_entries():
    assert TEMPLATES == ("modern", "classic", "compact")


@pytest.mark.parametrize("template", TEMPLATES)
def test_render_cv_html_includes_all_sections(template):
    html = render_cv_html(_sample_profile(), template)
    assert "Jordan Reyes" in html
    assert "jordan.reyes@email.com" in html
    assert "Northwind Systems" in html
    assert "Redesigned the payments pipeline." in html
    assert "Faircloud" in html
    assert "UC San Diego" in html
    assert "Python" in html


@pytest.mark.parametrize("template", TEMPLATES)
def test_render_cv_html_escapes_content(template):
    profile = _sample_profile()
    profile.name = "<script>alert(1)</script>"
    html = render_cv_html(profile, template)
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


@pytest.mark.parametrize("template", TEMPLATES)
def test_render_cv_html_handles_minimal_profile(template):
    profile = CVProfile(name="Alex Kim", contact=Contact(email="alex@example.com"))
    html = render_cv_html(profile, template)
    assert "Alex Kim" in html
    assert "alex@example.com" in html


def test_render_cv_html_rejects_unknown_template():
    with pytest.raises(ValueError, match="Unknown template"):
        render_cv_html(_sample_profile(), "brutalist")


def test_output_formats_tuple_has_three_entries():
    assert OUTPUT_FORMATS == ("html", "pdf", "markdown")


@pytest.mark.parametrize("template", TEMPLATES)
def test_render_cv_pdf_produces_valid_pdf_bytes(template):
    # Real page-count/one-page-fit verification needs a PDF parser, which
    # isn't a project dependency - that was done by hand (rendered +
    # rasterized each template's PDF output and inspected it) rather than
    # here. This just guards the basic contract: valid, non-trivial PDF bytes.
    pdf_bytes = render_cv_pdf(_sample_profile(), template)
    assert pdf_bytes.startswith(b"%PDF")
    assert len(pdf_bytes) > 1000


def test_render_cv_markdown_includes_all_sections():
    md = render_cv_markdown(_sample_profile())
    assert md.startswith("# Jordan Reyes")
    assert "jordan.reyes@email.com" in md
    assert "## Experience" in md
    assert "Northwind Systems" in md
    assert "Redesigned the payments pipeline." in md
    assert "## Education" in md
    assert "UC San Diego" in md
    assert "## Skills" in md
    assert "Python, Go, PostgreSQL" in md
    assert "## Languages" in md


def test_render_cv_markdown_omits_empty_sections():
    profile = CVProfile(name="Alex Kim", contact=Contact(email="alex@example.com"))
    md = render_cv_markdown(profile)
    assert "## Experience" not in md
    assert "## Education" not in md
    assert "## Skills" not in md
    assert "## Languages" not in md


# ── layout: a long, realistic profile must still fit the one-page frame ──

def _long_profile():
    from src.cv.schema import CVProfile
    return CVProfile.model_validate({
        "name": "Aleksandr Koviazin", "role": "Team Lead MLE / MLOps / DS",
        "contact": {"email": "alexkovyaz@gmail.com", "location": "Luxembourg / Moscow",
                    "links": ["aleksandr-kovyazin", "krastykovyaz", "kaggle.com/alexkovyaz"]},
        "experience": [
            {"title": "Team Lead MLE/MLOps/DS", "organization": "Finmars", "location": "Luxembourg", "start": "2024", "end": "Present", "bullets": ["Finance systems for investors."]},
            {"title": "Team Lead (15 specialists)", "organization": "Sberbank", "start": "2022", "end": "2024", "bullets": ["IVR/ChatBot systems."]},
            {"title": "Middle ML Engineer", "organization": "Sberbank", "start": "2020", "end": "2021", "bullets": ["ChatBot: BiLSTM/BERT, 100k+ requests."]},
        ],
        "education": [{"degree": "PhD CS in Finance", "institution": "Luxembourg Uni"}, {"degree": "Computer Science", "institution": "Ecole 42, Paris"},
                      {"degree": "ML/DS/MLOps", "institution": "Various courses"}],
        "skills": ["Python", "Django", "BiLSTM", "BERT", "GPT", "LightGBM", "ML/DL", "NLP/CV", "LLM", "MLOps", "Data Science", "Agents"],
        "languages": ["English (fluent)", "Russian (native)", "French (basic)"],
    })


@pytest.mark.parametrize("template", TEMPLATES)
def test_a_long_skills_list_still_fits_one_pdf_page(template):
    from weasyprint import HTML
    assert len(HTML(string=render_cv_html(_long_profile(), template)).render().pages) == 1


def test_modern_sidebar_skills_are_one_flowing_line_not_a_bullet_per_skill():
    html = render_cv_html(_long_profile(), "modern")
    assert "Python · Django" in html and "<li>Python</li>" not in html


@pytest.mark.parametrize("template", TEMPLATES)
def test_a_job_date_never_wraps_onto_two_lines(template):
    # The title shrinks and wraps instead; the date keeps its width.
    assert "white-space: nowrap" in render_cv_html(_long_profile(), template)


# ── a CV longer than one page: nothing is cut off ──

def _very_long_profile():
    p = _long_profile().model_dump()
    p["education"] = [{"degree": f"Degree {i}", "institution": f"INSTITUTION-{i}", "year": "2000"} for i in range(14)]
    p["languages"] = ["English (fluent)", "Russian (native)"]
    return CVProfile.model_validate(p)


@pytest.mark.parametrize("template", TEMPLATES)
def test_the_on_screen_card_grows_instead_of_clipping(template):
    import re
    html = render_cv_html(_very_long_profile(), template)
    rule = re.search(r"\.doc \{[^}]*\}", html).group(0)
    assert "overflow: hidden" not in rule and "overflow:hidden" not in rule
    assert "min-height: 569px" in html or template != "modern"


@pytest.mark.parametrize("template", TEMPLATES)
def test_a_very_long_cv_keeps_every_entry_in_the_pdf(template):
    from io import BytesIO
    from pypdf import PdfReader
    pages = PdfReader(BytesIO(render_cv_pdf(_very_long_profile(), template))).pages
    text = "\n".join(p.extract_text() for p in pages)
    assert len(pages) >= 2 and all(f"INSTITUTION-{i}" in text for i in range(14))


@pytest.mark.parametrize("template", TEMPLATES)
def test_languages_are_rendered_by_every_template(template):
    # Classic and Compact used to drop the field silently.
    assert "Russian (native)" in render_cv_html(_long_profile(), template)
