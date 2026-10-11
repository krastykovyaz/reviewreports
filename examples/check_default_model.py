"""One real request through ModelManager's real code path, to see which model answers.

    python examples/check_default_model.py                       # the configured DEFAULT_MODEL
    python examples/check_default_model.py openrouter/gemini-3-flash-preview

Exits 0 only if a model answered. Prints the model that was asked, the model the
provider says actually served it, and the first words of the reply. Never prints
an API key (only whether one is configured). Costs a handful of tokens.
"""

import asyncio
import os
import sys

from src.message.types import HumanMessage
from src.model import model_manager
from src.model_defaults import DEFAULT_MODEL, GEMINI_CHAIN


def _served_by(response) -> str:
    """The model id the provider reports in its raw response, when it gives one."""
    data = response.extra.data if response.extra and isinstance(response.extra.data, dict) else {}
    raw = data.get("raw_response")
    if isinstance(raw, dict):
        return str(raw.get("model") or "")
    return ""


async def main(requested: str) -> int:
    await model_manager.initialize()
    config = model_manager.models.get(requested)
    print(f"requested model : {requested}")
    print(f"configured as   : {config.provider + ':' + config.model_id if config else 'NOT REGISTERED'}")
    print(f"api key present : {bool(config and config.api_key)}")
    print(f"fallback chain  : {' -> '.join(model_manager._fallback_chain(requested))}")
    print(f"default chain   : {' -> '.join(GEMINI_CHAIN)}")

    response = await model_manager(model=requested, messages=[HumanMessage(content="Reply with the single word: ok")])

    print("---")
    print(f"success         : {response.success}")
    print(f"served by       : {_served_by(response) or '(provider did not report a model)'}")
    print(f"reply           : {(response.message or '').strip()[:120]!r}")
    return 0 if response.success else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else DEFAULT_MODEL)))
