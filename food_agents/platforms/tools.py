from food_agents.common.db import get_db


def get_platform(platform_id: str) -> dict | None:
    doc = get_db().platforms.find_one({"_id": platform_id})
    if doc is None:
        return None
    doc = dict(doc)
    doc["id"] = doc.pop("_id")
    return doc


def get_listings(platform_id: str, restaurant_ids=None) -> list[dict]:
    """The restaurants this platform carries, with its own price multiplier,
    delivery fee and discount for each. {"_id": 0} drops Mongo's ObjectId,
    which is not JSON-serialisable."""
    query = {"platform_id": platform_id}
    if restaurant_ids:
        query["restaurant_id"] = {"$in": list(restaurant_ids)}
    return list(get_db().platform_listings.find(query, {"_id": 0}))


def save_order(order: dict) -> None:
    """Store a new order. order["order_id"] becomes Mongo's _id."""
    doc = dict(order)
    doc["_id"] = doc.pop("order_id")
    get_db().orders.insert_one(doc)


def record_stage(order_id: str, stage: str, at: str) -> None:
    """Move the order to a new stage and append it to the order's history."""
    get_db().orders.update_one(
        {"_id": order_id},
        {"$set": {"status": stage}, "$push": {"history": {"stage": stage, "at": at}}},
    )


def get_order(order_id: str) -> dict | None:
    doc = get_db().orders.find_one({"_id": order_id})
    if doc is None:
        return None
    doc = dict(doc)
    doc["order_id"] = doc.pop("_id")
    return doc