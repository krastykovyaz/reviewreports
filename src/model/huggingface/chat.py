from typing import Any, Optional, List, Dict
import os
import json
import httpx
from pydantic import BaseModel, ConfigDict

from src.model.types import LLMResponse, LLMExtra
from src.message.types import Message, HumanMessage, SystemMessage, AssistantMessage
from src.logger import logger


class ChatHuggingFace(BaseModel):
    """Simple wrapper for Hugging Face Inference API (text-generation).

    This client builds a prompt from Message objects and calls the
    Hugging Face Inference endpoint configured by api_base and api_key.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    model: str
    api_key: Optional[str] = None
    api_base: Optional[str] = None
    temperature: Optional[float] = 0.7
    top_p: Optional[float] = None
    max_new_tokens: Optional[int] = 512
    timeout: Optional[float] = 30.0

    @property
    def provider(self) -> str:
        return "huggingface"

    def _build_prompt(self, messages: List[Message]) -> str:
        system_texts: List[str] = []
        convo_lines: List[str] = []

        for message in messages:
            if isinstance(message, SystemMessage):
                system_texts.append(message.text)
            elif isinstance(message, HumanMessage):
                # Use text property which joins content parts
                convo_lines.append(f"User: {message.text}")
            elif isinstance(message, AssistantMessage):
                convo_lines.append(f"Assistant: {message.text}")
            else:
                convo_lines.append(str(message))

        prompt = ""
        if system_texts:
            prompt += "\n".join(system_texts) + "\n\n"
        prompt += "\n".join(convo_lines)

        return prompt

    async def __call__(
        self,
        messages: List[Message],
        tools: Optional[List[Any]] = None,
        response_format: Optional[Any] = None,
        stream: bool = False,
        **kwargs: Any,
    ) -> LLMResponse:
        # Resolve api_base and api_key from env if not provided
        if not self.api_base:
            self.api_base = os.getenv("HF_API_BASE", "https://api-inference.huggingface.co")
        if not self.api_key:
            self.api_key = os.getenv("HF_TOKEN")

        if not self.api_key:
            return LLMResponse(success=False, message="Hugging Face API token not set (HF_TOKEN or config.api_key)", extra=LLMExtra(data={"model": self.model}))

        prompt = self._build_prompt(messages)
        if not prompt or not prompt.strip():
            return LLMResponse(success=False, message="Empty prompt", extra=None)

        url = f"{self.api_base.rstrip('/')}/models/{self.model}"
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

        parameters: Dict[str, Any] = {
            "max_new_tokens": int(self.max_new_tokens or 256),
            "temperature": float(self.temperature or 0.7),
        }
        if self.top_p is not None:
            parameters["top_p"] = float(self.top_p)

        # Allow overriding parameters via kwargs
        for k in ["max_new_tokens", "temperature", "top_p"]:
            if k in kwargs:
                parameters[k] = kwargs[k]

        payload = {
            "inputs": prompt,
            "parameters": parameters,
            "options": {"wait_for_model": True},
        }

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(url, headers=headers, json=payload)

            if resp.status_code >= 400:
                try:
                    data = resp.json()
                except Exception:
                    data = resp.text
                logger.error(f"HuggingFace API error {resp.status_code}: {data}")
                return LLMResponse(success=False, message=f"HuggingFace API error {resp.status_code}: {data}", extra=LLMExtra(data={"status_code": resp.status_code, "raw": data}))

            data = resp.json()

            # Common shapes: dict with 'generated_text' or list of dicts with 'generated_text'
            output_text = ""
            if isinstance(data, dict) and "generated_text" in data:
                output_text = data.get("generated_text", "")
            elif isinstance(data, list) and len(data) > 0 and isinstance(data[0], dict):
                if "generated_text" in data[0]:
                    output_text = data[0].get("generated_text", "")
                else:
                    # Some models return text under other keys
                    output_text = json.dumps(data)
            else:
                # Fallback
                try:
                    output_text = json.dumps(data)
                except Exception:
                    output_text = str(data)

            return LLMResponse(success=True, message=output_text, extra=LLMExtra(data={"raw_response": data}))

        except Exception as e:
            logger.error(f"HuggingFace request failed: {e}")
            return LLMResponse(success=False, message=str(e), extra=LLMExtra(data={"error": str(e)}))
