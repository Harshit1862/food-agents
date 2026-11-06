"""Plain language -> a Swiggy / Zomato request. One model call with a strict JSON schema: the model can
only pick one of this platform's real actions and one of the restaurants it really carries."""

import asyncio

from food_agents.common import config, llm
from food_agents.platforms import tools

ACTIONS = ["list_restaurants", "get_menu", "search_dishes", "get_quotes", "place_order", "none"]

SYSTEM = """You are the front desk of the food-delivery platform "{platform}". A person typed a request in plain language.
Turn it into exactly ONE request for the platform's system.

Actions:
- list_restaurants: which restaurants the platform carries.
- get_menu: one restaurant's menu. Needs restaurant_id.
- search_dishes: which restaurants serve a dish. query = the dish words (e.g. "biryani"); veg_only=true if they want vegetarian only.
- get_quotes: price some dishes at every restaurant (restaurant_ids = [] ) or only at the restaurants they name. Use this for
  "how much for ...", "cheapest ...", "compare ...". items = dish + quantity (1 if not said).
- place_order: ONLY when they clearly tell you to place or buy an order ("order 2 butter chicken from spice route", "place the order").
  Needs ONE restaurant_id and items.
- none: anything else (chit-chat, restaurants we don't carry). Put a short, helpful answer in `reply` saying what you can do.

Restaurants we carry (use the id):
{restaurants}

Leave fields you don't use as: restaurant_id="none", restaurant_ids=[], query="", veg_only=false, items=[], reply="".
Keep dish names in the person's words; the system matches them against the menus."""


def schema(ids: list[str]) -> dict:
    return {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ACTIONS},
            "reply": {"type": "string"},
            "restaurant_id": {"type": "string", "enum": [*ids, "none"]},
            "restaurant_ids": {"type": "array", "items": {"type": "string", "enum": ids}},
            "query": {"type": "string"},
            "veg_only": {"type": "boolean"},
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"name": {"type": "string"}, "qty": {"type": "integer"}},
                    "required": ["name", "qty"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["action", "reply", "restaurant_id", "restaurant_ids", "query", "veg_only", "items"],
        "additionalProperties": False,
    }


async def understand(platform_id: str, text: str) -> tuple[dict | None, str]:
    platform = await asyncio.to_thread(tools.get_platform, platform_id)
    listings = await asyncio.to_thread(tools.get_listings, platform_id)
    ids = [l["restaurant_id"] for l in listings]
    names = "\n".join(f"- {rid}  ({rid.replace('_', ' ').title()})" for rid in ids)

    a = await llm.chat_json(
        model=config.AGENT_MODEL,
        system=SYSTEM.format(platform=platform["name"], restaurants=names),
        user=text,
        schema_name="platform_request",
        schema=schema(ids),
    )

    action = a["action"]
    items = [{"name": i["name"], "qty": max(1, i["qty"])} for i in a["items"] if i["name"].strip()]
    rid = None if a["restaurant_id"] == "none" else a["restaurant_id"]

    if action == "list_restaurants":
        return {"action": "list_restaurants"}, ""
    if action == "get_menu":
        if rid:
            return {"action": "get_menu", "restaurant_id": rid}, ""
        return None, "Which restaurant's menu would you like?"
    if action == "search_dishes":
        if a["query"].strip():
            return {"action": "search_dishes", "query": a["query"].strip(), "veg_only": a["veg_only"]}, ""
        return None, "Which dish are you looking for?"
    if action == "get_quotes":
        if not items:
            return None, "Which dishes should I price, and how many of each?"
        request = {"action": "get_quotes", "items": items}
        if a["restaurant_ids"]:
            request["restaurant_ids"] = a["restaurant_ids"]
        return request, ""
    if action == "place_order":
        if rid and items:
            return {"action": "place_order", "restaurant_id": rid, "items": items}, ""
        return None, "To place an order I need the restaurant and the dishes, e.g. 'order 2 butter chicken from Spice Route'."
    return None, a["reply"] or "I can list restaurants, show a menu, find a dish, price items or place an order."
