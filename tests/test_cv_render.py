import pytest

from src.cv.render import TEMPLATES, render_cv_html
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
