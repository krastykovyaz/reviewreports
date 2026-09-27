"""Regression test for a real bug found while building the CV builder's
"paste your info" feature (which calls model_manager against a real DeepSeek
model): ModelManager.__call__ always forwarded `plugins` (an OpenRouter-only
extension) to every client, including ChatOpenAI - which then forwarded it
straight into the real OpenAI SDK's AsyncCompletions.create(), which doesn't
accept that keyword at all and raised a TypeError. Every real (non-mocked)
call to a non-OpenRouter model was broken before this fix; every existing
test mocked model_manager itself, so nothing had caught it.
"""

import pytest

from src.message.types import HumanMessage
from src.model.manager import ModelManager
from src.model.openai.chat import ChatOpenAI
from src.model.openrouter.chat import ChatOpenRouter
from src.model.types import LLMResponse, ModelConfig


@pytest.mark.asyncio
async def test_call_does_not_forward_plugins_to_non_openrouter_client(monkeypatch):
    captured = {}

    async def fake_call(self, **kwargs):
        captured.update(kwargs)
        return LLMResponse(success=True, message="ok")

    monkeypatch.setattr(ChatOpenAI, "__call__", fake_call)

    manager = ModelManager()
    manager.models["fake/openai-model"] = ModelConfig(model_name="fake/openai-model", model_type="chat/completions", model_id="fake", provider="openai")
    manager.model_clients["fake/openai-model"] = ChatOpenAI(model="fake")

    await manager(model="fake/openai-model", messages=[HumanMessage(content="hi")])
    assert "plugins" not in captured


@pytest.mark.asyncio
async def test_call_forwards_plugins_to_openrouter_client(monkeypatch):
    captured = {}

    async def fake_call(self, **kwargs):
        captured.update(kwargs)
        return LLMResponse(success=True, message="ok")

    monkeypatch.setattr(ChatOpenRouter, "__call__", fake_call)

    manager = ModelManager()
    manager.models["fake/openrouter-model"] = ModelConfig(model_name="fake/openrouter-model", model_type="chat/completions", model_id="fake", provider="openrouter")
    manager.model_clients["fake/openrouter-model"] = ChatOpenRouter(model="fake")

    await manager(model="fake/openrouter-model", messages=[HumanMessage(content="hi")], plugins=[{"id": "web"}])
    assert captured.get("plugins") == [{"id": "web"}]
