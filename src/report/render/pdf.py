from src.report.render.html import render_html
from src.report.schema import Report


def render_pdf(report: Report, include_footer: bool = True) -> bytes:
    """Render the report to PDF bytes by feeding the existing HTML template
    through WeasyPrint, rather than maintaining a separate PDF template.

    include_footer is accepted for interface parity with the other renderers
    but ignored: PDFs are a "final", downloadable/printable document people
    save and share standalone, so they never carry the tsech footer,
    regardless of the footer= query param the caller passed.

    Import is local to keep this repo's other renderers (and any code that
    only needs markdown/html/latex) free of WeasyPrint's system-library
    dependency (Pango/GObject via Homebrew) at import time.
    """
    from weasyprint import HTML

    html = render_html(report, include_footer=False)
    return HTML(string=html).write_pdf()
