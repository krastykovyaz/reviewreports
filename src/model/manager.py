"""
Model manager for OpenAI models.
Provides a unified interface for registering and invoking OpenAI models.
"""

import os
import re
import time
from typing import Optional, Dict, List, Any, Tuple, Union, TYPE_CHECKING
from pydantic import BaseModel

from dotenv import load_dotenv
load_dotenv(verbose=True)

from src.model.types import ModelConfig, LLMResponse, LLMExtra
from src.model.openai.chat import ChatOpenAI
from src.model.ollama.chat import ChatOllama
from src.model.openai.response import ResponseOpenAI
from src.model.openai.transcribe import TranscribeOpenAI
from src.model.openai.embedding import EmbeddingOpenAI
from src.model.openrouter.chat import ChatOpenRouter
from src.model.anthropic.chat import ChatAnthropic
from src.model.google.chat import ChatGoogle
from src.model.huggingface.chat import ChatHuggingFace
from src.message.types import Message
from src.logger import logger
from src.model_defaults import (
    DEFAULT_MODEL,
    GEMINI_CHAIN,
    GOOGLE_DIRECT_EXTRA,
    LEGACY_ALIASES,
    NOT_FOUND_COOLDOWN_S,
    google_name,
    openrouter_name,
    plugins_name,
)

if TYPE_CHECKING:
    from src.tool.types import Tool


class ModelManager:
    """
    Central registry and invoker for OpenAI models.

    Responsibilities:
    1. Register and store model configurations.
    2. Provide a unified invocation interface using ChatOpenAI or ResponseOpenAI.
    """
    
    def __init__(self):
        """Initialize the manager."""
        self.models: Dict[str, ModelConfig] = {}
        self.model_clients: Dict[str, Union[ChatOpenAI, ResponseOpenAI, TranscribeOpenAI, EmbeddingOpenAI, ChatOpenRouter, ChatAnthropic]] = {}
        # (provider, model_id) -> time.monotonic() until which a 404/NOT_FOUND model is skipped
        self._unavailable_until: Dict[Tuple[str, str], float] = {}
        self._warned_aliases: set = set()
        
        # Default parameters
        self.max_tokens: int = 16384
        self.default_temperature: float = 0.7
        self.default_reasoning: Dict[str, Any] = {
            "reasoning_effort": "high"
        }
        self.default_plugins: Optional[List[Dict[str, Any]]] = [
            {
                "id": "file-parser",
                "pdf": {
                    "engine": "mistral-ocr"
                }
            },
            {
                "id": "web", "max_results": 10
            },
            { 
                "id": "response-healing"
            }
        ]

    @property
    def registed_models(self):
        """Alias for backward compatibility: mapping of model name -> client instance."""
        return self.model_clients

    async def initialize(self):
        """Initialize the manager and register default models."""
        # Prefer Hugging Face as primary provider if HF_TOKEN is set
        await self._initialize_ollama_models()
        await self._initialize_huggingface_models()
        await self._initialize_openai_models()
        await self._initialize_deepseek_models()
        await self._initialize_openrouter_models()
        await self._initialize_anthropic_models()
        await self._initialize_google_models()
        logger.info(f"| Model manager initialized successfully with {len(self.models)} models.")
        if DEFAULT_MODEL not in self.models:
            logger.warning(f"| ⚠️ DEFAULT_MODEL={DEFAULT_MODEL!r} is not a registered model; callers relying on it will fail. Registered Gemini models: {[m for m in self.models if 'gemini' in m]}")
    
    async def _initialize_openai_models(self):
        """Initialize OpenAI models."""
        # General chat/completions models
        chat_models = [
            {
                "model_name": "openai/gpt-4o",
                "model_id": "gpt-4o",
                "model_type": "chat/completions",
                "temperature": self.default_temperature,
                "max_completion_tokens": self.max_tokens,
                "fallback_model": "openai/gpt-4.1"
            },
            {
                "model_name": "openai/gpt-4.1",
                "model_type": "chat/completions",
                "model_id": "gpt-4.1",
                "temperature": self.default_temperature,
                "max_completion_tokens": self.max_tokens,
                "fallback_model": "openai/gpt-4o",
            },
        ]
        
        # Responses API models
        response_models = [
            {
                "model_name": "openai/gpt-5",
                "model_type": "responses",
                "model_id": "gpt-5",
                "reasoning": self.default_reasoning,
                "max_output_tokens": self.max_tokens,
                "fallback_model": "openai/o3",
            },
            {
                "model_name": "openai/gpt-5.1",
                "model_type": "responses",
                "model_id": "gpt-5.1",
                "reasoning": self.default_reasoning,
                "max_output_tokens": self.max_tokens,
                "fallback_model": "openai/gpt-5",
            },
            {
                "model_name": "openai/o3",
                "model_type": "responses",
                "model_id": "o3",
                "reasoning": self.default_reasoning,
                "max_output_tokens": self.max_tokens,
                "fallback_model": "openai/gpt-5.1",
            }
        ]
        
        # Transcription models
        transcribe_models = [
            {
                "model_name": "openai/gpt-4o-transcribe",
                "model_type": "transcriptions",
                "model_id": "gpt-4o-transcribe",
                "fallback_model": "openai/gpt-4o-transcribe",
            }
        ]
        
        # Embedding models
        embedding_models = [
            {
                "model_name": "openai/text-embedding-3-small",
                "model_type": "embeddings",
                "model_id": "text-embedding-3-small",
                "fallback_model": "openai/text-embedding-3-large",
            },
            {
                "model_name": "openai/text-embedding-3-large",
                "model_type": "embeddings",
                "model_id": "text-embedding-3-large",
                "fallback_model": "openai/text-embedding-3-large",
            },
            {
                "model_name": "openai/text-embedding-ada-002",
                "model_type": "embeddings",
                "model_id": "text-embedding-ada-002",
                "fallback_model": "openai/text-embedding-3-large",
            },
        ]
        
        # Register chat models
        for model in chat_models:
            config = ModelConfig(
                model_name=model["model_name"],
                model_id=model["model_id"],
                model_type=model["model_type"],
                provider="openai",
                api_base=os.getenv("OPENAI_API_BASE"),
                api_key=os.getenv("OPENAI_API_KEY"),
                temperature=model.get("temperature"),
                max_completion_tokens=model.get("max_completion_tokens"),
                supports_streaming=True,
                supports_functions=True,
                supports_vision=True,
                output_version=None,
                fallback_model=model.get("fallback_model"),
            )
            self.models[config.model_name] = config
            await self._create_client(config)
        
        # Register response models
        for model in response_models:
            config = ModelConfig(
                model_name=model["model_name"],
                model_id=model["model_id"],
                model_type=model["model_type"],
                provider="openai",  
                api_base=os.getenv("OPENAI_API_BASE"),
                api_key=os.getenv("OPENAI_API_KEY"),
                reasoning=model.get("reasoning"),
                max_output_tokens=model.get("max_output_tokens"),
                supports_streaming=False,  # Responses API may not support streaming
                supports_functions=False,  # Responses API may not support tools
                supports_vision=True,
                output_version=None,
                fallback_model=model.get("fallback_model"),
            )
            self.models[config.model_name] = config
            await self._create_client(config)
        
        # Register transcription models
        for model in transcribe_models:
            config = ModelConfig(
                model_name=model["model_name"],
                model_id=model["model_id"],
                model_type=model["model_type"],
                provider="openai",
                api_base=os.getenv("OPENAI_API_BASE"),
                api_key=os.getenv("OPENAI_API_KEY"),
                supports_streaming=False,
                supports_functions=False,
                supports_vision=False,
                output_version=None,
                fallback_model=model.get("fallback_model"),
            )
            self.models[config.model_name] = config
            await self._create_client(config)
        
        # Register embedding models
        for model in embedding_models:
            config = ModelConfig(
                model_name=model["model_name"],
                model_id=model["model_id"],
                model_type=model["model_type"],
                provider="openai",
                api_base=os.getenv("OPENAI_API_BASE"),
                api_key=os.getenv("OPENAI_API_KEY"),
                supports_streaming=False,
                supports_functions=False,
                supports_vision=False,
                output_version=None,
                fallback_model=model.get("fallback_model"),
            )
            self.models[config.model_name] = config
            await self._create_client(config)
    
    async def _initialize_deepseek_models(self):
        """Initialize DeepSeek models.

        DeepSeek's API is OpenAI-protocol-compatible (chat/completions shape,
        including response_format), so this reuses ChatOpenAI itself rather
        than a new client class — just a distinct api_base/api_key and a
        "deepseek/" model_name prefix. Registered with provider="openai" so
        _create_client's existing ChatOpenAI branch picks it up unchanged.

        DeepSeek's chat/reasoner models are text-only (no vision), so the
        Design & UX pillar (which sends a screenshot) will not work with
        these models — use a vision-capable model for that pillar specifically.
        """
        chat_models = [
            {
                "model_name": "deepseek/deepseek-chat",
                "model_id": "deepseek-chat",
                "fallback_model": "deepseek/deepseek-reasoner",
            },
            {
                "model_name": "deepseek/deepseek-reasoner",
                "model_id": "deepseek-reasoner",
                "fallback_model": "deepseek/deepseek-chat",
            },
        ]
        for model in chat_models:
            config = ModelConfig(
                model_name=model["model_name"],
                model_id=model["model_id"],
                model_type="chat/completions",
                provider="openai",
                api_base=os.getenv("DEEPSEEK_API_BASE", "https://api.deepseek.com"),
                api_key=os.getenv("DEEPSEEK_API_KEY"),
                temperature=self.default_temperature,
                max_completion_tokens=self.max_tokens,
                supports_streaming=True,
                supports_functions=True,
                supports_vision=False,
                output_version=None,
                fallback_model=model.get("fallback_model"),
            )
            self.models[config.model_name] = config
            await self._create_client(config)

    async def _initialize_openrouter_models(self):
        """Initialize OpenRouter models (OpenAI models via OpenRouter)."""
        chat_models = [
            # OpenAI models
            {
                "model_name": "openrouter/gpt-4o",
                "model_id": "openai/gpt-4o",
                "model_type": "chat/completions",
                "temperature": self.default_temperature,
                "max_completion_tokens": self.max_tokens,
                "fallback_model": "openrouter/o3",
            },
            {
                "model_name": "openrouter/gpt-4.1",
                "model_id": "openai/gpt-4.1",
                "model_type": "chat/completions",
                "temperature": self.default_temperature,
                "max_completion_tokens": self.max_tokens,
                "fallback_model": "openrouter/o3",
            },
            {
                "model_name": "openrouter/gpt-5",
                "model_id": "openai/gpt-5",
                "model_type": "chat/completions",
                "reasoning": {
                    "reasoning": {
                        "enabled": True
                    }
                },
                "temperature": self.default_temperature,
                "max_completion_tokens": self.max_tokens,
                "fallback_model": "openrouter/o3",
            },
            {
                "model_name": "openrouter/gpt-5.1",
                "model_id": "openai/gpt-5.1",
                "model_type": "chat/completions",
                "reasoning": {
                    "reasoning": {
                        "enabled": True
                    }
                },
                "temperature": self.default_temperature,
                "max_completion_tokens": self.max_tokens,
                "fallback_model": "openrouter/o3",
            },
            {
                "model_name": "openrouter/gpt-5.2",
                "model_id": "openai/gpt-5.2",
                "model_type": "chat/completions",
                "reasoning": {
                    "reasoning": {
                        "enabled": True
                    }
                },
                "temperature": self.default_temperature,
                "max_completion_tokens": self.max_tokens,
                "fallback_model": "openrouter/o3",
            },
            {
                "model_name": "openrouter/o3",
                "model_id": "openai/o3",
                "model_type": "chat/completions",
                "reasoning": {
                    "reasoning": {
                        "enabled": True
                    }
                },
                "temperature": 1.0,
                "max_completion_tokens": self.max_tokens,
                "fallback_model": "openrouter/o3",
            },
            # Anthropic models
            {
                "model_name": "openrouter/claude-sonnet-3.5",
                "model_id": "anthropic/claude-3.5-sonnet",
                "model_type": "chat/completions",
                "reasoning": {
                    "reasoning": {
                        "enabled": True
                    }
                },
                "temperature": self.default_temperature,
                "max_completion_tokens": self.max_tokens,
                "fallback_model": "openrouter/o3",
            },
            {
                "model_name": "openrouter/claude-sonnet-3.7",
                "model_id": "anthropic/claude-3.7-sonnet",
                "model_type": "chat/completions",
                "reasoning": {
                    "reasoning": {
                        "enabled": True
                    }
                },
                "temperature": self.default_temperature,
                "max_completion_tokens": self.max_tokens,
                "fallback_model": "openrouter/o3",
            },
            {
                "model_name": "openrouter/claude-sonnet-4",
                "model_id": "anthropic/claude-sonnet-4",
                "model_type": "chat/completions",
                "reasoning": {
                    "reasoning": {
                        "enabled": True
                    }
                },
                "temperature": self.default_temperature,
                "max_completion_tokens": self.max_tokens,
                "fallback_model": "openrouter/o3",
            },
            {
                "model_name": "openrouter/claude-opus-4",
                "model_id": "anthropic/claude-opus-4",
                "model_type": "chat/completions",
                "reasoning": {
                    "reasoning": {
                        "enabled": True
                    }
                },
                "temperature": self.default_temperature,
                "max_completion_tokens": self.max_tokens,
                "fallback_model": "openrouter/o3",
            },
            {
                "model_name": "openrouter/claude-sonnet-4.5",
                "model_id": "anthropic/claude-sonnet-4.5",
                "model_type": "chat/completions",
                "reasoning": {
                    "reasoning": {
                        "enabled": True
                    }
                },
                "temperature": self.default_temperature,
                "max_completion_tokens": self.max_tokens,
                "fallback_model": "openrouter/o3",
            },
            {
                "model_name": "openrouter/claude-opus-4.5",
                "model_id": "anthropic/claude-opus-4.5",
                "model_type": "chat/completions",
                "reasoning": {
                    "reasoning": {
                        "enabled": True
                    }
                },
                "temperature": self.default_temperature,
                "max_completion_tokens": self.max_tokens,
                "fallback_model": "openrouter/o3",
            }
        ]

        # Gemini models for chat, vision, audio, video, pdf, etc. Names, order and
        # fallbacks all come from src/model_defaults.py (nothing is spelled out here).
        # Each model also gets a "-plugins" twin that sends default_plugins; its
        # fallback is the NEXT model's twin, so a 404 never silently drops the plugins.
        for i, bare in enumerate(GEMINI_CHAIN):
            next_bare = GEMINI_CHAIN[i + 1] if i + 1 < len(GEMINI_CHAIN) else None
            for with_plugins in (False, True):
                name = openrouter_name(bare)
                fallback = openrouter_name(next_bare) if next_bare else None
                entry = {
                    "model_name": plugins_name(name) if with_plugins else name,
                    "model_type": "chat/completions",
                    "model_id": f"google/{bare}",
                    "reasoning": {"reasoning": {"enabled": True}},
                    "temperature": self.default_temperature,
                    "max_completion_tokens": self.max_tokens,
                    "fallback_model": (plugins_name(fallback) if with_plugins else fallback) if fallback else None,
                }
                if with_plugins:
                    entry["plugins"] = self.default_plugins
                chat_models.append(entry)

        # Register OpenRouter models
        for model in chat_models:
            config = ModelConfig(
                model_name=model["model_name"],
                model_id=model["model_id"],
                model_type=model["model_type"],
                provider="openrouter",
                api_base=os.getenv("OPENROUTER_API_BASE", "https://openrouter.ai/api/v1"),
                api_key=os.getenv("OPENROUTER_API_KEY"),
                reasoning=model.get("reasoning") if model.get("reasoning") else None,
                plugins=model.get("plugins") if model.get("plugins") else None,
                temperature=model.get("temperature"),
                max_completion_tokens=model.get("max_completion_tokens"),
                supports_streaming=True,
                supports_functions=True,
                supports_vision=True,
                output_version=None,
                fallback_model=model.get("fallback_model"),
            )
            self.models[config.model_name] = config
            await self._create_client(config)
    
    async def _initialize_anthropic_models(self):
        """Initialize Anthropic models."""
        chat_models = [
            # Note: Claude 3.5 Sonnet is deprecated, use Claude 3.7 Sonnet or Claude Sonnet 4.5 instead
            # {
            #     "model_name": "anthropic/claude-sonnet-3.5",
            #     "model_id": "claude-3-5-sonnet-20240620",
            #     "model_type": "chat/completions",
            #     "temperature": self.default_temperature,
            #     "max_completion_tokens": self.max_tokens,
            #     "fallback_model": "anthropic/claude-sonnet-4.5",
            # },
            {
                "model_name": "anthropic/claude-sonnet-3.7",
                "model_id": "claude-3-7-sonnet-20250219",  # Anthropic model ID with version
                "model_type": "chat/completions",
                "temperature": self.default_temperature,
                "max_completion_tokens": self.max_tokens,
                "fallback_model": "anthropic/claude-sonnet-4.5",
            },
            {
                "model_name": "anthropic/claude-sonnet-4",
                "model_id": "claude-sonnet-4-20250514",  # Anthropic model ID with version
                "model_type": "chat/completions",
                "temperature": self.default_temperature,
                "max_completion_tokens": self.max_tokens,
                "fallback_model": "anthropic/claude-sonnet-4.5",
            },
            # {
            #     "model_name": "anthropic/claude-opus-4",
            #     "model_id": "claude-opus-4-20250514",  # Anthropic model ID with version
            #     "model_type": "chat/completions",
            #     "temperature": self.default_temperature,
            #     "max_completion_tokens": self.max_tokens,
            #     "fallback_model": "anthropic/claude-sonnet-4.5",
            # },
            {
                "model_name": "anthropic/claude-sonnet-4.5",
                "model_id": "claude-sonnet-4-5-20250929",  # Anthropic model ID with version
                "model_type": "chat/completions",
                "temperature": self.default_temperature,
                "max_completion_tokens": self.max_tokens,
                "fallback_model": "anthropic/claude-sonnet-4.5",
            },
            # {
            #     "model_name": "anthropic/claude-opus-4.5",
            #     "model_id": "claude-opus-4-1-20250805",  # Correct Anthropic model ID (Opus 4.1)
            #     "model_type": "chat/completions",
            #     "temperature": self.default_temperature,
            #     "max_completion_tokens": self.max_tokens,
            #    "fallback_model": "anthropic/claude-sonnet-4.5",
            # },
        ]
        
        # Register Anthropic models
        for model in chat_models:
            config = ModelConfig(
                model_name=model["model_name"],
                model_id=model["model_id"],
                model_type=model["model_type"],
                provider="anthropic",
                api_base=os.getenv("ANTHROPIC_API_BASE"),
                api_key=os.getenv("ANTHROPIC_API_KEY"),
                temperature=model.get("temperature"),
                max_completion_tokens=model.get("max_completion_tokens"),
                supports_streaming=True,
                supports_functions=True,
                supports_vision=True,
                output_version=None,
                fallback_model=model.get("fallback_model"),
            )
            self.models[config.model_name] = config
            await self._create_client(config)
    
    async def _initialize_google_models(self):
        """Initialize Google Gemini models."""
        chat_models: List[Dict[str, Any]] = []

        # Same chain as the OpenRouter route (ids here are the bare Google names);
        # the extras are callable on Google's own API but are in no fallback chain.
        for i, bare in enumerate(GEMINI_CHAIN):
            next_bare = GEMINI_CHAIN[i + 1] if i + 1 < len(GEMINI_CHAIN) else None
            chat_models.append(
                {
                    "model_name": google_name(bare),
                    "model_id": bare,
                    "model_type": "chat/completions",
                    "reasoning": {"reasoning": {"enabled": True}},
                    "temperature": self.default_temperature,
                    "max_completion_tokens": self.max_tokens,
                    "fallback_model": google_name(next_bare) if next_bare else None,
                }
            )
        for bare in GOOGLE_DIRECT_EXTRA:
            chat_models.append(
                {
                    "model_name": google_name(bare),
                    "model_id": bare,
                    "model_type": "chat/completions",
                    "reasoning": {"reasoning": {"enabled": True}},
                    "temperature": self.default_temperature,
                    "max_completion_tokens": self.max_tokens,
                    "fallback_model": google_name(GEMINI_CHAIN[0]),
                }
            )

        # Register Google models
        for model in chat_models:
            config = ModelConfig(
                model_name=model["model_name"],
                model_id=model["model_id"],
                model_type=model["model_type"],
                provider="google",
                api_base=model.get("api_base", None),
                api_key=model.get("api_key", os.getenv("GOOGLE_API_KEY")),
                reasoning=model.get("reasoning"),
                temperature=model.get("temperature"),
                max_completion_tokens=model.get("max_completion_tokens"),
                supports_streaming=True,
                supports_functions=True,
                supports_vision=True,
                output_version=None,
                fallback_model=model.get("fallback_model", None),
            )
            self.models[config.model_name] = config
            await self._create_client(config)

        # Register optional in-house Gemma model if configured via environment variables
        gemma_api_base = os.getenv("GEMMA_API_BASE")
        gemma_api_key = os.getenv("GEMMA_API_KEY")
        if gemma_api_base or gemma_api_key:
            gemma_config = ModelConfig(
                model_name="google/gemma-4-E2B-it",
                model_id="gemma-4-E2B-it",
                model_type="chat/completions",
                provider="google",
                api_base=gemma_api_base,
                api_key=gemma_api_key or os.getenv("GOOGLE_API_KEY"),
                reasoning=None,
                temperature=self.default_temperature,
                max_completion_tokens=self.max_tokens,
                supports_streaming=True,
                supports_functions=True,
                supports_vision=True,
                output_version=None,
                fallback_model=None,
            )
            self.models[gemma_config.model_name] = gemma_config
            await self._create_client(gemma_config)

    async def _initialize_huggingface_models(self):
        """Initialize Hugging Face models using HF_TOKEN/HF_MODEL env vars."""
        chat_models = [
            {
                "model_name": "huggingface/default",
                "model_id": os.getenv("HF_MODEL", "gpt2"),
                "model_type": "chat/completions",
                "temperature": self.default_temperature,
                "max_completion_tokens": self.max_tokens,
                "api_base": os.getenv("HF_API_BASE", "https://api-inference.huggingface.co"),
                "api_key": os.getenv("HF_TOKEN"),
            }
        ]

        for model in chat_models:
            config = ModelConfig(
                model_name=model["model_name"],
                model_id=model["model_id"],
                model_type=model["model_type"],
                provider="huggingface",
                api_base=model.get("api_base"),
                api_key=model.get("api_key"),
                temperature=model.get("temperature"),
                max_completion_tokens=model.get("max_completion_tokens"),
                supports_streaming=False,
                supports_functions=False,
                supports_vision=False,
                output_version=None,
                fallback_model=None,
            )
            self.models[config.model_name] = config
            await self._create_client(config)

    async def _initialize_ollama_models(self):
        ollama_base = os.getenv("OLLAMA_API_BASE", "http://localhost:11434")
        models = [
            {
                "model_name": "ollama/gemma4-e2b",
                "model_id": "gemma4:e2b",
                "max_tokens": 512,
            },
            {
                "model_name": "ollama/qwen3-30b",
                "model_id": "qwen3:30b",
            },
        ]
        for model in models:
            config = ModelConfig(
                model_name=model["model_name"],
                model_id=model["model_id"],
                model_type="chat/completions",
                provider="ollama",
                api_base=ollama_base,
                api_key="ollama",  # Ollama игнорирует ключ, но поле required
                temperature=self.default_temperature,
                max_completion_tokens=self.max_tokens,
                supports_streaming=True,
                supports_functions=True,
                supports_vision=False,
                output_version=None,
                fallback_model=None,
            )
            self.models[config.model_name] = config
            await self._create_client(config)

    async def _create_client(self, config: ModelConfig) -> None:
        """Create and cache a client for the given model config."""
        if config.provider == "openrouter":
            # OpenRouter models (only chat/completions supported for now)
            if config.model_type == "chat/completions":
                client = ChatOpenRouter(
                    model=config.model_id,
                    api_key=config.api_key,
                    base_url=config.api_base,
                    reasoning=config.reasoning if config.reasoning else None,
                    plugins=config.plugins if config.plugins else None,
                    temperature=config.temperature or self.default_temperature,
                    max_completion_tokens=config.max_completion_tokens or self.max_tokens,
                    http_referer=os.getenv("OPENROUTER_HTTP_REFERER"),
                    x_title=os.getenv("OPENROUTER_X_TITLE"),
                )
                logger.info(f"| Created ChatOpenRouter client for {config.model_name}")
            else:
                raise ValueError(f"Unsupported model type {config.model_type} for OpenRouter provider")
        elif config.provider == "ollama":
            if config.model_type == "chat/completions":
                client = ChatOllama(
                    model=config.model_id,
                    api_key=config.api_key,
                    base_url=config.api_base,
                    temperature=config.temperature or self.default_temperature,
                    max_completion_tokens=config.max_completion_tokens or self.max_tokens,
                )
                logger.info(f"| Created ChatOllama client for {config.model_name}")
        elif config.provider == "anthropic":
            # Anthropic models (only chat/completions supported for now)
            if config.model_type == "chat/completions":
                client = ChatAnthropic(
                    model=config.model_id,
                    api_key=config.api_key,
                    base_url=config.api_base,
                    reasoning=config.reasoning if config.reasoning else None,
                    temperature=config.temperature or self.default_temperature,
                    max_tokens=config.max_completion_tokens or self.max_tokens,
                )
                logger.info(f"| Created ChatAnthropic client for {config.model_name}")
            else:
                raise ValueError(f"Unsupported model type {config.model_type} for Anthropic provider")
        elif config.provider == "huggingface":
            # Hugging Face models (text-generation via Inference API)
            if config.model_type == "chat/completions":
                client = ChatHuggingFace(
                    model=config.model_id,
                    api_key=config.api_key,
                    api_base=config.api_base or os.getenv("HF_API_BASE", "https://api-inference.huggingface.co"),
                    temperature=config.temperature or self.default_temperature,
                    max_new_tokens=config.max_completion_tokens or self.max_tokens,
                )
                logger.info(f"| Created ChatHuggingFace client for {config.model_name}")
            else:
                raise ValueError(f"Unsupported model type {config.model_type} for Hugging Face provider")
        elif config.provider == "google":
            # Google Gemini models (only chat/completions supported for now)
            if config.model_type == "chat/completions":
                client = ChatGoogle(
                    model=config.model_id,
                    api_key=config.api_key,
                    api_base=config.api_base,
                    reasoning=config.reasoning if config.reasoning else None,
                    temperature=config.temperature or self.default_temperature,
                    max_output_tokens=config.max_completion_tokens or self.max_tokens,
                )
                logger.info(f"| Created ChatGoogle client for {config.model_name}")
            else:
                raise ValueError(f"Unsupported model type {config.model_type} for Google provider")
        elif config.model_type == "responses":
            # Create ResponseOpenAI client
            client = ResponseOpenAI(
                model=config.model_id,
                api_key=config.api_key,
                base_url=config.api_base,
                reasoning=config.reasoning if config.reasoning else None,
                max_output_tokens=config.max_output_tokens or self.max_tokens,
            )
            logger.info(f"| Created ResponseOpenAI client for {config.model_name}")
        elif config.model_type == "transcriptions":
            # Create TranscribeOpenAI client
            client = TranscribeOpenAI(
                model=config.model_id,
                api_key=config.api_key,
                base_url=config.api_base,
            )
            logger.info(f"| Created TranscribeOpenAI client for {config.model_name}")
        elif config.model_type == "embeddings":
            # Create EmbeddingOpenAI client
            client = EmbeddingOpenAI(
                model=config.model_id,
                api_key=config.api_key,
                base_url=config.api_base,
            )
            logger.info(f"| Created EmbeddingOpenAI client for {config.model_name}")
        else:
            # Create ChatOpenAI client
            client = ChatOpenAI(
                model=config.model_id,
                api_key=config.api_key,
                base_url=config.api_base,
                temperature=config.temperature or self.default_temperature,
                reasoning=config.reasoning if config.reasoning else None,
                max_completion_tokens=config.max_completion_tokens or self.max_tokens,
            )
            logger.info(f"| Created ChatOpenAI client for {config.model_name}")
            
        self.model_clients[config.model_name] = client
    
    async def register_model(self, config: ModelConfig) -> None:
        """Register a new model configuration."""
        if config.provider not in ["openai", "openrouter", "anthropic", "google"]:
            raise ValueError(f"Only OpenAI, OpenRouter, Anthropic, and Google models are supported. Got provider: {config.provider}")
        
        self.models[config.model_name] = config
        await self._create_client(config)
        logger.info(f"Registered model: {config.model_name}")
    
    # ---- fallback chain / not-found cooldown ---------------------------------------

    _NOT_FOUND_RE = re.compile(
        r"Error code: 404|NOT_FOUND|\b404\b[^\n]{0,80}not found|is not found for API version"
        r"|not a valid model|no endpoints found|model [^\n]{0,80}(does not exist|is not available|has been (retired|deprecated|removed))",
        re.IGNORECASE,
    )

    def _resolve_alias(self, model: str) -> str:
        target = LEGACY_ALIASES.get(model)
        if target is None or model in self.models:
            return model
        if model not in self._warned_aliases:
            self._warned_aliases.add(model)
            logger.warning(f"| ⚠️ Model {model} is retired or unavailable; routing to {target}. Update the caller to use DEFAULT_MODEL.")
        return target

    def _fallback_chain(self, model: str) -> List[str]:
        """[model, its fallback, that one's fallback, ...] without repeats (several
        configs point at each other, and one pointed at itself)."""
        chain: List[str] = []
        name: Optional[str] = model
        while name and name not in chain and len(chain) < 8:
            chain.append(name)
            config = self.models.get(name)
            name = config.fallback_model if config else None
        return chain

    def _availability_key(self, name: str) -> Tuple[str, str]:
        """Keyed by provider + model id, not registry name: 'X' and 'X-plugins' are the
        same remote model, so one 404 must skip both."""
        config = self.models.get(name)
        return (config.provider, config.model_id) if config else ("?", name)

    def _skipped_until(self, name: str) -> float:
        return self._unavailable_until.get(self._availability_key(name), 0.0)

    def _mark_unavailable(self, name: str, reason: str) -> None:
        self._unavailable_until[self._availability_key(name)] = time.monotonic() + NOT_FOUND_COOLDOWN_S
        logger.warning(
            f"| ⚠️ Model {name} returned 404/NOT_FOUND ({str(reason)[:160]}); skipping it for {int(NOT_FOUND_COOLDOWN_S // 60)} min "
            f"instead of retrying it on every request."
        )

    @classmethod
    def _is_not_found(cls, outcome: Any) -> bool:
        """True for a 404/NOT_FOUND failure, whether raised or returned: the provider
        clients return LLMResponse(success=False) rather than raising on API errors."""
        if isinstance(outcome, BaseException):
            return bool(cls._NOT_FOUND_RE.search(str(outcome)))
        if not isinstance(outcome, LLMResponse) or outcome.success:
            return False
        data = outcome.extra.data if outcome.extra and isinstance(outcome.extra.data, dict) else {}
        return data.get("status_code") == 404 or bool(cls._NOT_FOUND_RE.search(outcome.message or ""))

    async def _invoke(
        self,
        name: str,
        messages: List[Message],
        tools: Optional[List["Tool"]],
        response_format: Optional[Union[BaseModel, Dict]],
        stream: bool,
        plugins: Optional[List[Dict[str, Any]]],
        kwargs: Dict[str, Any],
    ) -> LLMResponse:
        client = self.model_clients.get(name)
        if not client:
            raise ValueError(f"Model {name} not found. Available models: {list(self.models.keys())}")
        model_config = self.models.get(name)

        if model_config and model_config.model_type == "transcriptions":
            # Transcription models use messages parameter (extracts audio from messages)
            if messages is None:
                raise ValueError("messages parameter is required for transcription models")
            return await client(messages=messages, **kwargs)
        if model_config and model_config.model_type == "embeddings":
            # Embedding models use messages parameter (extracts text from messages)
            # Filter out unsupported parameters (tools, response_format, stream)
            embedding_kwargs = {k: v for k, v in kwargs.items() if k not in ["tools", "response_format", "stream"]}
            if messages is None:
                raise ValueError("messages parameter is required for embedding models")
            return await client(messages=messages, **embedding_kwargs)

        # Chat/response models use messages parameter
        if messages is None:
            raise ValueError("messages parameter is required for chat/response models")
        # `plugins` is an OpenRouter-specific extension - forwarding it (even as None)
        # to any other provider's client reaches their underlying SDK call, which
        # doesn't accept that keyword at all and raises a TypeError, not a no-op.
        chat_kwargs: Dict[str, Any] = dict(messages=messages, tools=tools, response_format=response_format, stream=stream, **kwargs)
        if isinstance(client, ChatOpenRouter):
            chat_kwargs["plugins"] = plugins
        return await client(**chat_kwargs)

    async def __call__(
        self,
        model: str,
        messages: List[Message],
        tools: Optional[List["Tool"]] = None,
        response_format: Optional[Union[BaseModel, Dict]] = None,
        stream: bool = False,
        plugins: Optional[List[Dict[str, Any]]] = None,
        **kwargs: Any,
    ) -> LLMResponse:
        """
        Invoke a model asynchronously, walking its fallback chain.

        A model that fails with an exception, or answers 404/NOT_FOUND (retired,
        renamed, or an id that never existed), moves the call on to its
        `fallback_model`; a 404 model is then skipped for NOT_FOUND_COOLDOWN_S so
        it is not retried on every request. Other error *responses* are returned
        as-is, as before. Every call logs which model actually answered.

        Args:
            model: Model name
            messages: List of Message objects (for transcription models, should contain ContentPartAudio)
            tools: Optional list of Tool instances
            response_format: Optional response format (Pydantic model or dict)
            stream: Whether to stream the response
            **kwargs: Additional parameters

        Returns:
            LLMResponse with formatted message
        """
        # Validate that tools and response_format are not both provided
        if tools and response_format:
            raise ValueError("tools and response_format cannot be used together")

        requested = model
        model = self._resolve_alias(model)
        chain = self._fallback_chain(model)
        now = time.monotonic()
        candidates = [name for name in chain if self._skipped_until(name) <= now]
        if not candidates:
            waits = ", ".join(f"{n} ({int(self._skipped_until(n) - now)}s left)" for n in chain)
            logger.error(f"| Every model in the chain for {requested} is skipped after 404/NOT_FOUND: {waits}")
            return LLMResponse(success=False, message=f"No available model for {requested}: all of [{waits}] returned 404/NOT_FOUND recently.")

        failures: List[Tuple[str, str]] = []
        for name in candidates:
            try:
                result = await self._invoke(name, messages, tools, response_format, stream, plugins, kwargs)
            except Exception as exc:  # noqa: BLE001 - any failure moves on to the next model
                if self._is_not_found(exc):
                    self._mark_unavailable(name, str(exc))
                else:
                    logger.warning(f"| ⚠️ Model {name} failed ({type(exc).__name__}: {str(exc)[:160]}); trying the next model in its chain.")
                failures.append((name, str(exc)))
                continue

            if self._is_not_found(result):
                self._mark_unavailable(name, result.message)
                failures.append((name, result.message))
                continue

            config = self.models.get(name)
            detail = f"{config.provider}:{config.model_id}" if config else name
            if not isinstance(result, LLMResponse) or result.success:
                note = "" if name == requested else f" (requested {requested})"
                logger.info(f"| Model answered: {name} [{detail}]{note}")
            else:
                logger.warning(f"| ⚠️ Model {name} [{detail}] returned an error response (requested {requested}).")
            return result

        if len(failures) == 1:
            logger.error(f"Model invocation failed: {failures[0][1]}")
            return LLMResponse(success=False, message=failures[0][1])
        summary = "; ".join(f"{n}: {m[:200]}" for n, m in failures)
        logger.error(f"| Every model in the fallback chain for {requested} failed: {summary}")
        return LLMResponse(success=False, message=f"All models in the fallback chain for {requested} failed. {summary}")

    async def get_model_config(self, model: str) -> Optional[ModelConfig]:
        """Get the configuration for a model."""
        return await self.models.get(model)
    
    async def list(self) -> List[str]:
        """List all registered model names."""
        return list(self.models.keys())


    # Global singleton instance
model_manager = ModelManager()

