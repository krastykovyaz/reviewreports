"""How a failed model call is logged.

What a model returns, and what its client raises about it, is derived from a person's CV or from a visited site's text
(a pydantic validation error quotes the input it rejected; a provider error can echo the prompt; a parse failure carries the
raw output). So the WARNING line says only THAT it failed and its shape (exception type, success flag, length of the
message); the text itself is DEBUG, which is off in production."""
import logging
from typing import Any, Optional

from src.logger import logger


def log_llm_failure(label: str, model_name: str, exc: Optional[BaseException] = None, response: Any = None) -> None:
    if exc is not None:
        logger.warning(f"| ⚠️ {label} failed for model {model_name}: {type(exc).__name__}")
        detail = str(exc)
    else:
        message = getattr(response, "message", None) or ""
        logger.warning(f"| ⚠️ {label} returned no structured result (success={getattr(response, 'success', None)}, message of {len(message)} characters)")
        detail = message
    # The logger writes every record it is handed to its log file, whatever the level: only build the record when DEBUG is on.
    if logger.isEnabledFor(logging.DEBUG):
        logger.debug(f"| {label} failure detail: {detail}")
