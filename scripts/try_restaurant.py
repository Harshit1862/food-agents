import json

from food_agents.restaurant import tools


def show(title, data):
    print(f"\n=== {title} ===")
    print(json.dumps(data, indent=2, ensure_ascii=False))


show("Spice Route: menu (names only)", [m["name"] for m in tools.check_menu("spice_route")])

show("Search: veg, under 250 at Biryani Junction",
     [(m["name"], m["base_price"]) for m in tools.search_menu("biryani_junction", is_veg=True, max_price=250)])

show("Quote: good order",
     tools.quote_items("spice_route", [{"name": "Butter Chicken", "qty": 2}, {"name": "garlic naan", "qty": 3}]))

show("Quote: unavailable item",
     tools.quote_items("spice_route", [{"name": "Chicken Tikka Masala", "qty": 1}]))

show("Quote: ambiguous item",
     tools.quote_items("biryani_junction", [{"name": "biryani", "qty": 2}]))

show("Quote: item this restaurant doesn't sell",
     tools.quote_items("spice_route", [{"name": "pizza", "qty": 1}]))

show("Quote: unknown restaurant", tools.quote_items("nowhere", [{"name": "x"}]))