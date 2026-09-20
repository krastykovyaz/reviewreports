"""Lazy package init.

generate_report/supported_kinds (from src.review.generate) only import
run_website_audit eagerly; run_code_review/run_document_review are loaded on
first actual use of that kind (see src.review.generate), not at package-load
time, so importing this package doesn't require code_review's/document's
dependency chains to be installed.

run_code_review and run_document_review remain accessible as
`src.review.run_code_review` / `src.review.run_document_review` via
module-level __getattr__ (PEP 562) — same public surface as before, just
deferred until actually accessed.
"""

from src.review.generate import generate_report, supported_kinds

__all__ = ["run_code_review", "run_document_review", "generate_report", "supported_kinds"]


def __getattr__(name):
    if name == "run_code_review":
        from src.review.code_review import run_code_review

        return run_code_review
    if name == "run_document_review":
        from src.review.document import run_document_review

        return run_document_review
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
