import argparse
from functools import partial

import uvicorn

from food_agents.common import config
from food_agents.common.a2a_helpers import build_app
from food_agents.common.auth import TokenVerifier
from food_agents.common.interpret import PlainLanguage
from food_agents.restaurant.card import build_card
from food_agents.restaurant.executor import RestaurantExecutor
from food_agents.restaurant.language import understand


def main() -> None:
    parser = argparse.ArgumentParser(description="Run one restaurant as an A2A agent.")
    parser.add_argument("--id", required=True, choices=config.RESTAURANT_IDS)
    args = parser.parse_args()

    card = build_card(args.id)
    # Only the two platforms (and the developer console) may call a restaurant, with a token made for restaurants.
    verifier = TokenVerifier(config.RESTAURANT_AUDIENCE, allowed_clients=["platform-swiggy", "platform-zomato", "dev-console"])
    # Data requests go straight to the executor; plain text is first turned into a data request.
    executor = PlainLanguage(RestaurantExecutor(args.id), partial(understand, args.id))
    app = build_app(card, executor, verifier)
    port = config.restaurant_port(args.id)
    print(f"{card.name} agent on http://localhost:{port}  (card: /.well-known/agent-card.json)")
    print("token required: " + ("yes (Keycloak)" if config.AUTH_ENABLED else "no (AUTH_ENABLED=false)"))
    uvicorn.run(app, host="127.0.0.1", port=port)


if __name__ == "__main__":
    main()
