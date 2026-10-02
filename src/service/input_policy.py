"""What an unauthenticated network caller may point a review kind at.

The kinds were written for local use (CLI / agent): code_review takes a git
URL *or a local directory*, app_review a directory, the document kinds a file
path. Exposed over HTTP with no auth, "a local path" means "any path on the
server" - reproduced end to end: POST /reports {"url": "/some/dir",
"kind": "app_review"} returned a report naming which files held secrets, and
a document kind on an arbitrary file extracted its full text for the model.

So from the network:
- website_audit: any URL (SSRF is enforced in the fetch layer, src/audit).
- code_review: a public http(s) git URL only.
- app_review / document kinds: a local path ONLY under a directory the
  operator has explicitly allowed with REVIEW_ALLOWED_ROOTS (os.pathsep
  separated). Unset = no local paths at all (fail closed).

Library and agent-tool callers (generate_report, the tools) are unaffected;
this gates only what the HTTP routes accept.
"""

import os
from typing import List
from urllib.parse import urlparse

from src.audit.http_client import SSRFBlocked, assert_public_url

LOCAL_PATH_KINDS = {"code_review", "app_review", "resume_review", "presentation_review", "book_review"}


class InputRejected(ValueError):
    """The input is not something a network caller may submit for this kind."""


def allowed_roots() -> List[str]:
    raw = os.getenv("REVIEW_ALLOWED_ROOTS", "")
    return [os.path.realpath(p) for p in raw.split(os.pathsep) if p.strip()]


def _is_under(path: str, root: str) -> bool:
    try:
        return os.path.commonpath([path, root]) == root
    except ValueError:  # different drives / mixed absolute+relative
        return False


def _is_http_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in ("http", "https") and bool(parsed.netloc) and not value.startswith("-")


async def validate_network_input(kind: str, value: str) -> None:
    value = value.strip()
    if kind == "website_audit":
        return

    if kind == "code_review" and _is_http_url(value):
        try:
            await assert_public_url(value)
        except SSRFBlocked as exc:
            raise InputRejected(str(exc)) from exc
        return

    if kind in LOCAL_PATH_KINDS and not _is_http_url(value):
        real = os.path.realpath(value)  # resolves symlinks and ".." before comparing
        if any(_is_under(real, root) for root in allowed_roots()):
            return
        # Same message whether or not the path exists: no filesystem oracle.
        if kind == "code_review":
            raise InputRejected("code_review accepts a public http(s) git URL from the network, not a local path.")
        raise InputRejected(f"{kind} does not accept local paths from the network. Upload a file, or ask the operator to allow a directory via REVIEW_ALLOWED_ROOTS.")

    raise InputRejected(f"{kind} does not accept this input.")
