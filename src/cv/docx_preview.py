"""A read-only HTML preview of an uploaded Word (.docx) file, so the original can be shown next to
our templates (a browser can't display .docx by itself).

Deliberately small and safe by construction: only paragraphs, headings, simple lists, bold/italic/
underline and tables are rendered, every piece of text is escaped, nothing is ever copied through
as markup, and no images, links, fields or macros are followed. It's a faithful-enough look at the
content and structure, not a pixel copy of Word's layout.
"""
import html
import io
import re
import zipfile
import xml.etree.ElementTree as ET

from src.cv.doc_text import MAX_XML_BYTES

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
MAX_BLOCKS = 3000


def _on(el) -> bool:
    """A run property like <w:b/> is on unless it says val=0/false."""
    return el is not None and el.get(_W + "val") not in ("0", "false", "off")


def _runs_html(p) -> str:
    out = []
    for r in p.iter(_W + "r"):
        rpr = r.find(_W + "rPr")
        bold = rpr is not None and _on(rpr.find(_W + "b"))
        ital = rpr is not None and _on(rpr.find(_W + "i"))
        und = rpr is not None and rpr.find(_W + "u") is not None and rpr.find(_W + "u").get(_W + "val") not in (None, "none")
        text = []
        for el in r:
            if el.tag == _W + "t" and el.text:
                text.append(html.escape(el.text))
            elif el.tag == _W + "tab":
                text.append("&emsp;")
            elif el.tag in (_W + "br", _W + "cr"):
                text.append("<br>")
        s = "".join(text)
        if not s:
            continue
        if bold: s = f"<b>{s}</b>"
        if ital: s = f"<i>{s}</i>"
        if und: s = f"<u>{s}</u>"
        out.append(s)
    return "".join(out)


def _para(p):
    """-> (kind, html) where kind is 'h1'..'h3' | 'li' | 'p', or None for an empty paragraph."""
    body = _runs_html(p)
    if not re.sub(r"<[^>]+>|&emsp;", "", body).strip():
        return None
    ppr = p.find(_W + "pPr")
    style = ""
    align = ""
    if ppr is not None:
        ps = ppr.find(_W + "pStyle")
        style = (ps.get(_W + "val") if ps is not None else "") or ""
        jc = ppr.find(_W + "jc")
        align = (jc.get(_W + "val") if jc is not None else "") or ""
        if ppr.find(_W + "numPr") is not None:
            return "li", body
    m = re.fullmatch(r"(?i)heading\s*([1-9])", style)
    kind = "h1" if style.lower() == "title" else (f"h{min(3, int(m.group(1)))}" if m else "p")
    if align in ("center", "right") and kind == "p":
        return "p", f'<span style="display:block;text-align:{align}">{body}</span>'
    return kind, body


def _blocks(root) -> list:
    out, in_list = [], False
    def flush():
        nonlocal in_list
        if in_list: out.append("</ul>"); in_list = False
    body = root.find(_W + "body")
    children = list(body) if body is not None else list(root)
    for el in children:
        if len(out) > MAX_BLOCKS:
            break
        if el.tag == _W + "p":
            r = _para(el)
            if r is None: continue
            kind, h = r
            if kind == "li":
                if not in_list: out.append("<ul>"); in_list = True
                out.append(f"<li>{h}</li>")
            else:
                flush(); out.append(f"<{kind}>{h}</{kind}>")
        elif el.tag == _W + "tbl":
            flush()
            rows = []
            for tr in el.iter(_W + "tr"):
                cells = []
                for tc in tr.findall(_W + "tc"):
                    parts = [r[1] for p in tc.iter(_W + "p") if (r := _para(p))]
                    cells.append("<td>" + "<br>".join(parts) + "</td>")
                rows.append("<tr>" + "".join(cells) + "</tr>")
            if rows: out.append("<table>" + "".join(rows) + "</table>")
    flush()
    return out


_CSS = """
  body { margin: 0; padding: 24px; background: #f2f3f5; font-family: Calibri, -apple-system, "Segoe UI", Helvetica, Arial, sans-serif; color: #1a1d23; font-size: 14px; line-height: 1.45; }
  .page { max-width: 680px; margin: 0 auto; background: #fff; padding: 40px 44px; border-radius: 4px; box-shadow: 0 1px 3px rgba(0,0,0,.08), 0 8px 24px rgba(0,0,0,.06); overflow-wrap: anywhere; }
  h1 { font-size: 22px; margin: 0 0 8px; } h2 { font-size: 16px; margin: 16px 0 6px; border-bottom: 1px solid #ccc; padding-bottom: 3px; } h3 { font-size: 14px; margin: 12px 0 4px; }
  p { margin: 0 0 6px; } ul { margin: 0 0 8px; padding-left: 22px; } li { margin: 0 0 2px; }
  table { border-collapse: collapse; width: 100%; margin: 6px 0; } td { vertical-align: top; padding: 3px 8px 3px 0; }
"""


def docx_to_html(raw: bytes) -> str:
    zf = zipfile.ZipFile(io.BytesIO(raw))
    names = {i.filename: i for i in zf.infolist()}
    parts = sorted(n for n in names if re.fullmatch(r"word/header\d*\.xml", n)) + ["word/document.xml"] \
        + sorted(n for n in names if re.fullmatch(r"word/footer\d*\.xml", n))
    blocks = []
    for n in parts:
        if n not in names or names[n].file_size > MAX_XML_BYTES:
            continue
        blocks += _blocks(ET.fromstring(zf.read(n)))
    return f'<!DOCTYPE html><html><head><meta charset="utf-8"><title>Original</title><style>{_CSS}</style></head><body><div class="page">{"".join(blocks)}</div></body></html>'
