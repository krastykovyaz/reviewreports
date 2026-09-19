import json
from typing import Any


async def eval_json(page, js: str) -> Any:
    """Run `page.evaluate(js)` and parse its result as JSON.

    `page.evaluate` JSON-stringifies objects/arrays but returns primitive
    strings as-is, so this handles both shapes.
    """
    result = await page.evaluate(js)
    if isinstance(result, (dict, list)) or result is None:
        return result
    try:
        return json.loads(result)
    except (TypeError, ValueError):
        return result
