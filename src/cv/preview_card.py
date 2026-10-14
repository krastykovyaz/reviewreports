"""The 1200x630 image shown when a CV link is shared (Telegram, WhatsApp, LinkedIn, Facebook, X).

Page 1 of the CV on the left, the person's name and role on the right. Built from our own
rendered PDF (or, for the "original" link, page 1 of the uploaded PDF), so the preview is what
the recipient will actually see. Kept small (a few tens of KB) because WhatsApp drops big images.
"""
import io
import os
import subprocess
import sys
from typing import List, Optional

from PIL import Image, ImageDraw, ImageFont

W, H = 1200, 630
MAX_BYTES = 250 * 1024
_BG = (238, 240, 243)
_INK = (26, 29, 35)
_MUTED = (91, 98, 112)
_FAINT = (140, 147, 160)
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_FONT_BOLD = ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"]
_FONT_REG = ["/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"]


class RasterError(RuntimeError):
    pass


def rasterize_first_page(pdf_bytes: bytes, target_height: int = 1200, timeout: int = 20) -> bytes:
    """PNG of page 1, rendered in a resource-limited child process (see raster_worker.py)."""
    try:
        r = subprocess.run([sys.executable, "-m", "src.cv.raster_worker", str(target_height)], input=pdf_bytes,
                           capture_output=True, timeout=timeout, cwd=_ROOT)
    except subprocess.TimeoutExpired:
        raise RasterError("timeout")
    if r.returncode != 0 or not r.stdout.startswith(b"\x89PNG"):
        raise RasterError("render failed")
    return r.stdout


def _font(paths: List[str], size: int):
    for p in paths:
        try:
            return ImageFont.truetype(p, size)
        except OSError:
            continue
    return ImageFont.load_default(size=size)


def _wrap(draw, text: str, font, max_w: int, max_lines: int) -> List[str]:
    words, lines, cur = text.split(), [], ""
    for w in words:
        trial = f"{cur} {w}".strip()
        if draw.textlength(trial, font=font) <= max_w:
            cur = trial
            continue
        if cur:
            lines.append(cur)
        cur = w
        while draw.textlength(cur, font=font) > max_w and len(cur) > 1:  # one very long word
            cur = cur[:-1]
    if cur:
        lines.append(cur)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        last = lines[-1]
        while last and draw.textlength(last + "…", font=font) > max_w:
            last = last[:-1]
        lines[-1] = last.rstrip() + "…"
    return lines


def compose_card(page_png: bytes, name: str, role: Optional[str]) -> bytes:
    canvas = Image.new("RGB", (W, H), _BG)
    page = Image.open(io.BytesIO(page_png)).convert("RGB")
    max_h, max_w = H - 72, 560
    scale = min(max_h / page.height, max_w / page.width)
    page = page.resize((max(1, int(page.width * scale)), max(1, int(page.height * scale))), Image.LANCZOS)
    x0, y0 = 48, (H - page.height) // 2
    d = ImageDraw.Draw(canvas)
    d.rectangle((x0 + 4, y0 + 6, x0 + page.width + 4, y0 + page.height + 6), fill=(214, 218, 224))  # soft shadow
    canvas.paste(page, (x0, y0))
    d.rectangle((x0 - 1, y0 - 1, x0 + page.width, y0 + page.height), outline=(210, 214, 220))

    tx = x0 + page.width + 56
    tw = W - tx - 56
    name_font, role_font, foot_font = _font(_FONT_BOLD, 58), _font(_FONT_REG, 30), _font(_FONT_BOLD, 26)
    y = 150
    for line in _wrap(d, (name or "CV").strip(), name_font, tw, 3):
        d.text((tx, y), line, font=name_font, fill=_INK)
        y += 70
    y += 14
    d.rectangle((tx, y, tx + 72, y + 5), fill=(37, 99, 235))
    y += 30
    for line in _wrap(d, (role or "").strip(), role_font, tw, 3) if role else []:
        d.text((tx, y), line, font=role_font, fill=_MUTED)
        y += 42
    d.text((tx, H - 76), "tsech.online", font=foot_font, fill=_FAINT)

    for q in (84, 76, 68, 58):
        out = io.BytesIO()
        canvas.save(out, "JPEG", quality=q, optimize=True, progressive=True)
        if out.tell() <= MAX_BYTES:
            break
    return out.getvalue()
