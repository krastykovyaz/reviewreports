"""Renders a CVProfile into one of the three fixed one-page templates, and
into any of the CV builder's output formats (HTML, PDF, Markdown).

Layout/density for each template was validated in a browser mockup against
a Letter-ratio (8.5:11) frame with actual sample content, confirming a
one-page fit via pixel measurement rather than by assertion. Each template's
CSS scales that same validated content up from the 440px screen-preview
scale to true 8.5in print dimensions under `@media print` — WeasyPrint
(used for PDF export below) treats content as print media by default, so
the one rendered HTML works, unmodified, as both the on-screen preview and
the PDF source.

The print scaling uses literal pre-multiplied values (padding/font-size/
margins × 816/440), not `transform: scale` on the layout root — confirmed
by rendering actual PDFs, not just reviewing CSS, that WeasyPrint truncates
overflowing content when transform:scale is combined with a multi-column
layout (flex, float, or table all reproduced it; plain non-multi-column
content scaled correctly). Also confirmed the same way: WeasyPrint ignores
the `aspect-ratio` CSS property outright, so .doc's print-mode height is set
explicitly instead; and even with overflow:hidden and that explicit height,
WeasyPrint's pagination overrides it for content that doesn't fit — a
profile too long for one page spills onto additional PDF pages rather than
being clipped, which is safer than silently losing real content but means
"one page" isn't a hard guarantee for arbitrary input, only for content
that fits within the validated density.
"""

import os
from typing import Optional, Tuple

from jinja2 import Environment, FileSystemLoader, select_autoescape

from src.cv.photo import DATA_URI_PREFIX
from src.cv.schema import CVProfile

TEMPLATES: Tuple[str, ...] = ("modern", "classic", "compact")
OUTPUT_FORMATS: Tuple[str, ...] = ("html", "pdf", "markdown")

_TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "templates")
_env = Environment(loader=FileSystemLoader(_TEMPLATES_DIR), autoescape=select_autoescape(["html"]))


def _ceil_div(a: int, b: int) -> int:
    return max(1, -(-a // b))


def modern_column_estimates(profile: CVProfile, has_photo: bool = False) -> Tuple[float, float, float, float]:
    """Heights, in print-layout pixels (8.5in = 816px wide, 11in = 1056px tall; text metrics from modern.html's @media print rules), of
    (sidebar without education, education as sidebar entries, main column without education, education as main-column entries)."""
    SIDE_COLS, MAIN_COLS, SIDE_LH, MAIN_LH = 22, 42, 29.6, 30.6
    def side_text(t: str) -> float:   # paragraph in the sidebar
        return _ceil_div(len(t), SIDE_COLS) * SIDE_LH + 5.6
    def side_head() -> float:
        return 52.0

    c = profile.contact
    side = 66.8 + (163.0 if has_photo else 0.0)
    side += _ceil_div(len(profile.name), 13) * 39.4
    if profile.role:
        side += _ceil_div(len(profile.role), 16) * 28.5 + 35.0
    side += side_head() + side_text(c.email or "-")
    side += side_text(c.phone) if c.phone else 0
    side += side_text(c.location) if c.location else 0
    side += sum(side_text(link) for link in c.links)
    if profile.skills:
        side += side_head() + side_text("\u00a0· ".join(profile.skills))
    if profile.languages:
        side += side_head() + side_text(", ".join(profile.languages))
    edu_side = (side_head() + sum(side_text(f"{e.degree} {e.institution}, {e.year or ''}") + SIDE_LH for e in profile.education)) if profile.education else 0.0

    main = 55.6
    if profile.summary:
        main += 74.0 + _ceil_div(len(profile.summary), 44) * 30.9
    if profile.experience:
        main += 74.0
        for j in profile.experience:
            main += 27.0 * _ceil_div(len(j.title) + len(f"{j.start} – {j.end}") + 2, 38) + 33.0 + 10.5
            main += sum(_ceil_div(len(b), MAIN_COLS) * MAIN_LH for b in j.bullets)
    edu_main = (74.0 + sum(_ceil_div(len(e.degree) + len(e.institution or "") + len(e.year or "") + 5, MAIN_COLS) * 26.5 + 6.0 for e in profile.education)) if profile.education else 0.0
    return side, edu_side, main, edu_main


# Estimated px of the tallest column that still ends inside the 1056px page (the estimate runs ~10% high: 1066 still fits, 1160 does not). Calibrated against real
# PDFs: see tests/test_cv_modern_layout.py.
FITS_ONE_PAGE = 1100.0


def modern_education_in_main(profile: CVProfile, has_photo: bool = False) -> bool:
    """Where the Modern template puts Education: in the dark sidebar (False) or in the wide main column (True).

    The sidebar holds contact, skills, education and languages in a narrow column; a CV with several degrees makes it far taller
    than the main column, and since the sidebar cell cannot be shortened, the page break cut it in two (page 2 was a dark strip
    plus an empty main column). When the main column has room, Education belongs there. A CV that already fits one page keeps the
    familiar layout; otherwise the arrangement whose taller column is shorter wins. It is an estimate (see modern_column_estimates),
    judged in tests by the real PDFs' page counts.
    """
    if not profile.education:
        return False
    side, edu_side, main, edu_main = modern_column_estimates(profile, has_photo)
    in_side = max(side + edu_side, main)
    if in_side <= FITS_ONE_PAGE:
        return False
    in_main = max(side, main + edu_main)
    return in_main + 40.0 < in_side   # only move it when that clearly shortens the page


def render_cv_html(profile: CVProfile, template: str, photo: Optional[str] = None) -> str:
    """photo: a data URI produced by src.cv.photo.to_data_uri (anything else is ignored, so
    a caller can never smuggle an arbitrary URL or markup into the page through it)."""
    if template not in TEMPLATES:
        raise ValueError(f"Unknown template '{template}'. Must be one of {TEMPLATES}.")
    if photo is not None and not photo.startswith(DATA_URI_PREFIX):
        photo = None
    extra = {"edu_in_main": modern_education_in_main(profile, bool(photo))} if template == "modern" else {}
    return _env.get_template(f"{template}.html").render(profile=profile, photo=photo, **extra)


def render_cv_pdf(profile: CVProfile, template: str, photo: Optional[str] = None) -> bytes:
    """Render to PDF bytes by feeding the same HTML used for the on-screen
    preview through WeasyPrint, rather than maintaining a separate PDF
    template — mirrors src/report/render/pdf.py's approach.

    Import is local to keep callers that only need HTML/Markdown free of
    WeasyPrint's system-library dependency (Pango/GObject via Homebrew) at
    import time.
    """
    from weasyprint import HTML

    from src.service.pdf_fetch import deny_all_url_fetcher

    html = render_cv_html(profile, template, photo)
    return HTML(string=html, url_fetcher=deny_all_url_fetcher()).write_pdf()


def render_cv_markdown(profile: CVProfile) -> str:
    """Plain-text export, independent of the three visual templates — there
    is exactly one canonical Markdown shape for a CV, unlike HTML/PDF where
    the chosen template controls layout."""
    lines = [f"# {profile.name}"]
    if profile.role:
        lines.append(f"**{profile.role}**")
    lines.append("")

    contact_parts = [profile.contact.email]
    if profile.contact.phone:
        contact_parts.append(profile.contact.phone)
    if profile.contact.location:
        contact_parts.append(profile.contact.location)
    contact_parts.extend(profile.contact.links)
    lines.append(" | ".join(contact_parts))
    lines.append("")

    if profile.summary:
        lines += ["## Summary", "", profile.summary, ""]

    if profile.experience:
        lines.append("## Experience")
        lines.append("")
        for job in profile.experience:
            location_suffix = f", {job.location}" if job.location else ""
            lines.append(f"### {job.title}, {job.organization}{location_suffix} ({job.start} – {job.end})")
            for bullet in job.bullets:
                lines.append(f"- {bullet}")
            lines.append("")

    if profile.education:
        lines.append("## Education")
        lines.append("")
        for edu in profile.education:
            year_suffix = f", {edu.year}" if edu.year else ""
            lines.append(f"- {edu.degree} — {edu.institution}{year_suffix}")
        lines.append("")

    if profile.skills:
        lines += ["## Skills", "", ", ".join(profile.skills), ""]

    if profile.languages:
        lines += ["## Languages", "", ", ".join(profile.languages), ""]

    return "\n".join(lines).rstrip() + "\n"
