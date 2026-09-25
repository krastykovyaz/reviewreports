from src.report.render.html import render_html
from src.report.schema import Report


def render_pdf(report: Report, include_footer: bool = True) -> bytes:
    """Render the report to PDF bytes by feeding the existing HTML template
    through WeasyPrint, rather than maintaining a separate PDF template.

    Import is local to keep this repo's other renderers (and any code that
    only needs markdown/html/latex) free of WeasyPrint's system-library
    dependency (Pango/GObject via Homebrew) at import time.
    """
    from weasyprint import HTML

    html = render_html(report, include_footer=include_footer)
    return HTML(string=html).write_pdf()
