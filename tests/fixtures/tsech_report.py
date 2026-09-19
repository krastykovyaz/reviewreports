"""Fixture report built from the real tsech.online scan (see website audit example).

Deliberately includes a finding with LaTeX-special characters ("%", "&", "_")
in its detail text, to exercise escaping in the LaTeX renderer, and findings
with evidence/fix set, to exercise their rendering.
"""

from datetime import datetime, timezone

from src.report.helpers import build_recommendations
from src.report.schema import Finding, Pillar, Report, ReportMeta, Status


def build_tsech_report() -> Report:
    pillars = [
        Pillar(
            name="SEO",
            score=3,
            summary="No H1/H2, no canonical, no sitemap or robots.txt, weak title",
            findings=[
                Finding(check="Title tag", status=Status.WARN, detail='Single word ("tsech"), no keywords', evidence='"tsech" (5 characters)', fix="Expand to 30–60 characters."),
                Finding(check="Meta description", status=Status.OK, detail="Present, Russian-only"),
                Finding(check="H1 heading", status=Status.BAD, detail="Zero <h1> elements found", fix="Wrap the main headline in an <h1>."),
                Finding(check="robots.txt", status=Status.BAD, detail="Returns HTTP 404"),
            ],
        ),
        Pillar(
            name="Security Headers",
            score=2,
            summary="Missing CSP, HSTS, X-Frame-Options, X-Content-Type-Options",
            findings=[
                Finding(
                    check="Content-Security-Policy",
                    status=Status.BAD,
                    detail="Header not set; 100% of responses checked were missing it (edge_case & special_chars_test)",
                ),
            ],
        ),
        Pillar(name="Broken Links", score=None, summary="Homepage has zero <a> links to crawl", findings=[]),
    ]
    return Report(
        meta=ReportMeta(kind="website_audit", subject="https://tsech.online", generated_at=datetime(2026, 9, 19, tzinfo=timezone.utc)),
        verdict="Solid technical foundation, weak discoverability and compliance posture.",
        pillars=pillars,
        recommendations=build_recommendations(pillars),
        methodology="Live headless-browser scan of the homepage; direct HTTP requests for robots.txt/sitemap.xml.",
        limitations="Only the homepage was scanned; authenticated pages were out of scope.",
    ).finalize()
