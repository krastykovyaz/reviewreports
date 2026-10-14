"""Text out of an uploaded PDF CV, so it can go through the same extraction as pasted text.

The upload is untrusted: it's size-capped by the caller, must really be a PDF, can't be
encrypted, and is bounded in pages and output length. Only the text is kept - the PDF
itself is never stored or re-served. A scanned CV (pages that are pictures) has no text
to read and is reported as such rather than sent to the model as empty or garbage input.
"""
import io
import re

from pypdf import PdfReader

MAX_UPLOAD_BYTES = 5 * 1024 * 1024
MAX_PAGES = 8
MAX_CHARS = 20_000   # same cap as pasted text (CreateCVFromTextRequest)
MIN_CHARS = 80       # less than this is a scan, a cover page, or noise - not a CV


class PdfError(ValueError):
    """code: not_pdf | encrypted | too_many_pages | no_text | unreadable"""
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def extract_pdf_text(raw: bytes) -> str:
    if b"%PDF-" not in raw[:1024]:
        raise PdfError("not_pdf")
    try:
        reader = PdfReader(io.BytesIO(raw), strict=False)
        if reader.is_encrypted:
            # An "encrypted" PDF with an empty user password (common for permission-only locks) opens fine.
            if not reader.decrypt(""):
                raise PdfError("encrypted")
        if len(reader.pages) > MAX_PAGES:
            raise PdfError("too_many_pages")
        parts = []
        for page in reader.pages:
            parts.append(page.extract_text() or "")
    except PdfError:
        raise
    except Exception:
        raise PdfError("unreadable")
    text = "\n".join(parts)
    text = re.sub(r"[ \t ]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if len(text) < MIN_CHARS:
        raise PdfError("no_text")
    return text[:MAX_CHARS]
