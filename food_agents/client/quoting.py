import asyncio
import json

import httpx

from a2a.client import A2ACardResolver

from food_agents.common import config
from food_agents.common.a2a_client import call_agent


class PlatformDirectory:
    """Finds Swiggy and Zomato through their Agent Cards. Only successes are cached,
    so a platform that was down is retried on the next request."""

    def __init__(self) -> None:
        self._cards = {}

    async def get_card(self, platform_id: str):
        card = self._cards.get(platform_id)
        if card is None:
            async with httpx.AsyncClient(timeout=5) as http:
                resolver = A2ACardResolver(http, config.PLATFORM_URLS[platform_id])
                card = await resolver.get_agent_card()
            self._cards[platform_id] = card
        return card


async def ask_platform(directory: PlatformDirectory, platform_id: str, payload: dict, timeout: float = 30) -> dict:
    """One A2A request to Swiggy or Zomato. Never raises: a platform that is down comes back
    as {"state": "unreachable", ...}."""
    try:
        card = await directory.get_card(platform_id)
    except Exception as exc:
        return {"state": "unreachable", "data": None, "text": type(exc).__name__}
    return await call_agent(card, payload, timeout=timeout)


async def _ask_platform(directory: PlatformDirectory, platform_id: str, items: list[dict]):
    return platform_id, await ask_platform(directory, platform_id, {"action": "get_quotes", "items": items})


async def gather_quotes(items: list[dict], directory: PlatformDirectory) -> dict:
    """Ask Swiggy and Zomato at the same time and merge what they say.

    Returns {"options": [...], "needs_input": [...], "down": [platform ids that didn't answer]}
    """
    replies = await asyncio.gather(*[_ask_platform(directory, pid, items) for pid in config.PLATFORM_URLS])

    options, needs_input, down, seen = [], [], [], set()
    for platform_id, reply in replies:
        if reply["state"] == "unreachable":
            down.append(platform_id)
            continue
        data = reply["data"] or {}
        options.extend(data.get("options", []))
        for entry in data.get("needs_input", []):
            # Both platforms usually report the same restaurant problem; show it once.
            key = (entry["restaurant_id"], json.dumps(entry["issues"], sort_keys=True))
            if key not in seen:
                seen.add(key)
                needs_input.append(entry)
    return {"options": options, "needs_input": needs_input, "down": down}