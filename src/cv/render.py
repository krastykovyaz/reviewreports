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
from typing import Tuple

from jinja2 import Environment, FileSystemLoader, select_autoescape

from src.cv.schema import CVProfile

TEMPLATES: Tuple[str, ...] = ("modern", "classic", "compact")
OUTPUT_FORMATS: Tuple[str, ...] = ("html", "pdf", "markdown")

_TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "templates")
_env = Environment(loader=FileSystemLoader(_TEMPLATES_DIR), autoescape=select_autoescape(["html"]))


def render_cv_html(profile: CVProfile, template: str) -> str:
    if template not in TEMPLATES:
        raise ValueError(f"Unknown template '{template}'. Must be one of {TEMPLATES}.")
    return _env.get_template(f"{template}.html").render(profile=profile)


def render_cv_pdf(profile: CVProfile, template: str) -> bytes:
    """Render to PDF bytes by feeding the same HTML used for the on-screen
    preview through WeasyPrint, rather than maintaining a separate PDF
    template — mirrors src/report/render/pdf.py's approach.

    Import is local to keep callers that only need HTML/Markdown free of
    WeasyPrint's system-library dependency (Pango/GObject via Homebrew) at
    import time.
    """
    from weasyprint import HTML

    html = render_cv_html(profile, template)
    return HTML(string=html).write_pdf()


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
