"""Talk to any agent (restaurant, Swiggy, Zomato) from the browser, without the client agent.

The browser can't use the Python A2A SDK, so the gateway does the A2A call and turns the
answer into plain JSON events.
"""

import asyncio
import uuid

import httpx

from a2a.client import A2ACardResolver, ClientConfig, create_client
from a2a.helpers import get_data_parts, get_text_parts, new_data_part
from a2a.types import Message, Part, Role, SendMessageRequest, TaskState

from food_agents.common import config
from food_agents.common.auth import service_token


def agent_registry() -> dict[str, dict]:
    registry = {
        rid: {"id": rid, "kind": "restaurant", "url": config.restaurant_url(rid)} for rid in config.RESTAURANT_IDS
    }
    for pid, url in config.PLATFORM_URLS.items():
        registry[pid] = {"id": pid, "kind": "platform", "url": url}
    return registry


async def _describe(http: httpx.AsyncClient, agent: dict) -> dict:
    try:
        card = await A2ACardResolver(http, agent["url"]).get_agent_card()
    except Exception as exc:
        return {**agent, "up": False, "error": type(exc).__name__}
    return {
        **agent,
        "up": True,
        "name": card.name,
        "description": card.description,
        "skills": [
            {"id": s.id, "name": s.name, "description": s.description, "examples": list(s.examples)}
            for s in card.skills
        ],
    }


async def list_agents() -> list[dict]:
    """Every agent with its card summary, or up=false if it doesn't answer."""
    async with httpx.AsyncClient(timeout=3) as http:
        return list(await asyncio.gather(*[_describe(http, a) for a in agent_registry().values()]))


def _parts(parts) -> dict:
    data = get_data_parts(parts)
    return {"text": " ".join(get_text_parts(parts)), "data": data[0] if data else None}


async def stream_to_agent(agent_id: str, request: dict | str):
    """Send one request to an agent: a dict is sent as structured data, a string as plain text (the agent
    works out what is meant). Yields (event_name, json_data) pairs:
    task / status / artifact / message while it works, then 'done' (or 'error')."""
    agent = agent_registry()[agent_id]
    final_state = "unknown"
    client = None
    stream_http = None
    try:
        # The developer console speaks as the "dev-console" service. The gateway only lets users with the
        # developer role get here (see app.py). The default timeout (5 s) is too short for a stream.
        headers = {"Authorization": f"Bearer {await service_token('dev-console').get()}"} if config.AUTH_ENABLED else {}
        stream_http = httpx.AsyncClient(headers=headers, timeout=httpx.Timeout(60, connect=5))
        async with httpx.AsyncClient(timeout=5) as http:
            card = await A2ACardResolver(http, agent["url"]).get_agent_card()
        client = await create_client(card, client_config=ClientConfig(streaming=True, httpx_client=stream_http))
        part = new_data_part(request) if isinstance(request, dict) else Part(text=request)
        message = Message(role=Role.ROLE_USER, message_id=str(uuid.uuid4()), parts=[part])

        events = client.send_message(SendMessageRequest(message=message)).__aiter__()
        deadline = asyncio.get_running_loop().time() + 300  # give up on a task that never ends
        while True:
            try:
                event = await asyncio.wait_for(
                    events.__anext__(), max(0.1, deadline - asyncio.get_running_loop().time())
                )
            except StopAsyncIteration:
                break
            if event.HasField("task"):
                final_state = TaskState.Name(event.task.status.state).removeprefix("TASK_STATE_").lower()
                yield "task", {"task_id": event.task.id, "state": final_state}
            elif event.HasField("status_update"):
                status = event.status_update.status
                final_state = TaskState.Name(status.state).removeprefix("TASK_STATE_").lower()
                body = _parts(status.message.parts) if status.HasField("message") else {"text": "", "data": None}
                yield "status", {"state": final_state, **body}
            elif event.HasField("artifact_update"):
                artifact = event.artifact_update.artifact
                yield "artifact", {"name": artifact.name, **_parts(artifact.parts)}
            elif event.HasField("message"):
                yield "message", _parts(event.message.parts)
    except Exception as exc:
        yield "error", {"message": f"{type(exc).__name__}: {exc}"}
        return
    finally:
        if client is not None:
            await client.close()
        if stream_http is not None:
            await stream_http.aclose()
    yield "done", {"state": final_state}
