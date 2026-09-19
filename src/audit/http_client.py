from dataclasses import dataclass
from typing import Dict, Optional

import httpx


@dataclass
class FetchResult:
    status_code: int
    headers: Dict[str, str]
    text: str
    url: str


async def fetch(url: str, timeout: float = 15.0) -> FetchResult:
    async with httpx.AsyncClient(follow_redirects=True, timeout=timeout) as client:
        resp = await client.get(url)
        return FetchResult(status_code=resp.status_code, headers=dict(resp.headers), text=resp.text, url=str(resp.url))


async def try_fetch(url: str, timeout: float = 10.0) -> Optional[FetchResult]:
    try:
        return await fetch(url, timeout=timeout)
    except httpx.HTTPError:
        return None
