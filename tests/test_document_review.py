import pytest

from src.model.types import LLMExtra, LLMResponse
from src.report.schema import Status
from src.review.document import DocumentLLMReview, _collect_document_stats, collect_llm_review, run_document_review


def test_collect_document_stats_flags_thin_content(tmp_path):
    f = tmp_path / "resume.txt"
    f.write_text("Short.")
    pillar = _collect_document_stats(str(f), "Short.")
    checks = {c.check: c for c in pillar.findings}
    assert checks["Content volume"].status == Status.WARN
    assert pillar.score == 4.0


def test_collect_document_stats_ok_for_substantial_content(tmp_path):
    f = tmp_path / "resume.txt"
    text = "Experienced engineer. " * 50
    f.write_text(text)
    pillar = _collect_document_stats(str(f), text)
    checks = {c.check: c for c in pillar.findings}
    assert checks["Content volume"].status == Status.OK
    assert pillar.score == 8.0


@pytest.mark.asyncio
async def test_collect_llm_review_na_without_model():
    pillar = await collect_llm_review("some resume text", "resume", model_name=None)
    assert pillar.score is None
    assert pillar.findings[0].status == Status.NA


@pytest.mark.asyncio
async def test_collect_llm_review_na_for_empty_text():
    pillar = await collect_llm_review("   ", "resume", model_name="ollama/qwen3-30b")
    assert pillar.score is None
    assert "No text could be extracted" in pillar.findings[0].detail


@pytest.mark.asyncio
async def test_collect_llm_review_uses_flavor_specific_rubric(monkeypatch):
    review = DocumentLLMReview(summary="Solid resume.", strengths=["quantified impact"], weaknesses=["no summary section"], rating=8)
    captured = {}

    async def fake_model_manager(model, messages, response_format=None, **kwargs):
        captured["system_prompt"] = messages[0].content
        assert response_format is DocumentLLMReview
        return LLMResponse(success=True, message="", extra=LLMExtra(parsed_model=review))

    monkeypatch.setattr("src.review.document.model_manager", fake_model_manager)
    pillar = await collect_llm_review("Experienced engineer with 5 years...", "resume", model_name="ollama/qwen3-30b")

    assert pillar.name == "Resume Review"
    assert pillar.score == 8.0
    assert pillar.findings[0].status == Status.OK
    assert "quantified impact" in pillar.findings[0].detail
    assert "recruiter" in captured["system_prompt"].lower()


@pytest.mark.asyncio
async def test_collect_llm_review_degrades_on_failure(monkeypatch):
    async def failing_model_manager(model, messages, response_format=None, **kwargs):
        raise RuntimeError("connection refused")

    monkeypatch.setattr("src.review.document.model_manager", failing_model_manager)
    pillar = await collect_llm_review("some text here", "book", model_name="ollama/qwen3-30b")
    assert pillar.score is None
    assert pillar.name == "Book/Manuscript Review"
    assert pillar.findings[0].status == Status.NA


@pytest.mark.asyncio
async def test_run_document_review_end_to_end_without_model(tmp_path):
    f = tmp_path / "resume.txt"
    f.write_text("Jane Doe\nSoftware Engineer\n" + "Built things. " * 30)

    report = await run_document_review(str(f), flavor="resume")
    assert report.meta.kind == "resume_review"
    assert len(report.pillars) == 2
    assert report.pillars[1].name == "Resume Review"
    assert report.pillars[1].score is None
    assert report.overall_score == report.pillars[0].score
    assert report.verdict


@pytest.mark.asyncio
async def test_run_document_review_rejects_unknown_flavor(tmp_path):
    f = tmp_path / "doc.txt"
    f.write_text("content")
    with pytest.raises(ValueError, match="Unknown document review flavor"):
        await run_document_review(str(f), flavor="poem")


@pytest.mark.asyncio
async def test_run_document_review_rejects_missing_file():
    with pytest.raises(ValueError, match="Not a file"):
        await run_document_review("/no/such/file.pdf", flavor="resume")
