"""A CV's profile photo.

Kept apart from the CVProfile on purpose: the profile is what gets sent to the
model to apply "describe changes" edits, and a base64 image in that JSON would
cost thousands of tokens per edit for nothing. The photo is stored separately and
only merged in at render time, as a data URI - which also makes the rendered HTML
and the PDF self-contained (the photo travels with a downloaded file).

Whatever is uploaded is decoded and RE-ENCODED, never stored as received: EXIF
(GPS, device, timestamps) is stripped, the result is always a plain square JPEG
we produced ourselves, and size/pixel counts are bounded.
"""
import base64
from io import BytesIO

MAX_UPLOAD_BYTES = 6 * 1024 * 1024
MAX_PIXELS = 16_000_000  # decompression-bomb guard, checked from the header before any decode (4000x4000; was 40M, which peaked at ~620 MB)
SIZE = 480               # square; shown at <= ~1.5in in print, so this is already generous
_WORK = 2 * SIZE         # the picture is shrunk to about this on its short side before anything else is done to it
_BAND_ROWS = 256         # rows converted at a time: a full-resolution RGBA/RGB copy of the whole picture is never made
DATA_URI_PREFIX = "data:image/jpeg;base64,"
# EXIF orientation -> PIL transpose method (the numbers of Image.Transpose, as in ImageOps.exif_transpose)
_TRANSPOSE = {2: 0, 3: 3, 4: 1, 5: 5, 6: 4, 7: 6, 8: 2}


def _shrunk_rgb(im, factor: int, has_alpha: bool):
    """The picture as RGB, reduced `factor` times and flattened onto white, built band by band.

    Converting the whole image first (RGBA copy, premultiplied copy for reduce(), flattened copy) held several full-size
    buffers at once; here only one band is ever converted, so the cost on top of the decoded pixels is the small result."""
    from PIL import Image
    w, h = im.size
    out = Image.new("RGB", (-(-w // factor), -(-h // factor)), (255, 255, 255))  # a CV page is white
    step = factor * max(1, _BAND_ROWS // factor)  # a multiple of factor, so bands reduce to exactly their share of the result
    for y in range(0, h, step):
        band = im.crop((0, y, w, min(h, y + step)))
        if has_alpha:
            band = band.convert("RGBA")
            small = band.reduce(factor) if factor > 1 else band
            out.paste(small, (0, y // factor), mask=small.getchannel("A"))
        else:
            band = band.convert("RGB")
            out.paste(band.reduce(factor) if factor > 1 else band, (0, y // factor))
    return out


def process_cv_photo(raw: bytes) -> bytes:
    """A centered-square, metadata-free JPEG. Raises ValueError for anything that
    isn't a plain raster photo (SVG, HTML, truncated or hostile files, ...)."""
    from PIL import Image, ImageOps, UnidentifiedImageError
    try:
        im = Image.open(BytesIO(raw))
        if im.format not in ("JPEG", "PNG", "WEBP", "GIF"):
            raise ValueError("unsupported format")
        if im.width * im.height > MAX_PIXELS:
            raise ValueError("image too large")
        im.seek(0)  # animated GIF/WebP: first frame only
        orientation = im.getexif().get(0x0112, 1)  # read now: the pixel work below produces new images that carry no EXIF
        if im.format == "JPEG":
            im.draft(None, (_WORK, _WORK))  # the decoder itself scales by 1/2, 1/4 or 1/8: the full-size bitmap is never built
        im.load()
        has_alpha = im.mode in ("RGBA", "LA", "PA") or (im.mode == "P" and "transparency" in im.info)
        im = _shrunk_rgb(im, max(1, min(im.size) // _WORK), has_alpha)
        if isinstance(orientation, int) and orientation in _TRANSPOSE:  # bake the rotation in (on the small copy); the tag is dropped below
            im = im.transpose(_TRANSPOSE[orientation])
        # Crop to a square. centering y at 0.4 rather than 0.5: in a portrait photo
        # the face is in the upper part, so a centered crop tends to cut the forehead.
        im = ImageOps.fit(im, (SIZE, SIZE), Image.LANCZOS, centering=(0.5, 0.4))
        im.info.clear()
        out = BytesIO()
        im.save(out, "JPEG", quality=85, optimize=True)
        return out.getvalue()
    except (UnidentifiedImageError, OSError, SyntaxError, Image.DecompressionBombError) as exc:
        raise ValueError(f"not a readable image: {exc.__class__.__name__}") from exc


def to_data_uri(jpeg: bytes) -> str:
    return DATA_URI_PREFIX + base64.b64encode(jpeg).decode("ascii")
