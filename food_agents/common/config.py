import os

from dotenv import load_dotenv

load_dotenv()

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
CLIENT_MODEL = os.getenv("CLIENT_MODEL", "openai/gpt-oss-120b")
AGENT_MODEL = os.getenv("AGENT_MODEL", "openai/gpt-oss-20b")

MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
MONGO_DB = os.getenv("MONGO_DB", "food_agents")

GATEWAY_PORT = int(os.getenv("GATEWAY_PORT", "8000"))
SWIGGY_PORT = int(os.getenv("SWIGGY_PORT", "9001"))
ZOMATO_PORT = int(os.getenv("ZOMATO_PORT", "9002"))
RESTAURANT_BASE_PORT = int(os.getenv("RESTAURANT_BASE_PORT", "9101"))

# Order tracking is simulated: 1 delivery minute lasts this many real seconds.
# 0.6 turns a 31-minute delivery into about 19 seconds. Use 0.05 for quick tests.
SIM_SECONDS_PER_MIN = float(os.getenv("SIM_SECONDS_PER_MIN", "0.6"))

# Fixed order: each restaurant gets BASE_PORT + its index, so ports are stable.
RESTAURANT_IDS = [
    "spice_route",
    "biryani_junction",
    "dosa_corner",
    "wok_this_way",
    "pizza_piazza",
    "green_bowl",
    "tandoor_tales",
    "sweet_spot",
]


def restaurant_port(restaurant_id: str) -> int:
    return RESTAURANT_BASE_PORT + RESTAURANT_IDS.index(restaurant_id)


def restaurant_url(restaurant_id: str) -> str:
    return f"http://localhost:{restaurant_port(restaurant_id)}"


PLATFORM_URLS = {
    "swiggy": f"http://localhost:{SWIGGY_PORT}",
    "zomato": f"http://localhost:{ZOMATO_PORT}",
}

PLATFORM_PORTS = {"swiggy": SWIGGY_PORT, "zomato": ZOMATO_PORT}

# ---- sign-in with Keycloak -------------------------------------------------------------
# Set AUTH_ENABLED=false to run everything without Keycloak (the agents then accept any caller).
AUTH_ENABLED = os.getenv("AUTH_ENABLED", "true").lower() in ("1", "true", "yes")

KEYCLOAK_URL = os.getenv("KEYCLOAK_URL", "http://localhost:8080")
KEYCLOAK_REALM = os.getenv("KEYCLOAK_REALM", "food-agents")
KEYCLOAK_ISSUER = f"{KEYCLOAK_URL}/realms/{KEYCLOAK_REALM}"  # who signs the tokens
KEYCLOAK_JWKS_URL = f"{KEYCLOAK_ISSUER}/protocol/openid-connect/certs"  # the public keys that check the signature
KEYCLOAK_TOKEN_URL = f"{KEYCLOAK_ISSUER}/protocol/openid-connect/token"

# An "audience" says who a token is meant for. An agent rejects tokens meant for someone else.
GATEWAY_AUDIENCE = "food-gateway"  # tokens of logged-in customers
RESTAURANT_AUDIENCE = "restaurant-agents"  # tokens the platforms use to call restaurants


def platform_audience(platform_id: str) -> str:
    return f"platform-{platform_id}"  # tokens the client agent uses to call Swiggy / Zomato


# The services that get their own tokens (Keycloak "clients" with a secret).
# These defaults match keycloak/food-agents-realm.json. They are for local development only.
SERVICE_SECRETS = {
    "client-agent": os.getenv("CLIENT_AGENT_SECRET", "client-agent-dev-secret"),
    "platform-swiggy": os.getenv("PLATFORM_SWIGGY_SECRET", "platform-swiggy-dev-secret"),
    "platform-zomato": os.getenv("PLATFORM_ZOMATO_SECRET", "platform-zomato-dev-secret"),
    # Used only by the gateway, for the developer console (talk to any agent directly).
    "dev-console": os.getenv("DEV_CONSOLE_SECRET", "dev-console-dev-secret"),
}