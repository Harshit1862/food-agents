from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware

from a2a.helpers import get_data_parts
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import AgentCard, Message

from food_agents.common import config
from food_agents.common.auth import BearerAuth, TokenVerifier


def advertise_security(card: AgentCard) -> None:
    """Write into the Agent Card that this agent wants a Keycloak token, so a caller reading the
    card knows before it sends anything. (The card itself stays public.)"""
    scheme = card.security_schemes["keycloak"].open_id_connect_security_scheme
    scheme.description = "Send 'Authorization: Bearer <token>'. Get the token from Keycloak (realm food-agents)."
    scheme.open_id_connect_url = f"{config.KEYCLOAK_ISSUER}/.well-known/openid-configuration"
    card.security_requirements.add().schemes["keycloak"].list.extend([])


def build_app(card: AgentCard, executor, verifier: TokenVerifier | None = None) -> Starlette:
    """Wrap an executor into a web app that speaks A2A.

    Serves two things: the Agent Card at /.well-known/agent-card.json,
    and the JSON-RPC endpoint at "/" where tasks and messages arrive.

    With a verifier, every request to the JSON-RPC endpoint must carry a valid Keycloak token
    (the card stays public).
    """
    handler = DefaultRequestHandler(
        agent_executor=executor,
        task_store=InMemoryTaskStore(),
        agent_card=card,
    )
    # The first middleware is the outermost one. CORS must come before the token check, so the
    # browser's preflight request is answered without a token and error answers carry CORS headers.
    middleware = [
        # CORS lets the React app (a different origin) call this agent from the browser.
        Middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_methods=["*"],
            allow_headers=["*"],
        )
    ]
    if verifier is not None:
        if config.AUTH_ENABLED:
            advertise_security(card)
        middleware.append(Middleware(BearerAuth, verifier=verifier))

    return Starlette(
        routes=[
            *create_agent_card_routes(card),
            *create_jsonrpc_routes(handler, rpc_url="/"),
        ],
        middleware=middleware,
    )


def get_request_data(message: Message) -> dict | None:
    """Return the first structured (data) part of a message as a dict, if any."""
    parts = get_data_parts(message.parts)
    if parts and isinstance(parts[0], dict):
        return parts[0]
    return None