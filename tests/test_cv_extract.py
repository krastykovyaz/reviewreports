import pytest

from src.cv.extract import extract_cv_profile
from src.cv.schema import Contact, CVProfile, Experience
from src.model.types import LLMExtra, LLMResponse


def _sample_profile() -> CVProfile:
    return CVProfile(
        name="Jordan Reyes",
        role="Senior Backend Engineer",
        contact=Contact(email="jordan.reyes@email.com"),
        experience=[Experience(title="Senior Backend Engineer", organization="Northwind Systems", start="2021", end="Present", bullets=["Redesigned the payments pipeline."])],
    )


@pytest.mark.asyncio
async def test_extract_cv_profile_returns_parsed_model(monkeypatch):
    profile = _sample_profile()

    async def fake_model_manager(model, messages, response_format=None, **kwargs):
        assert response_format is CVProfile
        assert model == "deepseek/deepseek-chat"
        return LLMResponse(success=True, message="", extra=LLMExtra(parsed_model=profile))

    monkeypatch.setattr("src.cv.extract.model_manager", fake_model_manager)
    result = await extract_cv_profile("Jordan Reyes, Senior Backend Engineer at Northwind Systems...", "deepseek/deepseek-chat")
    assert result == profile


@pytest.mark.asyncio
async def test_extract_cv_profile_returns_none_on_exception(monkeypatch):
    async def raise_error(model, messages, response_format=None, **kwargs):
        raise RuntimeError("model unavailable")

    monkeypatch.setattr("src.cv.extract.model_manager", raise_error)
    result = await extract_cv_profile("some text", "deepseek/deepseek-chat")
    assert result is None


@pytest.mark.asyncio
async def test_extract_cv_profile_returns_none_on_failed_response(monkeypatch):
    async def fake_model_manager(model, messages, response_format=None, **kwargs):
        return LLMResponse(success=False, message="quota exceeded", extra=None)

    monkeypatch.setattr("src.cv.extract.model_manager", fake_model_manager)
    result = await extract_cv_profile("some text", "deepseek/deepseek-chat")
    assert result is None


@pytest.mark.asyncio
async def test_extract_cv_profile_returns_none_when_no_parsed_model(monkeypatch):
    async def fake_model_manager(model, messages, response_format=None, **kwargs):
        return LLMResponse(success=True, message="", extra=LLMExtra(parsed_model=None))

    monkeypatch.setattr("src.cv.extract.model_manager", fake_model_manager)
    result = await extract_cv_profile("some text", "deepseek/deepseek-chat")
    assert result is None
