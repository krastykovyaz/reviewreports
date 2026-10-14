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
MAX_PIXELS = 40_000_000  # decompression-bomb guard, checked from the header before any decode
SIZE = 480               # square; shown at <= ~1.5in in print, so this is already generous
DATA_URI_PREFIX = "data:image/jpeg;base64,"


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
        im = ImageOps.exif_transpose(im)  # bake the rotation in; the tag is dropped below
        if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
            rgba = im.convert("RGBA")
            flat = Image.new("RGB", rgba.size, (255, 255, 255))  # a CV page is white
            flat.paste(rgba, mask=rgba.split()[3])
            im = flat
        else:
            im = im.convert("RGB")
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
