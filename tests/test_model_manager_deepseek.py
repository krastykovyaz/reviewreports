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
