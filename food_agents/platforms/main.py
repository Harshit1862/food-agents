import argparse
from functools import partial

import uvicorn

from food_agents.common import config
from food_agents.common.a2a_client import use_identity
from food_agents.common.a2a_helpers import build_app
from food_agents.common.auth import TokenVerifier
from food_agents.common.interpret import PlainLanguage
from food_agents.platforms.card import build_card
from food_agents.platforms.executor import PlatformExecutor
from food_agents.platforms.language import understand


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Swiggy or Zomato as an A2A agent.")
    parser.add_argument("--id", required=True, choices=list(config.PLATFORM_PORTS))
    args = parser.parse_args()

    card = build_card(args.id)
    # Calls going OUT (to the restaurants) carry this platform's own token.
    use_identity(f"platform-{args.id}")
    # Calls coming IN: the client agent (and the developer console), with a token made for this platform.
    verifier = TokenVerifier(config.platform_audience(args.id), allowed_clients=["client-agent", "dev-console"])
    # Data requests go straight to the executor; plain text is first turned into a data request.
    executor = PlainLanguage(PlatformExecutor(args.id), partial(understand, args.id))
    app = build_app(card, executor, verifier)
    port = config.PLATFORM_PORTS[args.id]
    print(f"{card.name} agent on http://localhost:{port}  (card: /.well-known/agent-card.json)")
    print("token required: " + ("yes (Keycloak)" if config.AUTH_ENABLED else "no (AUTH_ENABLED=false)"))
    uvicorn.run(app, host="127.0.0.1", port=port)


if __name__ == "__main__":
    main()
