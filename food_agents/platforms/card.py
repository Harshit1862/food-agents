from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill

from food_agents.common import config
from food_agents.platforms import tools


def build_card(platform_id: str) -> AgentCard:
    platform = tools.get_platform(platform_id)
    if platform is None:
        raise SystemExit(f"No platform '{platform_id}' in MongoDB. Did you run seed/seed.py?")

    skill = AgentSkill(
        id="get_quotes",
        name="Get quotes",
        description=(
            "Asks every restaurant this platform carries to quote the requested items, "
            "then returns one priced option per restaurant with discount, delivery fee, "
            "total and delivery time (ETA). Restaurants that need a choice or can't serve "
            "the order are reported separately. Optional: restaurant_ids to limit the search."
        ),
        tags=["quote", "delivery"],
        examples=[
            "How much for 2 Hyderabadi Chicken Biryani?",
            '{"action": "get_quotes", "items": [{"name": "Hyderabadi Chicken Biryani", "qty": 2}]}',
            '{"action": "get_quotes", "items": [{"name": "Raita"}], "restaurant_ids": ["biryani_junction"]}',
        ],
    )

    place_order = AgentSkill(
        id="place_order",
        name="Place order",
        description=(
            "Prices the order again, asks the restaurant to accept it, saves it, then streams "
            "live delivery stages (confirmed, preparing, rider_assigned, out_for_delivery, delivered). "
            "Refused if the restaurant declines or if expected_total no longer matches the price."
        ),
        tags=["order", "tracking"],
        examples=[
            "Order 2 Butter Chicken from Spice Route",
            '{"action": "place_order", "restaurant_id": "spice_route", '
            '"items": [{"name": "Butter Chicken", "qty": 2}], "expected_total": 855}'
        ],
    )

    list_restaurants = AgentSkill(
        id="list_restaurants",
        name="List restaurants",
        description=(
            "Lists every restaurant this platform carries, with cuisine, rating, delivery fee, "
            "discount and estimated delivery time. The restaurants' own facts are fetched live."
        ),
        tags=["browse", "restaurants"],
        examples=["Which restaurants do you have?", '{"action": "list_restaurants"}'],
    )

    get_menu = AgentSkill(
        id="get_menu",
        name="Get menu",
        description="Returns one restaurant's menu at this platform's prices, plus its fee, discount and delivery time.",
        tags=["browse", "menu"],
        examples=["Show me the Spice Route menu", '{"action": "get_menu", "restaurant_id": "spice_route"}'],
    )

    search_dishes = AgentSkill(
        id="search_dishes",
        name="Search dishes",
        description=(
            "Finds which restaurants on this platform serve a dish (all words of the query must match "
            "the dish name or tags). Optional: veg_only."
        ),
        tags=["browse", "search"],
        examples=["Who serves biryani?", '{"action": "search_dishes", "query": "biryani"}'],
    )

    return AgentCard(
        name=platform["name"],
        description=f"{platform['name']} food-delivery aggregator agent.",
        version="0.1.0",
        supported_interfaces=[
            AgentInterface(
                protocol_binding="JSONRPC",
                protocol_version="1.0",
                url=f"{config.PLATFORM_URLS[platform_id]}/",
            )
        ],
        capabilities=AgentCapabilities(streaming=True, push_notifications=False),
        default_input_modes=["application/json", "text/plain"],
        default_output_modes=["application/json", "text/plain"],
        skills=[list_restaurants, get_menu, search_dishes, skill, place_order],
    )
