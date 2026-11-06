from food_agents.client.quoting import PlatformDirectory
from food_agents.client.render import render_update
from food_agents.common.a2a_stream import stream_agent


async def place_order(directory: PlatformDirectory, option: dict, on_update=None) -> dict:
    """Place one chosen option with its platform and follow the delivery live.

    on_update (optional, async) is called as on_update(line, stage_data) at every delivery
    stage: a ready-to-print line, plus the structured stage data for a UI.
    Returns the stream_agent outcome (state "completed" means delivered).
    """
    try:
        card = await directory.get_card(option["platform_id"])
    except Exception as exc:
        return {"state": "unreachable", "data": None, "text": type(exc).__name__, "task_id": None, "artifact": None}

    payload = {
        "action": "place_order",
        "restaurant_id": option["restaurant_id"],
        "items": [{"name": l["item"], "qty": int(l["qty"])} for l in option["lines"]],
        "expected_total": option["total"],  # the platform refuses if the price moved since the customer saw it
    }

    async def relay(state: str, data: dict | None, text: str) -> None:
        if on_update is not None and data and "stage" in data:
            await on_update(render_update(data), data)

    return await stream_agent(card, payload, relay)