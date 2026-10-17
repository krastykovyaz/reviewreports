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
MAX_XML_TOTAL_BYTES = 16 * 1024 * 1024  # ... and of all the parts of one file together
MAX_EXTRA_PARTS = 6                # header/footer parts read besides the body
MAX_PART_CHARS = 4 * MAX_CHARS     # text kept from any one part: far more than the 20k the model ever sees, but never unbounded
MAX_ZIP_ENTRIES = 500
_OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"  # legacy .doc, and password-protected .docx
_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


class DocError(ValueError):
    """code: unsupported | legacy | encrypted | too_many_pages | no_text | unreadable"""
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def read_part(zf: zipfile.ZipFile, info: zipfile.ZipInfo, budget: list) -> bytes:
    """Inflates a zip member WITHOUT trusting the size its header declares.

    A header can claim 1 KB for a member that really inflates to hundreds of MB; a plain zf.read() inflates before
    anything is checked (measured: a 395 KB upload cost ~260 MB of memory before being rejected). Here the member is
    read through a bounded reader that stops one byte past the allowance (the smaller of the per-part cap and what is
    left of the whole file's budget), so the cost is bounded by the caps whatever the header says.
    `budget` is a one-element list holding the bytes still allowed for this file."""
    allow = min(MAX_XML_BYTES, budget[0])
    with zf.open(info) as f:
        data = f.read(allow + 1)
    if len(data) > allow:
        raise DocError("unreadable")
    budget[0] -= len(data)
    return data


_XML_ENCODING_RE = re.compile(rb"^\s*<\?xml[^>]*?encoding\s*=\s*[\"']([^\"']+)", re.I)
_DTD_RE = re.compile(rb"<!\s*(?:doctype|entity)", re.I)


def parse_word_xml(data: bytes) -> ET.Element:
    """Parses one part of a Word file, refusing anything that can't be a Word-written part.

    Word never emits a DTD, but expat expands internal entities: a 9 MB part of nested entity definitions inflates to ~900 MB
    of text (measured: ~900 MB of memory) inside this one process. So a part with a DOCTYPE/ENTITY declaration is rejected on
    its raw bytes BEFORE parsing. The check has to see the bytes as written, so a part that isn't plain UTF-8/ASCII (NUL
    bytes = UTF-16/32; or an XML declaration naming another encoding, which expat would decode only after we looked) is
    rejected too: Word writes UTF-8."""
    m = _XML_ENCODING_RE.match(data[:200])
    if b"\x00" in data or (m and m.group(1).lower() not in (b"utf-8", b"us-ascii", b"ascii")) or _DTD_RE.search(data):
        raise DocError("unreadable")
    try:
        return ET.fromstring(data)
    except ET.ParseError:
        raise DocError("unreadable")


def word_parts(zf: zipfile.ZipFile) -> list:
    """The members worth reading, in reading order: headers, body, footers - with the header/footer count capped."""
    names = {i.filename: i for i in zf.infolist()}
    if "word/document.xml" not in names:
        raise DocError("unsupported")  # a zip, but not a Word document
    heads = sorted(n for n in names if re.fullmatch(r"word/header\d*\.xml", n))
    foots = sorted(n for n in names if re.fullmatch(r"word/footer\d*\.xml", n))
    if len(heads) + len(foots) > MAX_EXTRA_PARTS:
        raise DocError("unreadable")
    return [names[n] for n in heads + ["word/document.xml"] + foots]


def _paragraph_lines(xml_bytes: bytes) -> list:
    root = parse_word_xml(xml_bytes)
    lines = []
    kept = 0  # characters accumulated in this part: a legal but huge document can't build an unbounded string
    for p in root.iter(_W + "p"):
        if kept > MAX_PART_CHARS:
            break
        parts = []
        size = 0
        for el in p.iter():
            if size > MAX_PART_CHARS:
                break  # one enormous paragraph is cut off the same way
            if el.tag == _W + "t" and el.text:
                parts.append(el.text[:MAX_PART_CHARS])
                size += len(el.text)
            elif el.tag == _W + "tab":
                parts.append("\t")
            elif el.tag == _W + "br":
                parts.append("\n")
        line = "".join(parts).strip()
        # A text box is stored twice (modern + fallback copy): drop the immediate repeat.
        if line and (not lines or lines[-1] != line):
            lines.append(line)
            kept += len(line)
    return lines


def _docx_text(raw: bytes) -> str:
    try:
        zf = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile:
        raise DocError("unreadable")
    infos = zf.infolist()
    if len(infos) > MAX_ZIP_ENTRIES:
        raise DocError("unreadable")
    # Contact details often sit in the page header/footer, so read those too (header first).
    parts = word_parts(zf)
    budget = [MAX_XML_TOTAL_BYTES]
    lines = []
    try:
        for info in parts:
            lines += _paragraph_lines(read_part(zf, info, budget))
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
