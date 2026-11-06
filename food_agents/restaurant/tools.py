from food_agents.common.db import get_db
from food_agents.common.messages import describe_issues


def _clean(doc: dict) -> dict:
    """Rename Mongo's _id to id, so outputs look like normal JSON."""
    doc = dict(doc)
    doc["id"] = doc.pop("_id")
    return doc


def get_restaurant(restaurant_id: str) -> dict | None:
    doc = get_db().restaurants.find_one({"_id": restaurant_id})
    return _clean(doc) if doc else None


def get_info(restaurant_id: str) -> dict | None:
    """The restaurant's public profile: what a platform shows in a restaurant list."""
    r = get_restaurant(restaurant_id)
    if r is None:
        return None
    return {
        "restaurant_id": restaurant_id,
        "name": r["name"],
        "cuisine": r["cuisine"],
        "rating": r["rating"],
        "prep_time_min": r["prep_time_min"],
        "distance_km": r["distance_km"],
        "is_online": r["is_online"],
    }


def check_menu(restaurant_id: str, only_available: bool = False) -> list[dict]:
    query = {"restaurant_id": restaurant_id}
    if only_available:
        query["available"] = True
    return [_clean(d) for d in get_db().menu_items.find(query)]


def search_menu(restaurant_id: str, is_veg=None, max_price=None, tag=None) -> list[dict]:
    """Filter this restaurant's available items. Every filter is optional."""
    query = {"restaurant_id": restaurant_id, "available": True}
    if is_veg is not None:
        query["is_veg"] = is_veg
    if max_price is not None:
        query["base_price"] = {"$lte": max_price}  # $lte = less than or equal
    if tag:
        query["tags"] = tag  # on an array field, this means "array contains tag"
    return [_clean(d) for d in get_db().menu_items.find(query)]


def _find_matches(menu: list[dict], name: str) -> list[dict]:
    """Exact name (case-insensitive) wins; otherwise any item containing the text."""
    name = name.strip().lower()
    exact = [m for m in menu if m["name"].lower() == name]
    if exact:
        return exact
    return [m for m in menu if name in m["name"].lower()]


def _alternatives(menu: list[dict], item: dict, limit: int = 3) -> list[str]:
    """Available items sharing at least one tag with the unavailable one."""
    return [
        m["name"]
        for m in menu
        if m["available"] and m["id"] != item["id"] and set(m["tags"]) & set(item["tags"])
    ][:limit]


def quote_items(restaurant_id: str, items: list[dict]) -> dict:
    """items looks like [{"name": "Mutton Biryani", "qty": 2}, ...]"""
    restaurant = get_restaurant(restaurant_id)
    if restaurant is None:
        return {"status": "rejected", "reason": "unknown restaurant"}
    if not restaurant["is_online"]:
        return {"status": "rejected", "reason": "restaurant is offline"}

    menu = check_menu(restaurant_id)
    lines, issues = [], []

    for req in items:
        qty = int(req.get("qty", 1))
        matches = _find_matches(menu, req["name"])

        if not matches:
            issues.append({"item": req["name"], "problem": "not_found"})
        elif len(matches) > 1:
            issues.append({
                "item": req["name"],
                "problem": "ambiguous",
                "options": [m["name"] for m in matches],
            })
        else:
            item = matches[0]
            if not item["available"]:
                issues.append({
                    "item": item["name"],
                    "problem": "unavailable",
                    "alternatives": _alternatives(menu, item),
                })
            else:
                lines.append({
                    "item": item["name"],
                    "qty": qty,
                    "unit_price": item["base_price"],
                    "line_total": item["base_price"] * qty,
                })

    return {
        "status": "input_required" if issues else "ok",
        "restaurant_id": restaurant_id,
        "restaurant_name": restaurant["name"],
        "prep_time_min": restaurant["prep_time_min"],
        "distance_km": restaurant["distance_km"],
        "rating": restaurant["rating"],
        "lines": lines,
        "subtotal": sum(l["line_total"] for l in lines),
        "issues": issues,
    }


def accept_order(restaurant_id: str, order_id: str, items: list[dict]) -> dict:
    """The platform asks: will you cook this? Re-check everything right now (the menu
    may have changed since the quote), then either accept and record it, or refuse."""
    quote = quote_items(restaurant_id, items)
    if quote["status"] == "rejected":
        return {"status": "refused", "reason": quote["reason"]}
    if quote["status"] == "input_required":
        return {"status": "refused", "reason": "cannot prepare it: " + describe_issues(quote["issues"])}

    get_db().restaurant_orders.update_one(  # upsert: asking twice with the same order_id is harmless
        {"_id": order_id},
        {"$set": {"restaurant_id": restaurant_id, "lines": quote["lines"], "subtotal": quote["subtotal"], "status": "accepted"}},
        upsert=True,
    )
    return {
        "status": "accepted",
        "order_id": order_id,
        "restaurant_id": restaurant_id,
        "restaurant_name": quote["restaurant_name"],
        "prep_time_min": quote["prep_time_min"],
    }