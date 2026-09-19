from src.report.render.html import render_html
from src.report.render.latex import render_latex
from src.report.render.markdown import render_markdown
from src.report.render.pdf import render_pdf

RENDERERS = {
    "markdown": render_markdown,
    "html": render_html,
    "latex": render_latex,
    "pdf": render_pdf,
}

__all__ = ["render_markdown", "render_html", "render_latex", "render_pdf", "RENDERERS"]
