from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill

from food_agents.common import config
from food_agents.restaurant import tools


def build_card(restaurant_id: str) -> AgentCard:
    """The Agent Card is this restaurant's public identity: who it is, where it
    lives, and what it can do. Other agents read it before sending any work."""
    restaurant = tools.get_restaurant(restaurant_id)
    if restaurant is None:
        raise SystemExit(f"No restaurant '{restaurant_id}' in MongoDB. Did you run seed/seed.py?")

    skills = [
        AgentSkill(
            id="get_info",
            name="Get info",
            description="Returns the restaurant's profile: cuisine, rating, prep time, distance, and whether it is online.",
            tags=["profile"],
            examples=["Tell me about this restaurant", '{"action": "get_info"}'],
        ),
        AgentSkill(
            id="check_menu",
            name="Check menu",
            description="Returns the full menu with base prices and availability.",
            tags=["menu"],
            examples=["Show me the menu", '{"action": "check_menu"}'],
        ),
        AgentSkill(
            id="search_menu",
            name="Search menu",
            description="Filters available items by is_veg, max_price and tag.",
            tags=["menu", "search"],
            examples=["Veg dishes under 250", '{"action": "search_menu", "is_veg": true, "max_price": 250}'],
        ),
        AgentSkill(
            id="quote_items",
            name="Quote items",
            description=(
                "Prices a list of items at base price and reports prep time. "
                "Asks for input if an item is unavailable, unknown or ambiguous."
            ),
            tags=["quote", "order"],
            examples=["How much for 2 raita?", '{"action": "quote_items", "items": [{"name": "Raita", "qty": 2}]}'],
        ),
        AgentSkill(
            id="accept_order",
            name="Accept order",
            description=(
                "Re-checks that every item is available right now and the restaurant is online, "
                "then accepts the order (and records it) or refuses it with a reason."
            ),
            tags=["order"],
            examples=['{"action": "accept_order", "order_id": "ord_demo1", "items": [{"name": "Raita", "qty": 2}]}'],
        ),
    ]

    return AgentCard(
        name=restaurant["name"],
        description=f"{restaurant['name']} ({restaurant['cuisine']}) restaurant agent.",
        version="0.1.0",
        supported_interfaces=[
            AgentInterface(
                protocol_binding="JSONRPC",
                protocol_version="1.0",
                url=f"{config.restaurant_url(restaurant_id)}/",
            )
        ],
        capabilities=AgentCapabilities(streaming=True, push_notifications=False),
        default_input_modes=["application/json", "text/plain"],
        default_output_modes=["application/json", "text/plain"],
        skills=skills,
    )
