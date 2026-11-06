"""one The client agent: a tool-calling assistant for customer.

How a message is handled:
  1. The language model reads the conversation plus a small "state" block (cart, what the customer
     is looking at, what is waiting for their decision) and picks tools to call.
  2. Our code runs each tool. A tool asks Swiggy / Zomato over A2A (they ask the restaurants), and
     the REAL answer goes back to the model. Prices, fees and delivery times are never invented.
  3. Every tool also leaves a "block" in self.blocks: the structured result (restaurant list, menu,
     cart, comparison, receipt...). The gateway turns blocks into A2UI; the CLI prints their text.
  4. Buttons in the UI skip the model: act() runs the matching tool directly.

Money rule, unchanged: the model chooses tools and writes sentences, code does all the numbers.
"""

import inspect
import json
import re
import uuid

from food_agents.client.cart import Cart
from food_agents.client.ordering import place_order
from food_agents.client.quoting import PlatformDirectory, ask_platform, gather_quotes
from food_agents.client.render import (
    fallback_summary,
    render_bill,
    render_cart,
    render_dishes,
    render_ineligible,
    render_menu,
    render_options,
    render_question,
    render_receipt,
    render_restaurants,
    render_status,
)
from food_agents.client.satisfaction import evaluate, label, money, recommendation_facts
from food_agents.client.tools import SYSTEM_PROMPT, TOOL_SPECS
from food_agents.common import config, llm
from food_agents.common.a2a_stream import fetch_task

MAX_STEPS = 6  # model <-> tool round trips for one message
HISTORY_MESSAGES = 14  # how much of the chat the model remembers
MAX_SURFACES = 8  # menus kept in sync with the cart

RECOMMEND_PROMPT = (
    "You are a friendly food-ordering assistant. Write a 2-3 sentence recommendation for the customer "
    "from the JSON facts. Start with 'I recommend'. Use ONLY the names and numbers in the facts. "
    "Name the platform and restaurant, give the total in rupees (₹) and the delivery time in minutes, "
    "and give one concrete reason it is the best match (cheaper or faster than the next option, or within "
    "their limits). Nothing has been ordered yet, so never say the order is placed or confirmed. "
    "Plain text only: no markdown, no bullet points."
)

ACTIONS = {"show_menu", "cart_add", "replace_cart", "show_cart", "clear_cart", "checkout", "place_order"}


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(text).lower()).strip()


def _int(value, default: int = 1) -> int:
    try:
        return max(1, int(float(value)))
    except (TypeError, ValueError):
        return default


def _positive(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _term(text: str, shown: dict | None = None) -> dict:
    """A tool result that ENDS the turn: `text` is the reply, no further model call is needed."""
    return {"terminal": True, "text": text, "for_model": shown or {"shown_to_customer": True}}


def _error(message: str) -> dict:
    return {"text": message, "for_model": {"error": message}}


class ClientAgent:
    """Chat state for one customer: conversation, cart, what they are looking at, the order placed."""

    def __init__(self) -> None:
        self.directory = PlatformDirectory()
        self.placed: dict | None = None  # the last order placed; survives reset so it can still be tracked
        self.blocks: list[dict] = []  # structured results of the last reply, for the web UI
        self.sync: list[dict] = []  # cart quantities to push to menus already on screen
        self._on_update = None
        self.reset()

    def reset(self) -> None:
        self.history: list[dict] = []
        self.surfaces: list[dict] = []  # menus on screen: {"sid", "rows": {key: (platform, restaurant, dish)}}
        self.cart = Cart()
        self.evaluation: dict | None = None  # options of the last recommendation, ready to be ordered
        self.last_deal: dict | None = None  # the last find_best_deal request, so limits can be changed
        self.focus: dict = {}  # what the customer is looking at: platform_id, restaurant_id, restaurant_name
        self.catalog: dict[str, list[dict]] = {}  # platform_id -> its restaurants
        self.awaiting: str | None = None  # "cart" | "recommendation" | None: what a "yes" would order
        self._show_cart = False
        self._cart_changed = False

    # ------------------------------------------------------------------ entry points

    def is_action(self, name: str) -> bool:
        return name in ACTIONS

    async def handle(self, text: str, on_update=None) -> str:
        """One customer message. on_update (optional, async) gets live delivery updates.
        Returns the reply text; structured results are left in self.blocks."""
        self._begin(on_update)
        self.history.append({"role": "user", "content": text})
        reply = await self._converse()
        self._remember(reply)
        return reply

    async def act(self, name: str, context: dict, on_update=None) -> str:
        """The customer tapped a button in the UI. No language model involved."""
        self._begin(on_update)
        self.history.append({"role": "user", "content": f"[The customer tapped a button: {name} {json.dumps(context, ensure_ascii=False)}]"})
        context = context or {}
        pid, rid, item = context.get("platform_id", ""), context.get("restaurant_id", ""), context.get("item", "")

        if name == "show_menu":
            result = await self.tool_show_menu(restaurant=rid, platform=pid)
        elif name == "cart_add":
            delta = int(context.get("delta") or 1)
            if delta > 0:
                result = await self.tool_add_to_cart(item=item, qty=delta, restaurant=rid, platform=pid)
            else:
                result = await self.tool_remove_from_cart(item=item, qty=-delta)
        elif name == "replace_cart":
            self.cart.clear()
            dishes = context.get("items") or [{"name": item, "qty": context.get("qty")}]
            result = await self.tool_add_to_cart(items=dishes, restaurant=rid, platform=pid)
        elif name == "show_cart":
            result = await self.tool_show_cart()
        elif name == "clear_cart":
            result = await self.tool_clear_cart()
        elif name == "checkout":
            result = await self._place_cart()
        elif name == "place_order":
            result = await self._place_from_deal(pid, rid)
        else:
            result = _error(f"unknown action '{name}'")

        reply = await self._finish(result.get("text", ""))
        self._remember(reply)
        return reply

    async def place_chosen(self, platform_id: str, restaurant_id: str, on_update=None) -> str:
        """Kept for callers that place a recommended option directly."""
        return await self.act("place_order", {"platform_id": platform_id, "restaurant_id": restaurant_id}, on_update)

    def _begin(self, on_update) -> None:
        self._on_update = on_update
        self.blocks = []
        self.sync = []
        self._show_cart = False
        self._cart_changed = False

    def _remember(self, reply: str) -> None:
        self.history.append({"role": "assistant", "content": reply})
        self.history = self.history[-HISTORY_MESSAGES:]

    # ------------------------------------------------------------------ the model <-> tools loop

    def _system(self) -> str:
        focus = "nothing yet"
        if self.focus.get("platform_id"):
            focus = f"platform {self.focus['platform_id'].title()}"
            if self.focus.get("restaurant_name"):
                focus += f", restaurant {self.focus['restaurant_name']}"
        waiting = {"cart": "the cart (the customer has seen its total)", "recommendation": "a recommended option"}.get(
            self.awaiting, "nothing"
        )
        deal = "none"
        if self.last_deal:
            d = self.last_deal
            deal = json.dumps(
                {"items": d["items"], "max_budget": d["max_budget"], "max_eta_min": d["max_eta_min"], "priority": d["priority"]},
                ensure_ascii=False,
            )
        placed = f"yes, order {self.placed['order_id']}" if self.placed else "no"
        return (
            f"{SYSTEM_PROMPT}\n\nCurrent state:\n"
            f"- Platforms: swiggy, zomato\n"
            f"- Looking at: {focus}\n"
            f"- Cart: {self.cart.describe()}\n"
            f"- Waiting for the customer's decision: {waiting}\n"
            f"- Last deal request: {deal}\n"
            f"- Order already placed: {placed}"
        )

    async def _converse(self) -> str:
        messages = [{"role": "system", "content": self._system()}] + self.history
        for _ in range(MAX_STEPS):
            try:
                message = await llm.chat_tools(model=config.CLIENT_MODEL, messages=messages, tools=TOOL_SPECS)
            except Exception as exc:
                return await self._finish(f"Sorry, I couldn't process that ({type(exc).__name__}: {exc})")

            calls = message.tool_calls or []
            if not calls:
                return await self._finish(message.content or "")

            messages.append(
                {
                    "role": "assistant",
                    "content": message.content or "",
                    "tool_calls": [
                        {"id": c.id, "type": "function", "function": {"name": c.function.name, "arguments": c.function.arguments}}
                        for c in calls
                    ],
                }
            )
            last_text, all_terminal = "", True
            for call in calls:
                result = await self._run_tool(call.function.name, call.function.arguments)
                messages.append({"role": "tool", "tool_call_id": call.id, "content": json.dumps(result["for_model"], ensure_ascii=False)})
                if result.get("terminal"):
                    last_text = result["text"]
                else:
                    all_terminal = False
            if all_terminal:
                return await self._finish(last_text)

        return await self._finish("Sorry, I got stuck on that one. Could you say it a bit differently?")

    async def _run_tool(self, name: str, raw_arguments: str) -> dict:
        method = getattr(self, f"tool_{name}", None)
        if method is None:
            return _error(f"unknown tool '{name}'")
        try:
            arguments = json.loads(raw_arguments or "{}")
            if not isinstance(arguments, dict):
                arguments = {}
        except json.JSONDecodeError:
            return _error("the tool arguments were not valid JSON")
        allowed = set(inspect.signature(method).parameters)
        try:
            return await method(**{k: v for k, v in arguments.items() if k in allowed})
        except Exception as exc:  # a bug or a bad argument must not crash the chat
            return _error(f"{name} failed ({type(exc).__name__}: {exc})")

    async def _finish(self, text: str) -> str:
        """Last step of every reply: price the cart if it changed, sync menus, make sure there is text."""
        text = (text or "").strip()
        if self._show_cart and not self.cart.is_empty():
            text = f"{text} {await self._cart_block()}".strip()
        if self.awaiting == "cart" and self.cart.is_empty():
            self.awaiting = None
        if self._cart_changed:
            self.sync = [
                {"sid": s["sid"], "qty": {key: self.cart.qty(*place) for key, place in s["rows"].items()}} for s in self.surfaces
            ]
        if not text:
            text = "Here you go." if self.blocks else "Sorry, I didn't catch that. What would you like to order?"
        return text

    # ------------------------------------------------------------------ helpers shared by tools

    async def _restaurants(self, platform_id: str, refresh: bool = False) -> tuple[list[dict] | None, str]:
        if refresh or platform_id not in self.catalog:
            reply = await ask_platform(self.directory, platform_id, {"action": "list_restaurants"})
            if reply["state"] != "completed" or not reply["data"]:
                return None, f"{platform_id.title()} isn't responding right now."
            self.catalog[platform_id] = reply["data"]["restaurants"]
        return self.catalog[platform_id], ""

    def _platform_of(self, platform: str = "") -> str | None:
        pid = str(platform or "").lower() or self.focus.get("platform_id") or self.cart.platform_id
        return pid if pid in config.PLATFORM_URLS else None

    async def _resolve(self, platform: str, restaurant: str) -> tuple[tuple[str, dict] | None, str]:
        """Find (platform_id, restaurant) from what the model passed (a name or an id)."""
        pid = self._platform_of(platform)
        if pid is None:
            return None, "Which platform: Swiggy or Zomato? Ask the customer with ask_customer."
        if not restaurant:
            restaurant = self.focus.get("restaurant_id") if self.focus.get("platform_id") == pid else self.cart.restaurant_id
        if not restaurant:
            return None, "Which restaurant? Ask the customer, or show the restaurant list first."
        catalog, problem = await self._restaurants(pid)
        if catalog is None:
            return None, problem

        want = _norm(restaurant)
        exact = [r for r in catalog if want in (_norm(r["restaurant_id"]), _norm(r["name"]))]
        loose = [r for r in catalog if want and (want in _norm(r["name"]) or _norm(r["name"]) in want)]
        found = exact or loose
        if len(found) == 1:
            return (pid, found[0]), ""
        names = ", ".join(r["name"] for r in (found or catalog))
        if found:
            return None, f"'{restaurant}' matches several restaurants on {pid.title()}: {names}. Ask the customer which."
        return None, f"{pid.title()} has no restaurant called '{restaurant}'. It carries: {names}."

    async def _menu(self, pid: str, rid: str) -> tuple[dict | None, str]:
        reply = await ask_platform(self.directory, pid, {"action": "get_menu", "restaurant_id": rid})
        if reply["state"] != "completed" or not reply["data"]:
            return None, reply["text"] or f"{pid.title()} didn't return the menu ({reply['state']})."
        return reply["data"], ""

    def _register(self, rows: dict) -> str:
        """Remember which menu rows are on screen so cart changes can update their counters."""
        sid = f"menu-{uuid.uuid4().hex[:6]}"
        self.surfaces.append({"sid": sid, "rows": rows})
        self.surfaces = self.surfaces[-MAX_SURFACES:]
        return sid

    def _qty_map(self, rows: dict) -> dict:
        return {key: self.cart.qty(*place) for key, place in rows.items()}

    # ------------------------------------------------------------------ tools: browsing

    async def tool_ask_customer(self, question: str = "", choices: list | None = None) -> dict:
        options = [str(c) for c in (choices or []) if str(c).strip()][:6]
        if not question or len(options) < 2:
            return _error("ask_customer needs a question and at least two choices")
        self.blocks.append(
            {"kind": "choices", "choices": [{"label": c, "action": {"name": "say", "context": {"text": c}}} for c in options], "text": ""}
        )
        return _term(question)

    async def tool_list_restaurants(self, platform: str = "", cuisine: str = "", sort_by: str = "") -> dict:
        pid = self._platform_of(platform)
        if pid is None:
            return _error("Which platform: Swiggy or Zomato? Ask the customer with ask_customer.")
        catalog, problem = await self._restaurants(pid, refresh=True)
        if catalog is None:
            return _error(problem)
        self.focus = {"platform_id": pid}

        shown = list(catalog)
        if cuisine:
            words = _norm(cuisine).split()
            shown = [r for r in shown if all(w in _norm(r["cuisine"] + " " + r["name"]) for w in words)]
        key = {"rating": lambda r: -r["rating"], "delivery_time": lambda r: r["eta_min"], "delivery_fee": lambda r: r["delivery_fee"]}.get(sort_by)
        if key:
            shown.sort(key=key)
        if not shown:
            cuisines = ", ".join(sorted({r["cuisine"] for r in catalog}))
            return _error(f"No {pid.title()} restaurant matches '{cuisine}'. Cuisines available: {cuisines}.")

        block = {"kind": "restaurants", "platform_id": pid, "platform_name": pid.title(), "restaurants": shown, "filter": cuisine, "sort_by": sort_by}
        block["text"] = render_restaurants(block)
        self.blocks.append(block)
        brief = [
            {"id": r["restaurant_id"], "name": r["name"], "cuisine": r["cuisine"], "rating": r["rating"], "delivery_minutes": r["eta_min"],
             "delivery_fee": r["delivery_fee"], "discount_percent": r["discount_pct"], "open": r["is_online"]}
            for r in shown
        ]
        return {"text": f"Here are the restaurants on {pid.title()}.", "for_model": {"platform": pid.title(), "restaurants": brief, "shown_in_app": True}}

    async def tool_show_menu(self, restaurant: str = "", platform: str = "", veg_only: bool = False, max_price: float = 0, search: str = "") -> dict:
        found, problem = await self._resolve(platform, restaurant)
        if found is None:
            return _error(problem)
        pid, rest = found
        menu, problem = await self._menu(pid, rest["restaurant_id"])
        if menu is None:
            return _error(problem)
        self.focus = {"platform_id": pid, "restaurant_id": rest["restaurant_id"], "restaurant_name": menu["restaurant_name"]}

        items, notes = menu["items"], []
        limit = _positive(max_price)
        if veg_only:
            items = [i for i in items if i["is_veg"]]
            notes.append("Veg only")
        if limit:
            items = [i for i in items if i["price"] <= limit]
            notes.append(f"Up to {money(limit)}")
        if search:
            words = _norm(search).split()
            items = [i for i in items if all(w in _norm(i["name"] + " " + " ".join(i["tags"])) for w in words)]
            notes.append(f"Matching '{search}'")
        if notes:  # a filtered list only shows what can be ordered
            items = [i for i in items if i["available"]]
        if not items:
            return _error(f"{menu['restaurant_name']} has no dishes matching that on {pid.title()}.")

        rows = {f"i{n}": (pid, rest["restaurant_id"], item["name"]) for n, item in enumerate(items)}
        block = {
            "kind": "menu", "sid": self._register(rows), "platform_id": pid, "platform_name": pid.title(),
            "restaurant_id": rest["restaurant_id"], "restaurant_name": menu["restaurant_name"], "cuisine": menu["cuisine"],
            "rating": menu["rating"], "eta_min": menu["eta_min"], "delivery_fee": menu["delivery_fee"],
            "discount_pct": menu["discount_pct"], "is_online": menu["is_online"], "filters": " · ".join(notes),
            "rows": [{"key": key, "item": item} for key, item in zip(rows, items)],
        }
        block["qty"] = self._qty_map(rows)
        block["text"] = render_menu(block)
        self.blocks.append(block)
        brief = [{"name": i["name"], "price": i["price"], "veg": i["is_veg"], "available": i["available"], "tags": i["tags"]} for i in items]
        return {
            "text": f"Here is the menu of {menu['restaurant_name']} on {pid.title()}.",
            "for_model": {
                "restaurant": menu["restaurant_name"], "platform": pid.title(), "open": menu["is_online"],
                "delivery_fee": menu["delivery_fee"], "discount_percent": menu["discount_pct"],
                "delivery_minutes": menu["eta_min"], "dishes": brief, "shown_in_app": True,
            },
        }

    async def tool_search_dishes(self, query: str = "", platform: str = "", veg_only: bool = False) -> dict:
        if not str(query).strip():
            return _error("search_dishes needs a query")
        pid = str(platform or "").lower()
        platforms = [pid] if pid in config.PLATFORM_URLS else list(config.PLATFORM_URLS)
        replies = [
            (p, await ask_platform(self.directory, p, {"action": "search_dishes", "query": query, "veg_only": bool(veg_only)}))
            for p in platforms
        ]
        rows, groups, brief, down = {}, [], [], []
        for p, reply in replies:
            if reply["state"] != "completed" or not reply["data"]:
                down.append(p.title())
                continue
            for match in reply["data"]["matches"]:
                group_rows = []
                for item in match["items"]:
                    key = f"i{len(rows)}"
                    rows[key] = (p, match["restaurant_id"], item["name"])
                    group_rows.append({"key": key, "item": item})
                groups.append({"platform_id": p, "platform_name": p.title(), "restaurant_id": match["restaurant_id"],
                               "restaurant_name": match["restaurant_name"], "rating": match["rating"], "rows": group_rows})
                brief.append({"platform": p.title(), "restaurant": match["restaurant_name"], "dishes": [{"name": i["name"], "price": i["price"], "available": i["available"]} for i in match["items"]]})
        if not groups:
            return _error(f"No restaurant serves '{query}'." + (f" ({', '.join(down)} did not answer.)" if down else ""))
        block = {"kind": "dishes", "sid": self._register(rows), "query": query, "groups": groups, "qty": self._qty_map(rows)}
        block["text"] = render_dishes(block)
        self.blocks.append(block)
        return {"text": f"Here is where you can get {query}.", "for_model": {"query": query, "results": brief, "shown_in_app": True, "no_answer_from": down}}

    # ------------------------------------------------------------------ tools: cart

    @staticmethod
    def _match_dish(menu: dict, name: str) -> tuple[dict | None, str]:
        """Find one dish on a menu by the name the customer used. Returns (dish, "") or (None, what went wrong)."""
        want = _norm(name)
        restaurant = menu["restaurant_name"]
        matches = [i for i in menu["items"] if _norm(i["name"]) == want]
        if not matches:
            matches = [i for i in menu["items"] if want and all(w in _norm(i["name"]) for w in want.split())]
        if len(matches) > 1:
            return None, f"'{name}' matches several dishes at {restaurant}: " + ", ".join(m["name"] for m in matches)
        if not matches:
            names = ", ".join([i["name"] for i in menu["items"] if i["available"]][:10])
            return None, f"{restaurant} has no dish called '{name}' (available: {names})"
        if not matches[0]["available"]:
            return None, f"{matches[0]['name']} is unavailable at {restaurant} right now"
        return matches[0], ""

    async def tool_add_to_cart(self, items=None, restaurant: str = "", platform: str = "", item: str = "", qty: int = 1) -> dict:
        """Add one or more dishes from the same restaurant. All or nothing: if any dish can't be added
        (unknown, ambiguous, unavailable), nothing is added and the problems are reported."""
        wanted: list[tuple[str, int]] = []
        for entry in items if isinstance(items, list) else []:
            if isinstance(entry, dict):
                wanted.append((str(entry.get("name") or entry.get("item") or ""), _int(entry.get("qty", 1))))
            elif isinstance(entry, str):
                wanted.append((entry, 1))
        if item:  # a single dish, the way the buttons and older calls pass it
            wanted.append((str(item), _int(qty)))
        wanted = [(name, n) for name, n in wanted if name.strip()]
        if not wanted:
            return _error("Which dishes should I add? Pass them in 'items'.")

        found, problem = await self._resolve(platform, restaurant)
        if found is None:
            return _error(problem)
        pid, rest = found
        menu, problem = await self._menu(pid, rest["restaurant_id"])
        if menu is None:
            return _error(problem)
        if not menu["is_online"]:
            return _error(f"{menu['restaurant_name']} is offline right now.")

        dishes: dict[str, int] = {}  # exact dish name -> quantity (the same dish named twice is added up)
        problems = []
        for name, n in wanted:
            dish, why = self._match_dish(menu, name)
            if dish is None:
                problems.append(why)
            else:
                dishes[dish["name"]] = dishes.get(dish["name"], 0) + n
        if problems:
            return _error("Nothing was added. " + "; ".join(problems) + ". Tell or ask the customer.")

        if not self.cart.same_place(pid, rest["restaurant_id"]):
            self.blocks.append(
                {
                    "kind": "choices", "text": "",
                    "choices": [
                        {"label": f"Start a new cart with {menu['restaurant_name']}",
                         "action": {"name": "replace_cart", "context": {"platform_id": pid, "restaurant_id": rest["restaurant_id"], "items": [{"name": d, "qty": n} for d, n in dishes.items()]}}},
                        {"label": "Keep my current cart", "action": {"name": "show_cart", "context": {}}},
                    ],
                }
            )
            return _term(f"Your cart already has food from {self.cart.restaurant_name} on {self.cart.platform_id.title()}. Start a new cart with {menu['restaurant_name']}?")

        for dish, n in dishes.items():
            self.cart.add(pid, rest["restaurant_id"], menu["restaurant_name"], dish, n)
        self.focus = {"platform_id": pid, "restaurant_id": rest["restaurant_id"], "restaurant_name": menu["restaurant_name"]}
        self._show_cart = self._cart_changed = True
        parts = [f"{n} × {d}" for d, n in dishes.items()]
        return _term("Added " + (parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]) + ".")

    async def tool_remove_from_cart(self, item: str = "", qty: int = 0) -> dict:
        want = _norm(item)
        names = [n for n in self.cart.items if _norm(n) == want] or [n for n in self.cart.items if want and want in _norm(n)]
        if len(names) != 1:
            return _error(f"'{item}' is not (uniquely) in the cart. Cart: {self.cart.describe()}.")
        self.cart.remove(names[0], _int(qty, 0) if qty else 0)
        self._show_cart = self._cart_changed = True
        return _term(f"Removed {names[0]}." if names[0] not in self.cart.items else f"Updated {names[0]}.")

    async def tool_clear_cart(self) -> dict:
        self.cart.clear()
        self._cart_changed = True
        return _term("Your cart is empty.")

    async def tool_show_cart(self) -> dict:
        if self.cart.is_empty():
            return _term("Your cart is empty. Tell me what you'd like, or pick a restaurant to see its menu.")
        self._show_cart = True
        return _term("Here is your cart.")

    async def _price_cart(self) -> tuple[dict | None, str]:
        """The platform's real quote for the cart: prices, discount, fee, total, delivery time."""
        cart = self.cart
        reply = await ask_platform(
            self.directory,
            cart.platform_id,
            {"action": "get_quotes", "items": [{"name": n, "qty": q} for n, q in cart.items.items()], "restaurant_ids": [cart.restaurant_id]},
        )
        data = reply["data"] or {}
        if reply["state"] == "completed" and data.get("options"):
            return data["options"][0], ""
        if reply["state"] == "unreachable":
            return None, f"{cart.platform_id.title()} isn't responding right now."
        if data.get("needs_input"):
            return None, render_question(data["needs_input"])
        return None, reply["text"] or "The restaurant can't serve this cart right now."

    async def _cart_block(self) -> str:
        """Price the cart and add it to the reply. Returns one short sentence about it."""
        option, problem = await self._price_cart()
        cart = self.cart
        block = {"kind": "cart", "platform_id": cart.platform_id, "restaurant_id": cart.restaurant_id, "restaurant_name": cart.restaurant_name, "option": option, "problem": problem}
        block["text"] = render_cart(block)
        self.blocks.append(block)
        self.awaiting = "cart" if option else None
        if option is None:
            return problem
        return f"Cart total {money(option['total'])} (delivery included), about {round(option['eta_min'])} min."

    # ------------------------------------------------------------------ tools: best deal

    async def tool_find_best_deal(self, items: list | None = None, max_budget=None, max_eta_min=None, priority: str = "balanced") -> dict:
        clean = [{"name": str(i.get("name", "")).strip(), "qty": _int(i.get("qty", 1))} for i in (items or []) if isinstance(i, dict) and str(i.get("name", "")).strip()]
        if not clean:
            return _error("find_best_deal needs at least one dish")
        deal = {
            "items": clean, "max_budget": _positive(max_budget), "max_eta_min": _positive(max_eta_min),
            "priority": priority if priority in ("balanced", "cheapest", "fastest", "best_rated") else "balanced",
        }
        self.last_deal = deal
        self.evaluation = None
        quotes = await gather_quotes(clean, self.directory)

        if len(quotes["down"]) == len(config.PLATFORM_URLS):
            return _term("I can't reach Swiggy or Zomato right now. Are the agents running?")

        notes = []
        if quotes["down"]:
            notes.append(" and ".join(p.title() for p in quotes["down"]) + " didn't respond, so these options come from the other platform only.")

        if not quotes["options"]:
            if quotes["needs_input"]:
                question = render_question(quotes["needs_input"])
                self.blocks.append({"kind": "question", "needs_input": quotes["needs_input"], "text": question})
                return _term("I need you to pick one of these dishes.")
            return _term("Sorry, no restaurant on Swiggy or Zomato can serve all of: " + ", ".join(i["name"] for i in clean) + ".")

        if quotes["needs_input"]:
            places = ", ".join(sorted({n["restaurant_name"] for n in quotes["needs_input"]}))
            notes.append(f"{places} also has several matching dishes. Tell me which one if you meant that.")

        evaluation = evaluate(quotes["options"], deal["max_budget"], deal["max_eta_min"], deal["priority"])
        eligible, ineligible = evaluation["eligible"], evaluation["ineligible"]
        order_json = {"items": clean, "max_budget": deal["max_budget"], "max_eta_min": deal["max_eta_min"], "priority": deal["priority"]}

        if not eligible:
            closest = [{**e["option"], "violations": e["violations"]} for e in ineligible]
            text = "\n".join(["Nothing meets all your limits. These are the closest:", render_ineligible(ineligible, limit=3), "Tell me which limit to relax, e.g. 'make the budget 900'."] + notes)
            self.blocks.append({"kind": "no_match", "order": order_json, "closest": closest, "text": text})
            return _term("Nothing meets all your limits. These come closest.")

        self.evaluation = evaluation
        self.awaiting = "recommendation"
        summary = await self._recommend(deal, evaluation)
        parts = [render_bill(eligible[0]["option"]), "", render_options(eligible)]
        if ineligible:
            parts += ["", "Not eligible:", render_ineligible(ineligible)]
        if notes:
            parts += [""] + notes
        parts += ["", self._order_hint(eligible)]
        self.blocks.append(
            {
                "kind": "recommendation", "order": order_json,
                "options": [{**e["option"], "score": e["score"]} for e in eligible],
                "not_eligible": [{**e["option"], "violations": e["violations"]} for e in ineligible],
                "notes": notes, "text": "\n".join(parts),
            }
        )
        return _term(summary)

    @staticmethod
    def _order_hint(eligible: list[dict]) -> str:
        platforms = []
        for e in eligible:
            pid = e["option"]["platform_id"]
            if pid not in platforms:
                platforms.append(pid)
        hint = "Say 'order it' to place the first option"
        if len(platforms) > 1:
            hint += f", or e.g. 'order from {platforms[1].title()}'"
        return hint + "."

    async def _recommend(self, deal: dict, evaluation: dict) -> str:
        facts = recommendation_facts(evaluation, deal["max_budget"], deal["max_eta_min"], deal["priority"])
        try:
            text = await llm.chat_text(model=config.CLIENT_MODEL, system=RECOMMEND_PROMPT, user=json.dumps(facts, ensure_ascii=False))
            if text:
                return text
        except Exception:
            pass  # the cards carry the same facts, so fall back quietly
        return fallback_summary(evaluation["eligible"])

    # ------------------------------------------------------------------ tools: ordering and tracking

    async def tool_place_order(self, platform: str = "") -> dict:
        if self.awaiting == "cart" and not self.cart.is_empty():
            return await self._place_cart()
        if self.awaiting == "recommendation" and self.evaluation:
            return await self._place_from_deal(str(platform or "none").lower())
        return _error("Nothing is waiting for the customer's confirmation. Show the cart or find a deal first.")

    async def _place_cart(self) -> dict:
        if self.cart.is_empty():
            return _term("Your cart is empty, so there is nothing to order.")
        option, problem = await self._price_cart()
        if option is None:
            return _term(f"I couldn't price your cart: {problem}")
        ok, text = await self._place_option(option)
        if ok:
            self.cart.clear()
            self._cart_changed = True
            self.awaiting = None
        return _term(text)

    async def _place_from_deal(self, platform: str, restaurant_id: str = "") -> dict:
        """Place one of the recommended options (by platform, or platform + restaurant when a button was tapped)."""
        if not self.evaluation:
            return _term("There's nothing to order yet. Tell me what you'd like, e.g. 'biryani for 2 under ₹700 within 45 minutes'.")
        eligible = self.evaluation["eligible"]
        wanted = [e for e in eligible if platform in ("none", "", e["option"]["platform_id"]) and (not restaurant_id or e["option"]["restaurant_id"] == restaurant_id)]
        if not wanted:
            if restaurant_id:
                return _term("That option is no longer available. Send your order again to see fresh options.")
            available = sorted({e["option"]["platform_id"].title() for e in eligible})
            return _term(f"{platform.title()} has no option for this order. You can order it from: {', '.join(available)}.")
        ok, text = await self._place_option(wanted[0]["option"])
        if ok:
            self.awaiting = None
        return _term(text)

    async def _place_option(self, option: dict) -> tuple[bool, str]:
        """Place one priced option with its platform and follow the delivery live. Returns (delivered, reply text)."""
        if self._on_update is not None:
            await self._on_update(f"Placing your order with {label(option)} ({money(option['total'])})...")
        result = await place_order(self.directory, option, self._on_update)

        if result["state"] == "completed" and result["artifact"]:
            order = result["artifact"]
            self.placed = {"platform_id": order["platform_id"], "task_id": order["task_id"], "order_id": order["order_id"]}
            self.evaluation = None  # so a second "order it" can't order the same thing twice
            self.blocks.append({"kind": "receipt", "order": order, "text": render_receipt(order)})
            return True, f"Delivered! Your order from {order['restaurant_name']} has arrived. Enjoy your meal."

        if (result["data"] or {}).get("reason") == "price_changed":
            self.evaluation = None  # the options on screen are stale
            return False, f"{result['text']} Send your order again to see fresh prices."
        if result["state"] == "unreachable":
            return False, f"I couldn't place the order ({result['text']}). Nothing was ordered."
        return False, f"The order was not placed. {result['text'] or result['state']}"

    async def tool_track_order(self) -> dict:
        if self.placed is None:
            return _term("You haven't placed an order yet.")
        try:
            card = await self.directory.get_card(self.placed["platform_id"])
        except Exception as exc:
            return _term(f"I can't reach {self.placed['platform_id'].title()} right now ({type(exc).__name__}).")
        status = await fetch_task(card, self.placed["task_id"])
        if status["data"] and "stage" in status["data"]:
            self.blocks.append({"kind": "status", "stage": status["data"], "text": ""})
            return _term(render_status(status["data"]))
        return _term(f"Couldn't read the order status ({status['state']}: {status['text']}).")