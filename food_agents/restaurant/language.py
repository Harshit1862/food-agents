"""Plain language -> a restaurant request. One model call with a strict JSON schema, so the model can
only answer with one of the actions this restaurant really has."""

import asyncio

from food_agents.common import config, llm
from food_agents.restaurant import tools

SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["get_info", "check_menu", "search_menu", "quote_items", "none"]},
        "reply": {"type": "string"},
        "only_available": {"type": "boolean"},
        "is_veg": {"type": ["boolean", "null"]},
        "max_price": {"type": ["number", "null"]},
        "tag": {"type": ["string", "null"]},
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
    "required": ["action", "reply", "only_available", "is_veg", "max_price", "tag", "items"],
    "additionalProperties": False,
}

SYSTEM = """You are the front desk of the restaurant "{name}". A person typed a request in plain language.
Turn it into exactly ONE request for the restaurant's system.

Actions:
- get_info: the restaurant's profile (cuisine, rating, prep time, distance, online or not).
- check_menu: the whole menu. Set only_available=true if they only want what can be ordered right now.
- search_menu: filter the menu. is_veg (true = veg only, false = non-veg only), max_price (rupees), tag (one word from the tags below).
- quote_items: price specific dishes, e.g. "how much for 2 raita". items = dish + quantity (quantity is 1 if not said).
- none: anything else (chit-chat, other restaurants, accepting an order, which needs an order id). Put a short, helpful answer in `reply`
  that says what you can do here.

Use a dish's exact menu name when it is clear which dish they mean, otherwise keep their words.
Leave fields you don't use as: only_available=false, is_veg=null, max_price=null, tag=null, items=[], reply="".

The menu:
{menu}
Tags: {tags}"""


async def understand(restaurant_id: str, text: str) -> tuple[dict | None, str]:
    restaurant = await asyncio.to_thread(tools.get_restaurant, restaurant_id)
    menu = await asyncio.to_thread(tools.check_menu, restaurant_id, False)
    lines = "\n".join(f"- {m['name']} (₹{m['base_price']}, {'veg' if m['is_veg'] else 'non-veg'})" for m in menu)
    tags = sorted({t for m in menu for t in m.get("tags", [])})

    a = await llm.chat_json(
        model=config.AGENT_MODEL,
        system=SYSTEM.format(name=restaurant["name"], menu=lines, tags=", ".join(tags) or "none"),
        user=text,
        schema_name="restaurant_request",
        schema=SCHEMA,
    )

    action = a["action"]
    if action == "get_info":
        return {"action": "get_info"}, ""
    if action == "check_menu":
        return {"action": "check_menu", "only_available": a["only_available"]}, ""
    if action == "search_menu":
        request = {"action": "search_menu"}
        for key in ("is_veg", "max_price", "tag"):
            if a[key] is not None:
                request[key] = a[key]
        return request, ""
    if action == "quote_items":
        items = [{"name": i["name"], "qty": max(1, i["qty"])} for i in a["items"] if i["name"].strip()]
        if items:
            return {"action": "quote_items", "items": items}, ""
        return None, "Which dishes should I price, and how many of each?"
    return None, a["reply"] or "I can show the menu, search it (veg, price, tags), or price some dishes."
