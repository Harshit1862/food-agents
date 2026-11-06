"""Checks the Keycloak setup and food_agents/common/auth.py.

    python -m scripts.auth_check

Needs Keycloak running (docker compose up -d). Starts nothing else.
"""

import asyncio

import httpx
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

from food_agents.common import config
from food_agents.common.auth import AuthError, BearerAuth, TokenVerifier, service_token

RESULTS = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append(ok)
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"   ({detail})" if detail else ""))


async def expect_refused(verifier: TokenVerifier, token: str, status: int) -> tuple[bool, str]:
    try:
        await verifier.verify(token)
    except AuthError as exc:
        return exc.status == status, f"{exc.status}: {exc.message}"
    return False, "accepted"


async def main() -> None:
    print(f"Keycloak: {config.KEYCLOAK_ISSUER}\n")

    print("1. Service tokens")
    tokens = {}
    for name in config.SERVICE_SECRETS:
        try:
            tokens[name] = await service_token(name).get()
            check(f"{name} gets a token", True)
        except Exception as exc:
            check(f"{name} gets a token", False, str(exc))
            print("\nStop: is Keycloak running on", config.KEYCLOAK_URL, "?")
            return

    print("\n2. Checking tokens")
    swiggy = TokenVerifier(config.platform_audience("swiggy"), allowed_clients=["client-agent"])
    zomato = TokenVerifier(config.platform_audience("zomato"), allowed_clients=["client-agent"])
    restaurants = TokenVerifier(config.RESTAURANT_AUDIENCE, allowed_clients=["platform-swiggy", "platform-zomato"])

    claims = await swiggy.verify(tokens["client-agent"])
    check("Swiggy accepts the client agent's token", claims["azp"] == "client-agent", f"azp={claims['azp']}")
    claims = await zomato.verify(tokens["client-agent"])
    check("Zomato accepts the client agent's token", claims["azp"] == "client-agent")
    claims = await restaurants.verify(tokens["platform-swiggy"])
    check("Restaurants accept Swiggy's token", claims["azp"] == "platform-swiggy")
    claims = await restaurants.verify(tokens["platform-zomato"])
    check("Restaurants accept Zomato's token", claims["azp"] == "platform-zomato")

    ok, detail = await expect_refused(restaurants, tokens["client-agent"], 403)
    check("Restaurants REFUSE the client agent's token (wrong audience)", ok, detail)
    ok, detail = await expect_refused(swiggy, tokens["platform-swiggy"], 403)
    check("Swiggy REFUSES a platform token (wrong audience)", ok, detail)
    ok, detail = await expect_refused(swiggy, tokens["client-agent"][:-4] + "abcd", 401)
    check("A token with a damaged signature is refused", ok, detail)
    ok, detail = await expect_refused(swiggy, "not-a-token", 401)
    check("Garbage is refused", ok, detail)

    print("\n3. The middleware in front of an app")

    async def hello(request):
        return JSONResponse({"hello": request.state.auth["azp"] if hasattr(request.state, "auth") else "?"})

    async def card(request):
        return JSONResponse({"card": True})

    app = Starlette(routes=[Route("/", hello, methods=["GET", "POST"]), Route("/.well-known/agent-card.json", card)])
    app = BearerAuth(app, swiggy)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://agent") as http:
        r = await http.get("/.well-known/agent-card.json")
        check("The Agent Card is public (no token)", r.status_code == 200, str(r.status_code))
        r = await http.get("/")
        check("No token -> 401", r.status_code == 401, r.json()["error"])
        r = await http.get("/", headers={"Authorization": "Bearer nonsense"})
        check("Bad token -> 401", r.status_code == 401, r.json()["error"])
        r = await http.get("/", headers={"Authorization": f"Bearer {tokens['platform-swiggy']}"})
        check("Token for another audience -> 403", r.status_code == 403, r.json()["error"])
        r = await http.get("/", headers={"Authorization": f"Bearer {tokens['client-agent']}"})
        check("Right token -> 200", r.status_code == 200, str(r.json()))

    print(f"\n{sum(RESULTS)}/{len(RESULTS)} checks passed")


if __name__ == "__main__":
    asyncio.run(main())