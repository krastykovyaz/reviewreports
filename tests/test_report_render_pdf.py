import pytest

try:
    from weasyprint import HTML  # noqa: F401

    _WEASYPRINT_AVAILABLE = True
except (ImportError, OSError):
    # ImportError: package not installed. OSError: installed but the system
    # libs (Pango/GObject via Homebrew) aren't present, which WeasyPrint
    # raises lazily on first real import of its internals.
    _WEASYPRINT_AVAILABLE = False

from tests.fixtures.tsech_report import build_tsech_report

pytestmark = pytest.mark.skipif(not _WEASYPRINT_AVAILABLE, reason="weasyprint / its system libs (Pango) are not installed")


def test_render_pdf_produces_valid_pdf_bytes():
    from src.report.render.pdf import render_pdf

    pdf_bytes = render_pdf(build_tsech_report())
    assert pdf_bytes.startswith(b"%PDF-")
    assert len(pdf_bytes) > 500
