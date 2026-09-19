import pytest

from src.review.generate import generate_report, supported_kinds


def test_supported_kinds_lists_all_registered_kinds():
    assert supported_kinds() == ["website_audit", "code_review", "resume_review", "presentation_review", "book_review"]


@pytest.mark.asyncio
async def test_generate_report_dispatches_to_handler(monkeypatch):
    calls = {}

    async def fake_handler(input, model_name=None, lang="en"):
        calls["input"] = input
        calls["model_name"] = model_name
        calls["lang"] = lang
        return "sentinel-report"

    monkeypatch.setitem(__import__("src.review.generate", fromlist=["_HANDLERS"])._HANDLERS, "website_audit", fake_handler)

    result = await generate_report(kind="website_audit", input="https://example.com", model_name="ollama/qwen3-30b", lang="ru")
    assert result == "sentinel-report"
    assert calls == {"input": "https://example.com", "model_name": "ollama/qwen3-30b", "lang": "ru"}


@pytest.mark.asyncio
async def test_generate_report_dispatches_document_flavor_via_partial(monkeypatch):
    calls = {}

    async def fake_run_document_review(input, flavor=None, model_name=None, lang="en"):
        calls["input"] = input
        calls["flavor"] = flavor
        calls["model_name"] = model_name
        calls["lang"] = lang
        return "sentinel-resume-report"

    monkeypatch.setattr("src.review.generate.run_document_review", fake_run_document_review)
    # generate.py binds `flavor` into the partial at import time, so re-create the
    # handler entry against the patched function to verify the dispatch plumbing itself.
    from functools import partial

    monkeypatch.setitem(
        __import__("src.review.generate", fromlist=["_HANDLERS"])._HANDLERS,
        "resume_review",
        partial(fake_run_document_review, flavor="resume"),
    )
    result = await generate_report(kind="resume_review", input="/path/to/resume.pdf", lang="fr")
    assert result == "sentinel-resume-report"
    assert calls == {"input": "/path/to/resume.pdf", "flavor": "resume", "model_name": None, "lang": "fr"}


@pytest.mark.asyncio
async def test_generate_report_rejects_unknown_kind():
    with pytest.raises(ValueError, match="Unsupported report kind"):
        await generate_report(kind="video_review", input="anything")
