import pytest

from src.model.manager import ModelManager
from src.model.openai.chat import ChatOpenAI


@pytest.mark.asyncio
async def test_deepseek_models_register_as_openai_compatible():
    manager = ModelManager()
    await manager._initialize_deepseek_models()

    for model_name, model_id in (("deepseek/deepseek-chat", "deepseek-chat"), ("deepseek/deepseek-reasoner", "deepseek-reasoner")):
        assert model_name in manager.models
        config = manager.models[model_name]
        assert config.provider == "openai"  # reuses ChatOpenAI's response_format/JSON-mode support
        assert config.model_id == model_id
        assert config.api_base == "https://api.deepseek.com"
        assert config.supports_vision is False  # DeepSeek chat/reasoner are text-only

        assert model_name in manager.model_clients
        assert isinstance(manager.model_clients[model_name], ChatOpenAI)


@pytest.mark.asyncio
async def test_deepseek_models_use_custom_api_base_from_env(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_BASE", "https://deepseek.internal.example/v1")
    manager = ModelManager()
    await manager._initialize_deepseek_models()
    assert manager.models["deepseek/deepseek-chat"].api_base == "https://deepseek.internal.example/v1"


@pytest.mark.asyncio
async def test_initialize_registers_deepseek_alongside_other_providers():
    manager = ModelManager()
    await manager.initialize()
    assert "deepseek/deepseek-chat" in manager.model_clients
    assert "deepseek/deepseek-reasoner" in manager.model_clients


@pytest.mark.asyncio
async def test_deepseek_structured_output_uses_json_mode_not_json_schema(monkeypatch):
    """DeepSeek rejects response_format=json_schema (400 "This response_format
    type is unavailable now"), which silently broke every structured-output
    call through it (CV extraction, LLM audit pillars). The client must send
    JSON mode + the schema in a system message, must not forward
    reasoning_effort (a 400 on non-reasoning models), and must still parse the
    reply into the pydantic model."""
    from types import SimpleNamespace

    from pydantic import BaseModel

    from src.message.types import HumanMessage

    class Profile(BaseModel):
        name: str
        skills: list = []

    manager = ModelManager()
    await manager._initialize_deepseek_models()
    client = manager.model_clients["deepseek/deepseek-chat"]
    assert client.supports_json_schema is False

    captured = {}

    async def fake_call_model(self, messages, **params):
        captured["messages"] = messages
        captured["params"] = params
        message = SimpleNamespace(content='```json\n{"name": "Jane Doe", "skills": ["Python"]}\n```', tool_calls=None)
        return SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason="stop")], usage=None)

    monkeypatch.setattr(ChatOpenAI, "_call_model", fake_call_model)
    resp = await client(messages=[HumanMessage(content="Jane Doe, Python developer")], response_format=Profile)

    assert captured["params"]["response_format"] == {"type": "json_object"}
    assert "reasoning_effort" not in captured["params"]
    assert captured["messages"][0]["role"] == "system"
    assert "JSON schema" in captured["messages"][0]["content"] and '"name"' in captured["messages"][0]["content"]
    assert resp.success is True
    assert resp.extra.parsed_model.name == "Jane Doe"
    assert resp.extra.parsed_model.skills == ["Python"]


@pytest.mark.asyncio
async def test_openai_models_keep_strict_json_schema(monkeypatch):
    """The json_object fallback is opt-in per model; plain OpenAI models still
    get strict structured outputs and no injected system message."""
    from types import SimpleNamespace

    from pydantic import BaseModel

    from src.message.types import HumanMessage

    class Profile(BaseModel):
        name: str

    client = ChatOpenAI(model="gpt-4o", api_key="test")
    captured = {}

    async def fake_call_model(self, messages, **params):
        captured["messages"] = messages
        captured["params"] = params
        message = SimpleNamespace(content='{"name": "Jane Doe"}', tool_calls=None)
        return SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason="stop")], usage=None)

    monkeypatch.setattr(ChatOpenAI, "_call_model", fake_call_model)
    resp = await client(messages=[HumanMessage(content="Jane Doe")], response_format=Profile)

    assert captured["params"]["response_format"]["type"] == "json_schema"
    assert captured["messages"][0]["role"] != "system"
    assert "reasoning_effort" not in captured["params"]  # gpt-4o is not a reasoning model
    assert resp.extra.parsed_model.name == "Jane Doe"
