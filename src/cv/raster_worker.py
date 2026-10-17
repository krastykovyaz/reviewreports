"""Rasterises page 1 of a PDF to PNG, in a separate short-lived process.

Run as `python -m src.cv.raster_worker <target_height>`: PDF bytes on stdin, PNG bytes on stdout.
It is a subprocess on purpose - an uploaded PDF is untrusted, and a native renderer crash or a
runaway allocation must take down this throwaway process (limited below), not the service.
"""
import io
import resource
import sys

MAX_WIDTH = 2000


def main() -> int:
    resource.setrlimit(resource.RLIMIT_AS, (1024 * 1024 * 1024, 1024 * 1024 * 1024))
    resource.setrlimit(resource.RLIMIT_CPU, (12, 12))
    target_h = int(sys.argv[1]) if len(sys.argv) > 1 else 1200
    import pypdfium2 as pdfium
    pdf = pdfium.PdfDocument(sys.stdin.buffer.read())
    try:
        page = pdf[0]
        w, h = page.get_size()
        if w <= 0 or h <= 0:
            return 2
        scale = min(target_h / h, MAX_WIDTH / w)
        img = page.render(scale=scale).to_pil().convert("RGB")
    finally:
        pdf.close()
    out = io.BytesIO()
    img.save(out, "PNG")
    sys.stdout.buffer.write(out.getvalue())
    return 0


if __name__ == "__main__":
    sys.exit(main())
