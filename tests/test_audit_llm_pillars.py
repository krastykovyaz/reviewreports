import pytest

from src.audit.collectors.content import ContentLLMReview, collect_content, flesch_reading_ease
from src.audit.collectors.ux import UXLLMReview, collect_ux
from src.model.types import LLMExtra, LLMResponse
from src.report.schema import Status

_PROSE = ("The quick brown fox jumps over the lazy dog. " * 12).strip()


def _facts(**overrides):
    base = {"bodyText": _PROSE, "wordCount": 108, "lang": "en"}
    base.update(overrides)
    return base


class FakePage:
    def __init__(self, screenshot_b64: str = "ZmFrZQ=="):
        self._screenshot_b64 = screenshot_b64

    async def screenshot(self, format: str = "jpeg", quality=None):
        return self._screenshot_b64


# ---- Content: deterministic -------------------------------------------------


def test_flesch_reading_ease_simple_prose_is_easy():
    assert flesch_reading_ease(_PROSE) > 80


def test_flesch_needs_enough_words():
    assert flesch_reading_ease("Too short.") is None


@pytest.mark.asyncio
async def test_content_flags_thin_copy_and_language_mismatch():
    pillar = await collect_content(_facts(bodyText="Sign In One idea is all you need. AI will do the rest. Build", wordCount=14, lang="ru"))
    checks = {f.check: f for f in pillar.findings}
    assert checks["Content volume"].status == Status.WARN and checks["Content volume"].fix
    assert checks["Language consistency"].status == Status.WARN
    assert checks["Readability"].status == Status.NA
    assert checks["LLM tone/clarity review"].status == Status.NA


@pytest.mark.asyncio
async def test_content_substantial_prose_scores_well():
    pillar = await collect_content(_facts(wordCount=400))
    checks = {f.check: f for f in pillar.findings}
    assert checks["Content volume"].status == Status.OK
    assert checks["Readability"].status == Status.OK
    assert checks["Language consistency"].status == Status.OK


# ---- Content: LLM path -----------------------------------------------------


@pytest.mark.asyncio
async def test_content_uses_llm_review_when_model_configured(monkeypatch):
    review = ContentLLMReview(clarity=8, tone="professional", marketing_effectiveness=7, issues=["No CTA above the fold"])

    async def fake_model_manager(model, messages, response_format=None, **kwargs):
        assert model == "ollama/qwen3-30b" and response_format is ContentLLMReview
        return LLMResponse(success=True, message="", extra=LLMExtra(parsed_model=review))

    monkeypatch.setattr("src.audit.collectors.content.model_manager", fake_model_manager)
    pillar = await collect_content(_facts(wordCount=400), model_name="ollama/qwen3-30b")
    llm = next(f for f in pillar.findings if f.check == "LLM tone/clarity review")
    assert llm.status == Status.OK and "clarity 8/10" in llm.detail
    assert llm.fix == "No CTA above the fold"
    assert pillar.score == 10.0


@pytest.mark.asyncio
async def test_content_degrades_gracefully_when_llm_call_raises(monkeypatch):
    async def failing_model_manager(model, messages, response_format=None, **kwargs):
        raise RuntimeError("connection refused")

    monkeypatch.setattr("src.audit.collectors.content.model_manager", failing_model_manager)
    pillar = await collect_content(_facts(wordCount=400), model_name="ollama/qwen3-30b")
    llm = next(f for f in pillar.findings if f.check == "LLM tone/clarity review")
    assert llm.status == Status.NA and "failed" in llm.detail.lower()
    assert pillar.score == 10.0  # deterministic checks all pass; N/A carries no weight


@pytest.mark.asyncio
async def test_content_degrades_when_response_unsuccessful(monkeypatch):
    async def unsuccessful_model_manager(model, messages, response_format=None, **kwargs):
        return LLMResponse(success=False, message="rate limited", extra=None)

    monkeypatch.setattr("src.audit.collectors.content.model_manager", unsuccessful_model_manager)
    pillar = await collect_content(_facts(), model_name="ollama/qwen3-30b")
    assert next(f for f in pillar.findings if f.check == "LLM tone/clarity review").status == Status.NA


# ---- UX: LLM path -----------------------------------------------------------


@pytest.mark.asyncio
async def test_ux_is_na_without_model_name():
    pillar = await collect_ux(FakePage(), model_name=None)
    assert pillar.score is None and pillar.findings[0].status == Status.NA


@pytest.mark.asyncio
async def test_ux_scores_from_screenshot_review(monkeypatch):
    review = UXLLMReview(visual_clarity=9, layout_quality=8, issues=[])
    captured = {}

    async def fake_model_manager(model, messages, response_format=None, **kwargs):
        captured["messages"] = messages
        assert response_format is UXLLMReview
        return LLMResponse(success=True, message="", extra=LLMExtra(parsed_model=review))

    monkeypatch.setattr("src.audit.collectors.ux.model_manager", fake_model_manager)
    pillar = await collect_ux(FakePage("ZmFrZQ=="), model_name="openrouter/gpt-4.1")
    assert pillar.score == pytest.approx(8.5) and pillar.findings[0].status == Status.OK
    image_parts = [p for p in captured["messages"][1].content if p.type == "image_url"]
    assert len(image_parts) == 1 and image_parts[0].image_url.url.startswith("data:image/jpeg;base64,")


@pytest.mark.asyncio
async def test_ux_degrades_gracefully_on_screenshot_failure():
    class BrokenScreenshotPage(FakePage):
        async def screenshot(self, format: str = "jpeg", quality=None):
            raise RuntimeError("no target attached")

    pillar = await collect_ux(BrokenScreenshotPage(), model_name="openrouter/gpt-4.1")
    assert pillar.score is None and pillar.findings[0].status == Status.NA
