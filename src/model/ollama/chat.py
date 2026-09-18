import httpx
import json
import asyncio
from typing import Any, Dict, List, Optional
from src.model.openai.chat import ChatOpenAI

class ChatOllama(ChatOpenAI):
    """Ollama — OpenAI-compatible local inference via raw httpx."""
    timeout: float = 120.0

    async def _format_response(self, response, tools=None, response_format=None):
        print(f"[DEBUG] _format_response called, response_format={response_format}")
        if response_format and isinstance(response_format, type):
            import json as _json
            import re
            from src.model.types import LLMResponse, LLMExtra

            content = response.choices[0].message.content or ""

            # Убираем thinking блоки
            content = re.sub(r'<think>.*?</think>', '', content, flags=re.DOTALL).strip()

            # Убираем markdown
            json_match = re.search(r'```(?:json)?\s*([\s\S]*?)```', content)
            if json_match:
                content = json_match.group(1).strip()

            # Ищем JSON объект внутри текста
            json_obj_match = re.search(r'\{[\s\S]*\}', content)
            if json_obj_match:
                content = json_obj_match.group(0)

            try:
                data = _json.loads(content)
                parsed_model = response_format.model_validate(data)
                return LLMResponse(
                    success=True,
                    message=content,
                    extra=LLMExtra(
                        parsed_model=parsed_model,
                        data={"usage": None, "finish_reason": "stop", "reasoning": None}
                    )
                )
            except Exception as e:
                print(f"[DEBUG] JSON parse error: {e}, content: {content[:300]}")
                return LLMResponse(success=False, message=str(e))

        return await super()._format_response(response, tools, response_format)

    async def _call_model(self, messages, **params):
        params.pop("frequency_penalty", None)
        params.pop("max_completion_tokens", None)
        params.pop("max_tokens", None)

        # Извлекаем response_format и конвертируем в JSON mode
        response_format = params.pop("response_format", None)
        format_param = None
        if response_format:
            # Ollama поддерживает format="json" или format=<json_schema>
            print(f"[DEBUG] Last message role: {messages[-1].get('role')}, content: {str(messages[-1].get('content'))[:200]}")
            if isinstance(response_format, dict):
                schema = response_format.get("json_schema", {}).get("schema")
                if schema:
                    format_param = schema
                else:
                    format_param = "json"
            else:
                format_param = "json"

        url = f"{self.base_url}/api/chat"
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "options": {"num_ctx": 16384, "num_predict": 2048},
        }
        if format_param:
            payload["format"] = format_param
        # Конвертируем content из array в string для Ollama
        def normalize_messages(msgs):
            result = []
            for msg in msgs:
                content = msg.get("content", "")
                if isinstance(content, list):
                    text_parts = []
                    for part in content:
                        if isinstance(part, dict):
                            # Поддержка {'type': 'text', 'text': '...'} и {'text': '...'}
                            if part.get("type") == "text" or "type" not in part:
                                text_parts.append(part.get("text", ""))
                        elif isinstance(part, str):
                            text_parts.append(part)
                    content = "\n".join(text_parts)
                result.append({**msg, "content": content})
            return result

        payload["messages"] = normalize_messages(messages)
        print(f"[DEBUG] Normalized last msg: {str(payload['messages'][-1])[:300]}")

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            print(f"[DEBUG] Sending to {url}")
            print(f"[DEBUG] Payload: {json.dumps(payload)[:500]}")
            r = await client.post(url, json=payload)
            print(f"[DEBUG] Status: {r.status_code}")
            print(f"[DEBUG] Response: {r.text[:300]}")
            r.raise_for_status()
            data = r.json()
        content = data["message"].get("content") or data["message"].get("thinking", "")
        

        from openai.types.chat.chat_completion import ChatCompletion
        return ChatCompletion(**{
            "id": "chatcmpl-ollama",
            "object": "chat.completion",
            "created": 0,
            "model": self.model,
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }],
            "usage": {
                "prompt_tokens": data.get("prompt_eval_count", 0),
                "completion_tokens": data.get("eval_count", 0),
                "total_tokens": data.get("prompt_eval_count", 0) + data.get("eval_count", 0),
            }
        })