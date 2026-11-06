"""What the client agent's language model is told: its instructions and the tools it may call.

The model never sees a price it made up: every tool is run by code in agent.py, which asks the
Swiggy / Zomato agents over A2A and hands back the real answer. The model only decides WHICH tool
to call and with what arguments, and (for browsing) writes one or two friendly sentences.
"""

SYSTEM_PROMPT = """You are the customer's food-ordering assistant. You act only through tools. The tools talk to the Swiggy and Zomato delivery agents, and through them to the restaurants. Never invent restaurants, dishes, prices, fees or delivery times: say only what tool results contain.

The app draws rich cards for most tool results (restaurant tiles, menus with Add buttons, the cart with totals, comparison cards). After list_restaurants, show_menu or search_dishes write ONE or TWO short sentences and do not repeat lists or numbers that are already on screen. Plain text only, no markdown.

How to handle requests:
- "Show restaurants", "what is on Zomato", "what can I order": a platform is needed. If the customer has not said Swiggy or Zomato (and 'Looking at' below has no platform), call ask_customer with the question and choices ["Swiggy", "Zomato"]. Once they answer, call list_restaurants. If they name a cuisine ("show me Chinese places on Swiggy"), pass it as cuisine.
- When a restaurant is chosen (by name, "the first one", "show its menu"): call show_menu. Diet or price limits ("veg only", "under 200") are arguments of show_menu.
- "Which restaurants have biryani?": search_dishes.
- Adding food: add_to_cart. Pass ALL the dishes the customer named in ONE call, in `items` (each with a name and qty; qty defaults to 1): "add a margherita and a pepperoni pizza" is one call with two items. If a dish could mean several different dishes, call ask_customer with those dish names as choices. A cart holds food from ONE restaurant on ONE platform. 
- ask_customer choices are ALTERNATIVES: tapping one gives you one answer. Never make a choice that combines several dishes ("Margherita + Pepperoni").
- The best deal across both platforms for named dishes ("2 butter chicken and 3 garlic naan under 900, I'm in a hurry"): find_best_deal. Pass the complete list of dishes every time. Keep the limits from 'Last deal request' only when the customer is changing that same request ("make the budget 900"); a different request starts fresh with no old limits.
- place_order only when the customer clearly confirms ("order it", "place the order", "yes") AND 'Waiting for the customer' below says something is waiting. Never order just because food was requested: first add it to the cart or find a deal so they see the price.
- "Where is my order": track_order.
- If a tool returns an error, fix the call if you can (for example ask which platform), otherwise tell the customer plainly. Never pretend an action worked.
- Anything that is not ordering food from Swiggy or Zomato (groceries, Instamart, other apps, medicine, general chat): answer briefly and say what you can do. Do not claim capabilities you do not have.
Keep replies short and friendly."""


def _tool(name: str, description: str, properties: dict, required: list[str] | None = None) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": properties, "required": required or []},
        },
    }


PLATFORM = {"type": "string", "enum": ["swiggy", "zomato"]}

TOOL_SPECS = [
    _tool(
        "ask_customer",
        "Ask the customer a question with tappable answer buttons (for example which platform, or which of several matching dishes). Ends your turn.",
        {
            "question": {"type": "string", "description": "The question to show."},
            "choices": {"type": "array", "items": {"type": "string"}, "description": "2 to 6 short answers."},
        },
        ["question", "choices"],
    ),
    _tool(
        "list_restaurants",
        "Show the restaurants a platform carries (cuisine, rating, delivery fee, discount, estimated delivery time).",
        {
            "platform": PLATFORM,
            "cuisine": {"type": "string", "description": "Optional: keep only restaurants whose cuisine or name contains this."},
            "sort_by": {"type": "string", "enum": ["rating", "delivery_time", "delivery_fee"], "description": "Optional ordering."},
        },
        ["platform"],
    ),
    _tool(
        "show_menu",
        "Show one restaurant's menu at a platform's prices, with Add buttons. 'restaurant' may be its name or id.",
        {
            "restaurant": {"type": "string"},
            "platform": {**PLATFORM, "description": "Optional if the customer is already looking at a platform."},
            "veg_only": {"type": "boolean"},
            "max_price": {"type": "number", "description": "Optional: only dishes up to this price in rupees."},
            "search": {"type": "string", "description": "Optional words the dish name must contain."},
        },
        ["restaurant"],
    ),
    _tool(
        "search_dishes",
        "Find which restaurants serve a dish. Searches one platform, or both if platform is omitted.",
        {"query": {"type": "string"}, "platform": PLATFORM, "veg_only": {"type": "boolean"}},
        ["query"],
    ),
        _tool(
        "add_to_cart",
        "Put one or more dishes in the cart, all from one restaurant. Each dish name must be a dish name from a menu.",
        {
            "items": {
                "type": "array",
                "description": "Every dish to add, with its quantity.",
                "items": {
                    "type": "object",
                    "properties": {"name": {"type": "string"}, "qty": {"type": "integer", "description": "Default 1."}},
                    "required": ["name"],
                },
            },
            "restaurant": {"type": "string", "description": "Optional if the customer is already looking at a restaurant."},
            "platform": PLATFORM,
        },
        ["items"],
    ),
    _tool(
        "remove_from_cart",
        "Take a dish out of the cart (all of it, or `qty` of it).",
        {"item": {"type": "string"}, "qty": {"type": "integer", "description": "Optional: how many to remove."}},
        ["item"],
    ),
    _tool("clear_cart", "Empty the cart.", {}),
    _tool("show_cart", "Show the cart with the real total and delivery time.", {}),
    _tool(
        "find_best_deal",
        "Compare Swiggy and Zomato for specific dishes and recommend the best option, within optional limits. Use for 'cheapest', 'fastest', 'best deal'.",
        {
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"name": {"type": "string"}, "qty": {"type": "integer"}},
                    "required": ["name", "qty"],
                },
            },
            "max_budget": {"type": "number", "description": "Most the customer will pay in rupees, delivery included."},
            "max_eta_min": {"type": "number", "description": "Longest acceptable delivery time in minutes."},
            "priority": {"type": "string", "enum": ["balanced", "cheapest", "fastest", "best_rated"]},
        },
        ["items"],
    ),
    _tool(
        "place_order",
        "Place the order that is waiting for the customer's decision (the cart, or the recommended option). Only after the customer clearly confirmed. 'platform' picks a platform when a recommendation offers both.",
        {"platform": PLATFORM},
    ),
    _tool("track_order", "Where the customer's placed order is right now.", {}),
]