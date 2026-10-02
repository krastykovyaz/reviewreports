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


def _require_weasyprint():
    try:
        from weasyprint import HTML  # noqa: F401
    except (ImportError, OSError) as exc:  # OSError: WeasyPrint without system Pango/GObject
        pytest.skip(f"WeasyPrint unavailable: {exc}")


@pytest.mark.parametrize("template", TEMPLATES)
def test_render_cv_pdf_produces_valid_pdf_bytes(template):
    _require_weasyprint()
    # Basic contract: valid, non-trivial PDF bytes. The one-page fit is
    # asserted separately in test_full_profile_fits_one_pdf_page.
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


# ---- 2026-10-02 validation review regressions ------------------------------------


@pytest.mark.parametrize("template", TEMPLATES)
def test_every_template_renders_role_and_languages(template):
    # Classic and Compact silently dropped both; only Modern rendered them, and
    # the section test above never asserted on either field.
    profile = _sample_profile()
    profile.role = "ROLE_MARKER"
    profile.languages = ["LANG_MARKER_EN", "LANG_MARKER_ES"]
    html = render_cv_html(profile, template)
    assert "ROLE_MARKER" in html
    assert "LANG_MARKER_EN" in html and "LANG_MARKER_ES" in html


@pytest.mark.parametrize("template", TEMPLATES)
def test_languages_without_skills_still_render(template):
    profile = CVProfile(name="Alex Kim", contact=Contact(email="a@b.c"), languages=["LANG_MARKER"])
    assert "LANG_MARKER" in render_cv_html(profile, template)


@pytest.mark.parametrize("template", TEMPLATES)
def test_full_profile_fits_one_pdf_page(template):
    # WeasyPrint's own page count: the one-page claim the templates are tuned
    # for, checked for real instead of by eye. A regression that adds a field
    # without room for it spills onto a second page and fails here.
    _require_weasyprint()
    from weasyprint import HTML

    profile = _sample_profile()
    profile.role = "Senior Backend Engineer"
    assert len(HTML(string=render_cv_html(profile, template)).render().pages) == 1
