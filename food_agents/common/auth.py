"""Keycloak sign-in for the agents: check the tokens that arrive, fetch the tokens we send.

A token is a signed JSON document (a JWT) that Keycloak hands out after a login. It says who the
caller is ("sub", "azp"), who it is meant for ("aud") and until when it is valid ("exp").

  Receiving:  TokenVerifier checks the signature (with Keycloak's public keys), the issuer, the
              audience and the expiry. BearerAuth is a middleware that does this for every request
              to an agent, except the public Agent Card.
  Sending:    ServiceToken asks Keycloak for a token for one of our services (client_credentials)
              and keeps it until shortly before it expires.
"""

import asyncio
import time

import httpx
import jwt
from jwt import PyJWKClient
from jwt.exceptions import PyJWKClientConnectionError
from starlette.responses import JSONResponse

from food_agents.common import config


class AuthError(Exception):
    """A request that must be refused. `status` is the HTTP status to answer with."""

    def __init__(self, message: str, status: int = 401) -> None:
        super().__init__(message)
        self.message = message
        self.status = status


class TokenVerifier:
    """Checks tokens meant for ONE audience. If allowed_clients is given, only tokens issued
    to those Keycloak clients (the token's "azp") are accepted."""

    def __init__(self, audience: str, allowed_clients: list[str] | None = None) -> None:
        self.audience = audience
        self.allowed_clients = set(allowed_clients) if allowed_clients else None
        # Downloads Keycloak's public keys once and keeps them for an hour.
        self._keys = PyJWKClient(config.KEYCLOAK_JWKS_URL, cache_keys=True, lifespan=3600)

    def _verify(self, token: str) -> dict:
        try:
            key = self._keys.get_signing_key_from_jwt(token).key
            claims = jwt.decode(
                token,
                key,
                algorithms=["RS256"],
                audience=self.audience,
                issuer=config.KEYCLOAK_ISSUER,
                options={"require": ["exp", "iss", "aud"]},
                leeway=10,  # seconds of clock difference we forgive
            )
        except PyJWKClientConnectionError:
            raise AuthError("cannot reach Keycloak to check the token", 503)
        except jwt.ExpiredSignatureError:
            raise AuthError("token expired")
        except jwt.InvalidAudienceError:
            raise AuthError(f"token is not meant for '{self.audience}'", 403)
        except jwt.PyJWTError as exc:
            raise AuthError(f"invalid token ({type(exc).__name__})")

        if self.allowed_clients and claims.get("azp") not in self.allowed_clients:
            raise AuthError(f"client '{claims.get('azp')}' may not call this agent", 403)
        return claims

    async def verify(self, token: str) -> dict:
        """Returns the token's claims, or raises AuthError. Runs off the event loop because
        fetching the keys is a blocking HTTP call."""
        return await asyncio.to_thread(self._verify, token)


class BearerAuth:
    """ASGI middleware: every request must carry 'Authorization: Bearer <token>'.
    The Agent Card (/.well-known/...) stays public, because other agents must read it to find out
    how to talk to this one. The checked claims are left in scope["state"]["auth"]."""

    def __init__(self, app, verifier: TokenVerifier, public_prefixes: tuple[str, ...] = ("/.well-known/",)) -> None:
        self.app = app
        self.verifier = verifier
        self.public_prefixes = public_prefixes

    async def __call__(self, scope, receive, send) -> None:
        if (
            scope["type"] != "http"
            or not config.AUTH_ENABLED
            or scope["method"] == "OPTIONS"  # browsers ask this first (CORS preflight), without a token
            or scope["path"].startswith(self.public_prefixes)
        ):
            await self.app(scope, receive, send)
            return

        header = dict(scope["headers"]).get(b"authorization", b"").decode()
        scheme, _, token = header.partition(" ")
        try:
            if scheme.lower() != "bearer" or not token.strip():
                raise AuthError("missing bearer token")
            claims = await self.verifier.verify(token.strip())
        except AuthError as exc:
            response = JSONResponse(
                {"error": exc.message},
                status_code=exc.status,
                headers={"WWW-Authenticate": 'Bearer realm="food-agents"'},
            )
            await response(scope, receive, send)
            return

        scope.setdefault("state", {})["auth"] = claims
        await self.app(scope, receive, send)


class ServiceToken:
    """The token one of our services (client-agent, platform-swiggy...) shows when it calls another
    agent. Asked for from Keycloak with the "client credentials" grant: the service proves who it
    is with its secret, and no person is involved."""

    def __init__(self, client_id: str, secret: str) -> None:
        self.client_id = client_id
        self._secret = secret
        self._token: str | None = None
        self._expires_at = 0.0
        self._lock = asyncio.Lock()

    async def get(self) -> str:
        async with self._lock:
            if self._token is None or time.time() > self._expires_at - 30:  # renew 30 s early
                await self._fetch()
            return self._token

    async def _fetch(self) -> None:
        async with httpx.AsyncClient(timeout=5) as http:
            response = await http.post(
                config.KEYCLOAK_TOKEN_URL,
                data={"grant_type": "client_credentials", "client_id": self.client_id, "client_secret": self._secret},
            )
        if response.status_code != 200:
            raise RuntimeError(f"Keycloak refused a token for '{self.client_id}': {response.status_code} {response.text[:150]}")
        body = response.json()
        self._token = body["access_token"]
        self._expires_at = time.time() + int(body.get("expires_in", 60))


_services: dict[str, ServiceToken] = {}


def service_token(client_id: str) -> ServiceToken:
    """The (shared, cached) token provider of one of our services."""
    if client_id not in _services:
        _services[client_id] = ServiceToken(client_id, config.SERVICE_SECRETS[client_id])
    return _services[client_id]