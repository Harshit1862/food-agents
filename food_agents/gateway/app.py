"""The gateway: a small HTTP + Server-Sent-Events API in front of the agents, for the React app.

    GET  /api/health                      is the gateway up (the only route that needs no login)
    GET  /api/me                          who the gateway thinks you are (from your Keycloak token)
    GET  /api/agents                      every agent's card summary (name, skills, examples) and whether it is up
    POST /api/chat                        talk to the client agent        {message}               -> SSE
    POST /api/chat/action                 the customer clicked something  {action: {name, context}} -> SSE
    POST /api/chat/reset                  start a new order
    POST /api/agents/{agent_id}/send      talk to ONE agent directly      {payload}               -> SSE
                                          (only for users with the "developer" role)

Every route except /api/health needs "Authorization: Bearer <token>": the customer's Keycloak token.
The conversation (cart, order) belongs to the user in the token, not to an id the browser makes up.

SSE events from /api/chat:   session, update (live delivery stage), reply (text + a2ui), error, done.
SSE events from .../send:    task, status, artifact, message, error, done.
Events that can be drawn carry an "a2ui" list: A2UI messages for the React renderer.
"""

import asyncio
import json
import uuid
from collections import OrderedDict

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route

from food_agents.client.agent import ClientAgent
from food_agents.common import config
from food_agents.common.auth import BearerAuth, TokenVerifier
from food_agents.gateway import a2ui, direct

SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
MAX_SESSIONS = 200
_background: set[asyncio.Task] = set()  # keeps running chat tasks alive even if the browser disconnects


def sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


class Session:
    def __init__(self) -> None:
        self.agent = ClientAgent()
        self.lock = asyncio.Lock()  # one message at a time per customer


class Sessions:
    def __init__(self) -> None:
        self._items: OrderedDict[str, Session] = OrderedDict()

    def get(self, session_id: str | None) -> tuple[str, Session]:
        sid = session_id or uuid.uuid4().hex
        if sid not in self._items:
            self._items[sid] = Session()
            while len(self._items) > MAX_SESSIONS:
                self._items.popitem(last=False)  # forget the oldest customer
        self._items.move_to_end(sid)
        return sid, self._items[sid]

    def find(self, session_id: str | None) -> Session | None:
        return self._items.get(session_id) if session_id else None


sessions = Sessions()

# Tokens of logged-in customers: made for the gateway, issued to the web app.
user_verifier = TokenVerifier(config.GATEWAY_AUDIENCE, allowed_clients=["food-web"])


def current_user(request: Request) -> dict:
    """Who is calling, from the token BearerAuth already checked. With sign-in switched off
    (AUTH_ENABLED=false) everybody is one open 'guest'."""
    claims = getattr(request.state, "auth", None)
    if not claims:
        return {"id": None, "username": "guest", "name": "Guest", "roles": ["customer", "developer"]}
    username = claims.get("preferred_username", "")
    return {
        "id": claims["sub"],
        "username": username,
        "name": claims.get("name") or username,
        "roles": claims.get("realm_access", {}).get("roles", []),
    }


def session_for(request: Request, body: dict) -> tuple[str, Session]:
    """The conversation of the signed-in user. (Without sign-in, the browser's own session_id is used.)"""
    return sessions.get(current_user(request)["id"] or body.get("session_id"))


async def read_json(request: Request) -> dict:
    try:
        body = await request.json()
    except Exception:
        return {}
    return body if isinstance(body, dict) else {}


async def health(request: Request) -> JSONResponse:
    return JSONResponse({"ok": True})


async def me(request: Request) -> JSONResponse:
    return JSONResponse(current_user(request))


async def agents(request: Request) -> JSONResponse:
    return JSONResponse(await direct.list_agents())


async def run_stream(sid: str, session: Session, run):
    """Run one agent turn and stream its progress. `run(on_update)` returns the reply text."""
    queue: asyncio.Queue = asyncio.Queue()

    async def on_update(line: str, stage: dict | None = None) -> None:
        messages = a2ui.tracker(stage) if stage else None
        await queue.put(("update", {"text": line, "stage": stage, "a2ui": messages}))

    async def work() -> None:
        async with session.lock:
            try:
                text = await run(on_update)
                agent = session.agent
                await queue.put(("reply", {"text": text, "a2ui": a2ui.for_blocks(agent.blocks, agent.sync)}))
            except Exception as exc:
                await queue.put(("error", {"message": f"{type(exc).__name__}: {exc}"}))
            finally:
                await queue.put(None)

    task = asyncio.create_task(work())
    _background.add(task)
    task.add_done_callback(_background.discard)

    yield sse("session", {"session_id": sid})
    while True:
        try:
            item = await asyncio.wait_for(queue.get(), timeout=15)
        except asyncio.TimeoutError:
            yield ": keep-alive\n\n"  # stops proxies from closing a quiet stream
            continue
        if item is None:
            break
        yield sse(*item)
    yield sse("done", {})


async def chat(request: Request):
    body = await read_json(request)
    message = str(body.get("message") or "").strip()
    if not message:
        return JSONResponse({"error": "message is required"}, status_code=400)
    sid, session = session_for(request, body)

    async def run(on_update):
        return await session.agent.handle(message, on_update=on_update)

    return StreamingResponse(run_stream(sid, session, run), media_type="text/event-stream", headers=SSE_HEADERS)


async def chat_action(request: Request):
    """The customer clicked a button in an A2UI surface. The click arrives as an A2UI action."""
    body = await read_json(request)
    action = body.get("action") if isinstance(body.get("action"), dict) else {}
    context = action.get("context") if isinstance(action.get("context"), dict) else {}
    sid, session = session_for(request, body)

    if action.get("name") == "say" and context.get("text"):

        async def run(on_update):
            return await session.agent.handle(str(context["text"]), on_update=on_update)

    elif session.agent.is_action(str(action.get("name"))):

        async def run(on_update):
            return await session.agent.act(action["name"], context, on_update)

    else:
        return JSONResponse({"error": f"unknown action '{action.get('name')}'"}, status_code=400)

    return StreamingResponse(run_stream(sid, session, run), media_type="text/event-stream", headers=SSE_HEADERS)


async def chat_reset(request: Request) -> JSONResponse:
    body = await read_json(request)
    session = sessions.find(current_user(request)["id"] or body.get("session_id"))
    if session is not None:
        session.agent.reset()
    return JSONResponse({"ok": True})


def direct_a2ui(agent_id: str, event: str, data: dict) -> list[dict] | None:
    """Draw what we can recognise: a menu, or the live stages of an order."""
    body = data.get("data") or {}
    if event == "artifact" and data.get("name") in ("menu", "search"):
        items = body.get("menu") or body.get("items") or []
        return a2ui.menu(agent_id.replace("_", " ").title(), items)
    if event == "status" and "stage" in body:
        return a2ui.tracker(body)
    return None


async def agent_send(request: Request):
    if "developer" not in current_user(request)["roles"]:
        return JSONResponse({"error": "talking to agents directly needs the 'developer' role"}, status_code=403)
    agent_id = request.path_params["agent_id"]
    if agent_id not in direct.agent_registry():
        return JSONResponse({"error": f"unknown agent '{agent_id}'"}, status_code=404)
    body = await read_json(request)
    # Either plain words ("text") or a structured request ("payload", a JSON object).
    text, payload = str(body.get("text") or "").strip(), body.get("payload")
    if text:
        outgoing = text
    elif isinstance(payload, dict):
        outgoing = payload
    else:
        return JSONResponse({"error": "send either 'text' (plain words) or 'payload' (a JSON object)"}, status_code=400)

    async def stream():
        async for event, data in direct.stream_to_agent(agent_id, outgoing):
            data = {**data, "a2ui": direct_a2ui(agent_id, event, data)}
            yield sse(event, data)

    return StreamingResponse(stream(), media_type="text/event-stream", headers=SSE_HEADERS)


app = Starlette(
    routes=[
        Route("/api/health", health),
        Route("/api/me", me),
        Route("/api/agents", agents),
        Route("/api/chat", chat, methods=["POST"]),
        Route("/api/chat/action", chat_action, methods=["POST"]),
        Route("/api/chat/reset", chat_reset, methods=["POST"]),
        Route("/api/agents/{agent_id}/send", agent_send, methods=["POST"]),
    ],
    middleware=[
        # CORS first (outermost), so the browser's preflight needs no token and refusals carry CORS headers.
        Middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]),
        Middleware(BearerAuth, verifier=user_verifier, public_prefixes=("/api/health",)),
    ],
)
