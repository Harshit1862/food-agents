import uvicorn

from food_agents.common import config
from food_agents.common.a2a_client import use_identity


def main() -> None:
    # The client agent runs inside the gateway, so the gateway's calls to Swiggy and Zomato
    # carry the client agent's token.
    use_identity("client-agent")
    print(f"Gateway on http://localhost:{config.GATEWAY_PORT}  (try /api/health and /api/agents)")
    uvicorn.run("food_agents.gateway.app:app", host="127.0.0.1", port=config.GATEWAY_PORT)


if __name__ == "__main__":
    main()