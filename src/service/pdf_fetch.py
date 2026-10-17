"""The url_fetcher given to WeasyPrint for every PDF this service renders.

Our HTML is generated from templates plus escaped text and only ever needs inline resources (the photo is a data: URI), so
nothing may be fetched from anywhere else: not file:// (read a local file into the PDF), not http(s):// (a request from
this server's network position), not ftp://. Belt and braces behind the escaping and the templates themselves."""


def deny_all_url_fetcher():
    """A WeasyPrint url_fetcher that serves data: URIs and raises for everything else (WeasyPrint catches the error: the
    resource is skipped with a warning and the PDF is still produced). Imports WeasyPrint here, not at module level, for
    the same reason as the renderers: its system libraries (Pango) may be missing."""
    try:
        from weasyprint.urls import URLFetcher  # WeasyPrint >= 68
    except ImportError:
        URLFetcher = None
    if URLFetcher is not None:
        return URLFetcher(allowed_protocols=("data",))

    from weasyprint import default_url_fetcher  # older WeasyPrint: a plain function returning a dict

    def fetcher(url, *args, **kwargs):
        if not url.lower().startswith("data:"):
            raise ValueError(f"URI uses disallowed protocol: {url}")
        return default_url_fetcher(url, *args, **kwargs)

    return fetcher
