import httpx

from a2a.client import A2ACardResolver

from food_agents.common import config


class RestaurantDirectory:
    """Finds restaurant agents by fetching their Agent Cards, and remembers them.

    A failed lookup is not cached, so a restaurant that was down is simply
    tried again on the next request. Start order of the agents doesn't matter.
    """

    def __init__(self) -> None:
        self._cards = {}

    async def get_card(self, restaurant_id: str):
        card = self._cards.get(restaurant_id)
        if card is None:
            async with httpx.AsyncClient(timeout=5) as http:
                resolver = A2ACardResolver(http, config.restaurant_url(restaurant_id))
                card = await resolver.get_agent_card()
            self._cards[restaurant_id] = card
        return card