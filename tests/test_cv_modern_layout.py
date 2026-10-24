"""The Modern template used to spill onto a second page for an ordinary CV: its narrow dark sidebar holds contact, skills AND every
degree, so with a few degrees the sidebar was taller than the page while the wide main column was half empty - and the page break
cut the sidebar in two (page 2: a dark strip and an empty column). Education now moves to the main column when that makes the page
shorter (render.modern_education_in_main). These tests render real PDFs: the choice is judged by page count, not by the estimate."""
import io
import itertools
import random

import pytest
from pypdf import PdfReader

from src.cv import render as R
from src.cv.schema import CVProfile

WORDS = "ml data lead senior engineer platform analytics product research systems cloud".split()


def _txt(rng, n):
    return " ".join(rng.choice(WORDS) for _ in range(n))


def make(edu_n, jobs_n, bullets=1, skills_n=11, links_n=3, summary_words=0, seed=1):
    rng = random.Random(seed)
    return CVProfile.model_validate({
        "name": "Aleksandr Koviiazin", "role": "Team Lead MLE / MLOps / DS", "summary": _txt(rng, summary_words) if summary_words else "",
        "contact": {"email": "alexkovyaz@gmail.com", "location": "Luxembourg / Moscow", "links": [f"linkedin.com/in/someone-{i}-profile" for i in range(links_n)]},
        "skills": [_txt(rng, 1).title() + (" " + _txt(rng, 1) if i % 3 == 0 else "") for i in range(skills_n)],
        "experience": [{"title": _txt(rng, 3).title(), "organization": _txt(rng, 2).title(), "location": "Moscow", "start": "2020", "end": "2023",
                        "bullets": [_txt(rng, rng.randint(8, 16)) for _ in range(bullets)]} for _ in range(jobs_n)],
        "education": [{"degree": _txt(rng, rng.randint(2, 4)).title(), "institution": _txt(rng, rng.randint(2, 4)).title(), "year": "2010–2014"} for _ in range(edu_n)],
        "languages": []})


def pages(profile, edu_in_main=None):
    """Page count of the real Modern PDF; edu_in_main forces the layout (None = what the renderer decides)."""
    orig = R.modern_education_in_main
    if edu_in_main is not None:
        R.modern_education_in_main = lambda p, has_photo=False: edu_in_main
    try:
        return len(PdfReader(io.BytesIO(R.render_cv_pdf(profile, "modern"))).pages)
    finally:
        R.modern_education_in_main = orig


def reported_cv(edu_n=8, jobs_n=5):
    """The CV from the bug report (short bullets, 8 degrees, 5 jobs): what a real person's CV looks like."""
    return CVProfile.model_validate({
        "name": "Aleksandr Koviiazin", "role": "Team Lead MLE / MLOps / DS", "summary": "",
        "contact": {"email": "alexkovyaz@gmail.com", "location": "Luxembourg / Moscow",
                    "links": ["linkedin.com/in/aleksandr-kovyazin", "github.com/krastykovyaz", "kaggle.com/alexkovyaz"]},
        "skills": ["Python", "Django", "BiLSTM", "BERT", "GPT", "LightGBM", "ML Engineering", "Data Science", "NLP/CV", "LLM Agents", "MLOps"],
        "experience": [{"title": t, "organization": o, "location": l, "start": s, "end": e, "bullets": [b]} for t, o, l, s, e, b in [
            ("Team Lead MLE/MLOps/DS", "Finmars", "Luxembourg", "2024", "Present", "Finance systems for investors, ML for market predictions."),
            ("Team Lead (15 specialists)", "Sberbank", "Moscow", "2022", "2024", "IVR/ChatBot/Operator CoPilot systems."),
            ("Middle ML Engineer", "Sberbank", "Moscow", "2020", "2021", "ChatBot: BiLSTM/BERT, 100k+ requests. IVR voice assistant."),
            ("Developer", "Sberbank", "Moscow", "2019", "2020", "Data Analysis/Data Engineering."),
            ("Senior Chemist", "Core Laboratories", "Saint-Petersburg", "2015", "2018", "Petroleum laboratory core analysis.")][:jobs_n]],
        "education": [{"degree": d, "institution": i, "year": y} for d, i, y in [
            ("PhD CS in Finance", "Luxembourg Uni", "2024–Now"), ("Computer Science", "Ecole 42, Paris", "2022–2025"), ("ML/DS/MLOps", "VK MADE, Moscow", "2022–2023"),
            ("Programming", "School 21, Moscow", "2019–2022"), ("Exchange, Finance & Risk", "Peking University", "2018–2019"), ("Master, Economics", "SPBSU, Russia", "2017–2019"),
            ("Specialist, Chemistry", "SPBSU, Russia", "2009–2015"), ("Technic Technolog", "Saint-Petersburg Food College", "2005–2009")][:edu_n]],
        "languages": []})


# ── the case that was reported ───────────────────────────────────────────────

def test_a_cv_with_many_degrees_and_several_jobs_is_one_page():
    p = reported_cv(8, 5)
    assert pages(p, edu_in_main=False) == 2     # the old layout: the sidebar ran past the page
    assert pages(p) == 1                        # now


@pytest.mark.parametrize("edu_n,jobs_n", [(4, 5), (8, 2), (6, 3), (5, 4)])
def test_other_degree_heavy_cvs_that_used_to_spill_are_one_page_too(edu_n, jobs_n):
    assert pages(reported_cv(edu_n, jobs_n)) == 1


# ── the decision ─────────────────────────────────────────────────────────────

def test_education_stays_in_the_sidebar_when_it_fits_there_and_in_the_main_column_when_it_does_not():
    assert R.modern_education_in_main(reported_cv(edu_n=2, jobs_n=3)) is False    # fits as it is: the familiar layout stays
    assert R.modern_education_in_main(reported_cv(edu_n=3, jobs_n=3)) is False    # the last count that still fits one page in the sidebar
    assert R.modern_education_in_main(reported_cv(edu_n=8, jobs_n=5)) is True
    assert R.modern_education_in_main(reported_cv(edu_n=0, jobs_n=3)) is False


def test_a_cv_whose_main_column_is_already_the_long_one_keeps_education_in_the_sidebar():
    assert R.modern_education_in_main(make(edu_n=2, jobs_n=6, bullets=2, summary_words=40)) is False


def test_a_photo_makes_the_sidebar_taller_so_the_move_comes_sooner():
    p = make(edu_n=3, jobs_n=3)
    assert R.modern_education_in_main(p, has_photo=True) >= R.modern_education_in_main(p, has_photo=False)


def test_the_estimate_never_picks_a_longer_layout_than_the_other_one():
    """Judged by real page counts over a spread of shapes, not by the estimator's own numbers."""
    bad = []
    for edu_n, jobs_n, bullets, links_n, sw in itertools.product((1, 3, 6, 8), (1, 3, 5), (1, 2), (1, 4), (0, 25)):
        p = make(edu_n, jobs_n, bullets, 9, links_n, sw, seed=edu_n * 100 + jobs_n)
        a, b = pages(p, False), pages(p, True)
        chosen = b if R.modern_education_in_main(p) else a
        if chosen > min(a, b):
            bad.append((edu_n, jobs_n, bullets, links_n, sw, a, b))
    assert not bad, bad


# ── what the page contains ───────────────────────────────────────────────────

def test_education_is_in_the_main_column_when_moved_and_not_duplicated():
    p = reported_cv(8, 5)
    html = R.render_cv_html(p, "modern")
    main = html.split('<div class="main">')[1]
    side = html.split('<div class="main">')[0]
    assert "<h4>Education</h4>" in main and "<h4>Education</h4>" not in side
    import html as _html
    for e in p.education:
        d = _html.escape(e.degree)
        assert main.count(d) == 1 and d not in side


def test_education_is_in_the_sidebar_when_it_fits_there():
    p = reported_cv(2, 3)
    html = R.render_cv_html(p, "modern")
    side, main = html.split('<div class="main">')
    assert "<h4>Education</h4>" in side and "<h4>Education</h4>" not in main


def test_other_templates_and_the_text_export_are_untouched():
    p = reported_cv(8, 5)
    for tpl in ("classic", "compact"):
        assert "edu_in_main" not in R.render_cv_html(p, tpl) and "Education" in R.render_cv_html(p, tpl)
    assert R.render_cv_markdown(p).count("## Education") == 1


def test_degree_text_is_escaped_in_the_main_column_too():
    p = reported_cv(8, 5)
    p.education[0].degree = "<script>alert(1)</script>"
    html = R.render_cv_html(p, "modern")
    assert "<script>alert(1)" not in html and "&lt;script&gt;" in html
