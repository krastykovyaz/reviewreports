"""Single entry point for report generation, kind-dispatched.

Every review kind (website audit, code review, resume/presentation/book
review, ...) registers a handler here rather than each caller (service, tool,
agent) branching on kind itself.
"""

from functools import partial
from typing import Awaitable, Callable, Dict, Optional

from src.audit.website import run_website_audit
from src.report.schema import Report
from src.review.app_review import run_app_review
from src.review.code_review import run_code_review
from src.review.document import run_document_review

_HANDLERS: Dict[str, Callable[..., Awaitable[Report]]] = {
    "website_audit": run_website_audit,
    "code_review": run_code_review,
    "app_review": run_app_review,
    "resume_review": partial(run_document_review, flavor="resume"),
    "presentation_review": partial(run_document_review, flavor="presentation"),
    "book_review": partial(run_document_review, flavor="book"),
}


def supported_kinds() -> list:
    return list(_HANDLERS)


async def generate_report(kind: str, input: str, model_name: Optional[str] = None, lang: str = "en") -> Report:
    handler = _HANDLERS.get(kind)
    if handler is None:
        raise ValueError(f"Unsupported report kind: {kind!r}. Supported kinds: {supported_kinds()}")
    return await handler(input, model_name=model_name, lang=lang)
