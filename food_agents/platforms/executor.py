import asyncio
import uuid
from datetime import datetime, timezone
from math import ceil

from a2a.helpers import new_data_part
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.tasks import TaskUpdater
from a2a.types import Part, Task, TaskState, TaskStatus

from food_agents.common import config
from food_agents.common.a2a_client import call_agent
from food_agents.common.a2a_helpers import get_request_data
from food_agents.common.eta import compute_eta_min, listed_price
from food_agents.common.messages import describe_issues
from food_agents.platforms import tools
from food_agents.platforms.discovery import RestaurantDirectory
from food_agents.platforms.ordering import STAGE_TEXT, minutes_left, stage_plan
from food_agents.platforms.pricing import price_quote

HELP = (
    "Send a structured (data) request: "
    '{"action": "list_restaurants"}, {"action": "get_menu", "restaurant_id": "spice_route"}, '
    '{"action": "search_dishes", "query": "biryani"}, '
    '{"action": "get_quotes", "items": [{"name": "Raita", "qty": 2}]} '
    "(optional: restaurant_ids), or "
    '{"action": "place_order", "restaurant_id": "spice_route", "items": [{"name": "Butter Chicken", "qty": 2}], '
    '"expected_total": 855} (expected_total is optional: the order is refused if the price changed). '
    "Free-text requests are not supported yet."
)


class PlatformExecutor(AgentExecutor):
    def __init__(self, platform_id: str):
        self.platform_id = platform_id
        self.directory = RestaurantDirectory()

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        updater = TaskUpdater(event_queue, context.task_id, context.context_id)

        if context.current_task is None:
            await event_queue.enqueue_event(
                Task(
                    id=context.task_id,
                    context_id=context.context_id,
                    status=TaskStatus(state=TaskState.TASK_STATE_SUBMITTED),
                    history=[context.message],
                )
            )

        request = get_request_data(context.message)
        if request is None:
            await updater.reject(updater.new_agent_message([Part(text=HELP)]))
            return

        try:
            await updater.start_work()
            if request.get("action") == "list_restaurants":
                await self._list_restaurants(updater)
            elif request.get("action") == "get_menu":
                await self._get_menu(request, updater)
            elif request.get("action") == "search_dishes":
                await self._search_dishes(request, updater)
            elif request.get("action") == "get_quotes":
                await self._get_quotes(request, updater)
            elif request.get("action") == "place_order":
                await self._place_order(request, context, updater)
            else:
                await updater.reject(
                    updater.new_agent_message([Part(text=f"Unknown action '{request.get('action')}'. " + HELP)])
                )
        except Exception as exc:
            await updater.failed(updater.new_agent_message([Part(text=f"Platform error: {exc}")]))

    # ---- browsing: restaurant list, menu, dish search -------------------------------------

    async def _ask_restaurant(self, rid: str, payload: dict) -> dict:
        """One A2A call to a restaurant agent. Never raises: returns the call_agent outcome."""
        try:
            card = await self.directory.get_card(rid)
        except Exception as exc:
            return {"state": "unreachable", "data": None, "text": f"restaurant agent unreachable ({type(exc).__name__})"}
        return await call_agent(card, payload)

    async def _list_restaurants(self, updater: TaskUpdater) -> None:
        """Every restaurant this platform carries, with the restaurant's own facts (asked over A2A)
        and this platform's terms (fee, discount) and delivery-time estimate."""
        platform = await asyncio.to_thread(tools.get_platform, self.platform_id)
        listings = await asyncio.to_thread(tools.get_listings, self.platform_id)
        replies = await asyncio.gather(*[self._ask_restaurant(l["restaurant_id"], {"action": "get_info"}) for l in listings])

        restaurants, unavailable = [], []
        for listing, reply in zip(listings, replies):
            info = reply["data"]
            if reply["state"] != "completed" or not info:
                unavailable.append({"restaurant_id": listing["restaurant_id"], "reason": reply["text"] or reply["state"]})
                continue
            restaurants.append(
                {
                    "restaurant_id": listing["restaurant_id"],
                    "name": info["name"],
                    "cuisine": info["cuisine"],
                    "rating": info["rating"],
                    "is_online": info["is_online"],
                    "delivery_fee": listing["delivery_fee"],
                    "discount_pct": listing["discount_pct"],
                    "eta_min": compute_eta_min(
                        info["prep_time_min"], platform["rider_assign_min"], info["distance_km"], platform["min_per_km"]
                    ),
                }
            )
        result = {"platform_id": self.platform_id, "platform_name": platform["name"], "restaurants": restaurants, "unavailable": unavailable}
        await updater.add_artifact([new_data_part(result)], name="restaurants")
        await updater.complete()

    def _platform_items(self, menu: list[dict], listing: dict) -> list[dict]:
        """A restaurant's menu as this platform sells it: base price x the platform's multiplier."""
        return [
            {
                "id": m["id"],
                "name": m["name"],
                "price": listed_price(m["base_price"], listing["price_multiplier"]),
                "is_veg": m["is_veg"],
                "tags": list(m.get("tags", [])),
                "available": m["available"],
            }
            for m in menu
        ]

    async def _get_menu(self, request: dict, updater: TaskUpdater) -> None:
        rid = request.get("restaurant_id")
        if not rid:
            await updater.reject(updater.new_agent_message([Part(text="get_menu needs 'restaurant_id'.")]))
            return
        platform = await asyncio.to_thread(tools.get_platform, self.platform_id)
        listings = await asyncio.to_thread(tools.get_listings, self.platform_id, [rid])
        if not listings:
            await updater.reject(updater.new_agent_message([Part(text=f"{platform['name']} does not carry '{rid}'.")]))
            return
        listing = listings[0]

        info, menu = await asyncio.gather(
            self._ask_restaurant(rid, {"action": "get_info"}),
            self._ask_restaurant(rid, {"action": "check_menu"}),
        )
        if menu["state"] != "completed" or info["state"] != "completed" or not menu["data"] or not info["data"]:
            await updater.failed(updater.new_agent_message([Part(text=menu["text"] or info["text"] or "restaurant did not answer")]))
            return
        i = info["data"]
        result = {
            "platform_id": self.platform_id,
            "restaurant_id": rid,
            "restaurant_name": i["name"],
            "cuisine": i["cuisine"],
            "rating": i["rating"],
            "is_online": i["is_online"],
            "delivery_fee": listing["delivery_fee"],
            "discount_pct": listing["discount_pct"],
            "eta_min": compute_eta_min(i["prep_time_min"], platform["rider_assign_min"], i["distance_km"], platform["min_per_km"]),
            "items": self._platform_items(menu["data"]["menu"], listing),
        }
        await updater.add_artifact([new_data_part(result)], name="menu")
        await updater.complete()

    async def _search_dishes(self, request: dict, updater: TaskUpdater) -> None:
        """Which restaurants here serve a dish? Asks every restaurant for its menu and matches the words."""
        words = str(request.get("query") or "").lower().split()
        if not words:
            await updater.reject(updater.new_agent_message([Part(text="search_dishes needs a non-empty 'query'.")]))
            return
        platform = await asyncio.to_thread(tools.get_platform, self.platform_id)
        listings = await asyncio.to_thread(tools.get_listings, self.platform_id)
        replies = await asyncio.gather(*[self._ask_restaurant(l["restaurant_id"], {"action": "check_menu"}) for l in listings])
        infos = await asyncio.gather(*[self._ask_restaurant(l["restaurant_id"], {"action": "get_info"}) for l in listings])

        matches = []
        for listing, menu, info in zip(listings, replies, infos):
            if menu["state"] != "completed" or info["state"] != "completed" or not menu["data"] or not info["data"]:
                continue
            found = []
            for item in self._platform_items(menu["data"]["menu"], listing):
                if request.get("veg_only") and not item["is_veg"]:
                    continue
                haystack = (item["name"] + " " + " ".join(item["tags"])).lower()
                if all(w in haystack for w in words):
                    found.append(item)
            if found:
                matches.append(
                    {
                        "restaurant_id": listing["restaurant_id"],
                        "restaurant_name": info["data"]["name"],
                        "rating": info["data"]["rating"],
                        "delivery_fee": listing["delivery_fee"],
                        "discount_pct": listing["discount_pct"],
                        "items": found,
                    }
                )
        result = {"platform_id": self.platform_id, "platform_name": platform["name"], "query": request.get("query"), "matches": matches}
        await updater.add_artifact([new_data_part(result)], name="dishes")
        await updater.complete()

    async def _get_quotes(self, request: dict, updater: TaskUpdater) -> None:
        items = request.get("items")
        if not items:
            await updater.reject(updater.new_agent_message([Part(text="get_quotes needs a non-empty 'items' list.")]))
            return

        platform = await asyncio.to_thread(tools.get_platform, self.platform_id)
        listings = await asyncio.to_thread(tools.get_listings, self.platform_id, request.get("restaurant_ids"))
        if not listings:
            await updater.reject(
                updater.new_agent_message([Part(text=f"{platform['name']} carries none of those restaurants.")])
            )
            return

        # Ask every restaurant at the same time; total wait = the slowest one, not the sum.
        outcomes = await asyncio.gather(*[self._quote_one(platform, l, items) for l in listings])

        result = {
            "platform_id": self.platform_id,
            "options": [o["option"] for o in outcomes if o["kind"] == "quoted"],
            "needs_input": [o["detail"] for o in outcomes if o["kind"] == "needs_input"],
            "skipped": [o["detail"] for o in outcomes if o["kind"] == "skipped"],
        }

        if result["options"]:
            await updater.add_artifact([new_data_part(result)], name="quotes")
            await updater.complete()
        elif result["needs_input"]:
            text = f"{platform['name']} needs a choice: " + " | ".join(
                f"{n['restaurant_name']}: {describe_issues(n['issues'])}" for n in result["needs_input"]
            )
            await updater.requires_input(updater.new_agent_message([Part(text=text), new_data_part(result)]))
        else:
            text = f"No restaurant on {platform['name']} can serve this order."
            await updater.reject(updater.new_agent_message([Part(text=text), new_data_part(result)]))

    async def _place_order(self, request: dict, context: RequestContext, updater: TaskUpdater) -> None:
        """Quote again, ask the restaurant to accept, then stream the delivery stages."""
        rid, items = request.get("restaurant_id"), request.get("items")
        if not rid or not items:
            await updater.reject(updater.new_agent_message([Part(text="place_order needs 'restaurant_id' and 'items'.")]))
            return

        platform = await asyncio.to_thread(tools.get_platform, self.platform_id)
        listings = await asyncio.to_thread(tools.get_listings, self.platform_id, [rid])
        if not listings:
            await updater.reject(updater.new_agent_message([Part(text=f"{platform['name']} does not carry '{rid}'.")]))
            return

        try:
            card = await self.directory.get_card(rid)
        except Exception as exc:
            await updater.failed(
                updater.new_agent_message([Part(text=f"restaurant agent unreachable ({type(exc).__name__})")])
            )
            return

        # 1. Price it again: menus and availability may have changed since the customer saw the quote.
        quote = await call_agent(card, {"action": "quote_items", "items": items})
        if quote["state"] != "completed" or not quote["data"]:
            await updater.reject(
                updater.new_agent_message([Part(text=quote["text"] or f"restaurant answered '{quote['state']}'")])
            )
            return
        option = price_quote(platform, listings[0], quote["data"])

        expected = request.get("expected_total")
        if expected is not None and abs(option["total"] - float(expected)) > 0.5:
            text = f"The price changed from {round(float(expected))} to {round(option['total'])}. Nothing was ordered."
            data = {"reason": "price_changed", "old_total": float(expected), "new_total": option["total"]}
            await updater.reject(updater.new_agent_message([Part(text=text), new_data_part(data)]))
            return

        # 2. Ask the restaurant to accept. It can refuse.
        order_id = f"ord_{uuid.uuid4().hex[:8]}"
        accepted = await call_agent(
            card,
            {
                "action": "accept_order",
                "order_id": order_id,
                "items": [{"name": l["item"], "qty": l["qty"]} for l in option["lines"]],
            },
        )
        if accepted["state"] != "completed":
            reason = accepted["text"] or accepted["state"]
            await updater.reject(
                updater.new_agent_message([Part(text=f"{option['restaurant_name']} could not accept your order: {reason}")])
            )
            return

        # 3. Save it, then walk it through the stages, telling the customer at each one.
        name = option["restaurant_name"]
        await asyncio.to_thread(
            tools.save_order,
            {
                "order_id": order_id,
                "task_id": context.task_id,
                "platform_id": self.platform_id,
                "restaurant_id": rid,
                "restaurant_name": name,
                "lines": option["lines"],
                "items_subtotal": option["items_subtotal"],
                "discount": option["discount"],
                "delivery_fee": option["delivery_fee"],
                "total": option["total"],
                "eta_min": option["eta_min"],
                "status": "confirmed",
                "history": [],
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
        )

        plan = stage_plan(option["eta_breakdown"])
        last = len(plan) - 1
        for index, (stage, minutes) in enumerate(plan):
            await asyncio.to_thread(tools.record_stage, order_id, stage, datetime.now(timezone.utc).isoformat())
            text = STAGE_TEXT[stage].format(restaurant=name)

            def stage_data(left: int, tick: bool = False, stage=stage, text=text) -> dict:
                return {
                    "order_id": order_id,
                    "task_id": context.task_id,
                    "platform_id": self.platform_id,
                    "restaurant_name": name,
                    "stage": stage,
                    "text": text,
                    "minutes_left": left,
                    "total": option["total"],
                    "tick": tick,
                }

            message = updater.new_agent_message([Part(text=text), new_data_part(stage_data(minutes_left(plan, index)))])
            if index == last:
                receipt = await asyncio.to_thread(tools.get_order, order_id)
                await updater.add_artifact([new_data_part(receipt)], name="receipt")
                await updater.complete(message)
                return
            await updater.update_status(TaskState.TASK_STATE_WORKING, message)

            # Wait out this stage, but speak up every second: a silent connection gets closed by
            # clients (httpx gives up after 5 s), and the customer sees the minutes count down.
            speed = config.SIM_SECONDS_PER_MIN
            loop = asyncio.get_running_loop()
            end = loop.time() + minutes * speed
            later = sum(m for _, m in plan[index + 1 :])
            while (remaining := end - loop.time()) > 0:
                await asyncio.sleep(min(1.0, remaining))
                remaining = end - loop.time()
                if remaining > 0.05:
                    left = ceil(round(remaining / speed + later, 6))
                    tick = updater.new_agent_message([Part(text=text), new_data_part(stage_data(left, tick=True))])
                    await updater.update_status(TaskState.TASK_STATE_WORKING, tick)

    async def _quote_one(self, platform: dict, listing: dict, items: list) -> dict:
        """Ask one restaurant agent for a quote and sort the answer into
        quoted / needs_input / skipped."""
        rid = listing["restaurant_id"]

        def skipped(reason: str) -> dict:
            return {"kind": "skipped", "detail": {"restaurant_id": rid, "reason": reason}}

        try:
            card = await self.directory.get_card(rid)
        except Exception as exc:
            return skipped(f"restaurant agent unreachable ({type(exc).__name__})")

        reply = await call_agent(card, {"action": "quote_items", "items": items})
        state, data = reply["state"], reply["data"]

        if state == "completed" and data:
            return {"kind": "quoted", "option": price_quote(platform, listing, data)}

        if state == "input_required" and data:
            missing = [i["item"] for i in data["issues"] if i["problem"] == "not_found"]
            if missing:  # can't fulfil the whole order here, so it's not an option
                return skipped("does not have: " + ", ".join(missing))
            return {
                "kind": "needs_input",
                "detail": {
                    "restaurant_id": rid,
                    "restaurant_name": data["restaurant_name"],
                    "issues": data["issues"],
                },
            }

        return skipped(reply["text"] or f"restaurant answered '{state}'")

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        await TaskUpdater(event_queue, context.task_id, context.context_id).cancel()