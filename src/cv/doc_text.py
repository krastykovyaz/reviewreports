"""Text out of an uploaded CV document: PDF or Word (.docx).

Same rules as for PDFs (see pdf_text.py): untrusted input, bounded size, only the text is
kept. A .docx is a zip of XML; it's read with the standard library (no extra dependency),
with the zip's declared size checked before anything is inflated. The old binary .doc format
(and a password-protected .docx, which is stored the same way) isn't readable here and is
reported as such, so the person knows to save it as .docx or PDF.
"""
import io
import re
import zipfile
import xml.etree.ElementTree as ET

from src.cv.pdf_text import MAX_CHARS, MIN_CHARS, PdfError, extract_pdf_text

MAX_XML_BYTES = 10 * 1024 * 1024   # inflated size of any one part we read
MAX_ZIP_ENTRIES = 500
_OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"  # legacy .doc, and password-protected .docx
_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


class DocError(ValueError):
    """code: unsupported | legacy | encrypted | too_many_pages | no_text | unreadable"""
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _paragraph_lines(xml_bytes: bytes) -> list:
    root = ET.fromstring(xml_bytes)
    lines = []
    for p in root.iter(_W + "p"):
        parts = []
        for el in p.iter():
            if el.tag == _W + "t" and el.text:
                parts.append(el.text)
            elif el.tag == _W + "tab":
                parts.append("\t")
            elif el.tag == _W + "br":
                parts.append("\n")
        line = "".join(parts).strip()
        # A text box is stored twice (modern + fallback copy): drop the immediate repeat.
        if line and (not lines or lines[-1] != line):
            lines.append(line)
    return lines


def _docx_text(raw: bytes) -> str:
    try:
        zf = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile:
        raise DocError("unreadable")
    infos = zf.infolist()
    if len(infos) > MAX_ZIP_ENTRIES:
        raise DocError("unreadable")
    names = {i.filename: i for i in infos}
    if "word/document.xml" not in names:
        raise DocError("unsupported")  # a zip, but not a Word document
    # Contact details often sit in the page header/footer, so read those too (header first).
    wanted = sorted(n for n in names if re.fullmatch(r"word/header\d*\.xml", n))
    wanted += ["word/document.xml"] + sorted(n for n in names if re.fullmatch(r"word/footer\d*\.xml", n))
    lines = []
    try:
        for n in wanted:
            if names[n].file_size > MAX_XML_BYTES:
                raise DocError("unreadable")
            lines += _paragraph_lines(zf.read(n))
    except DocError:
        raise
    except Exception:
        raise DocError("unreadable")
    return "\n".join(lines)


def extract_document_text(raw: bytes) -> str:
    """Dispatches on content, not on the file name or the browser's claimed type."""
    head = raw[:1024]
    if b"%PDF-" in head:
        try:
            return extract_pdf_text(raw)
        except PdfError as e:
            raise DocError({"not_pdf": "unsupported"}.get(e.code, e.code))
    if head[:8] == _OLE_MAGIC:
        raise DocError("legacy")
    if head[:2] == b"PK":
        text = re.sub(r"[ \t ]+", " ", _docx_text(raw))
        text = re.sub(r"\n{3,}", "\n\n", text).strip()
        if len(text) < MIN_CHARS:
            raise DocError("no_text")
        return text[:MAX_CHARS]
    raise DocError("unsupported")
