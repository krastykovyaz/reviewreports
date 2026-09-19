import re

import pytest

from src.report.helpers import score_findings
from src.report.render import render_html, render_latex, render_markdown
from src.report.schema import Finding, Pillar, Report, ReportMeta, Status, grade_for
from tests.fixtures.tsech_report import build_tsech_report


@pytest.fixture()
def report() -> Report:
    return build_tsech_report()


def test_compute_overall_score_ignores_na_pillars(report: Report):
    assert report.overall_score == pytest.approx(2.5)  # SEO=3, Security=2, Links=None


def test_compute_overall_score_is_none_when_nothing_scored():
    r = Report(meta=ReportMeta(kind="website_audit", subject="https://example.com"), pillars=[Pillar(name="X", score=None, summary="n/a")])
    assert r.compute_overall_score() is None


def test_status_counts_and_grade(report: Report):
    assert report.status_counts() == {"ok": 1, "warn": 1, "bad": 3, "na": 0}
    assert report.grade == "F"
    assert [grade_for(s) for s in (9.5, 8, 6.5, 5, 2, None)] == ["A", "B", "C", "D", "F", "N/A"]


def test_score_findings_weights_and_na():
    findings = [
        Finding(check="a", status=Status.OK, detail=""),
        Finding(check="b", status=Status.WARN, detail=""),
        Finding(check="c", status=Status.BAD, detail=""),
        Finding(check="d", status=Status.NA, detail=""),
    ]
    assert score_findings(findings) == 5.0  # (1 + 0.5 + 0) / 3
    assert score_findings(findings, {"c": 3}) == 3.0  # (1 + 0.5 + 0) / 5
    assert score_findings([findings[3]]) is None


def test_recommendations_use_fix_when_present(report: Report):
    by_action = {r.action: r for r in report.recommendations}
    assert by_action["[SEO] Fix: H1 heading"].rationale == "Wrap the main headline in an <h1>."
    assert by_action["[SEO] Fix: robots.txt"].rationale == "Returns HTTP 404"  # falls back to detail
    assert [r.priority for r in report.recommendations] == [1, 2, 3, 4]
    assert report.recommendations[0].action.startswith("[SEO] Fix")  # BAD before WARN


def test_markdown_contains_summary_sections_evidence_and_fix(report: Report):
    md = render_markdown(report)
    assert "# Website Audit Report" in md
    assert "## Executive summary" in md
    assert "(grade F)" in md and "3 critical issue(s), 1 warning(s)" in md
    assert "Top priorities:" in md
    assert "| SEO | 3.0/10 |" in md and "| Broken Links | N/A |" in md
    assert "**[Warning] Title tag**" in md and "**[Missing] H1 heading**" in md
    assert '  - Evidence: "tsech" (5 characters)' in md
    assert "  - Fix: Wrap the main headline in an <h1>." in md
    assert "## Recommendations" in md and "## Methodology" in md and "## Limitations" in md


def test_html_renders_summary_status_evidence_and_fix(report: Report):
    html = render_html(report)
    assert "<table>" in html and "Website Audit Report" in html
    assert 'class="grade">F' in html
    assert 'class="status status-bad"' in html and "[Missing]" not in html  # labels are plain in HTML
    assert "Evidence:" in html and "5 characters" in html
    assert "<strong>Fix:</strong>" in html
    assert "100% of responses" in html


def test_html_autoescapes_untrusted_text():
    malicious = Report(
        meta=ReportMeta(kind="website_audit", subject="https://example.com"),
        pillars=[Pillar(name="XSS", score=1, summary="<script>alert(1)</script>", findings=[Finding(check="c", status=Status.BAD, detail="<img src=x onerror=alert(1)>", evidence="<b>e</b>", fix="<i>f</i>")])],
    ).finalize()
    html = render_html(malicious)
    for raw in ("<script>alert(1)</script>", "<img src=x onerror=alert(1)>", "<b>e</b>", "<i>f</i>"):
        assert raw not in html
    assert "&lt;script&gt;" in html


def test_latex_has_valid_document_structure_and_summary(report: Report):
    tex = render_latex(report)
    assert tex.strip().startswith(r"\documentclass") and tex.strip().endswith(r"\end{document}")
    assert r"\section*{Executive summary}" in tex and "grade F" in tex
    assert r"\section*{Scorecard}" in tex and r"\section*{SEO" in tex
    assert "Evidence:" in tex and r"\textbf{Fix:}" in tex


def test_latex_escapes_special_characters(report: Report):
    tex = render_latex(report)
    for line in tex.splitlines():
        if "responses checked" in line:
            assert r"\%" in line
            assert re.search(r"(?<!\\)%", line) is None, f"unescaped %% in: {line}"
            assert "edge\\_case" in line
            assert r"\&" in line
