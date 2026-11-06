import asyncio
import uuid

import httpx

from a2a.client import ClientConfig, create_client
from a2a.helpers import get_data_parts, get_text_parts, new_data_part
from a2a.types import Message, Role, SendMessageRequest, TaskState

from food_agents.common import config
from food_agents.common.auth import service_token

# Who this process is, when it calls other agents. Each service sets it once at startup:
# the gateway / client agent is "client-agent", Swiggy is "platform-swiggy", and so on.
_identity: str | None = None


def use_identity(client_id: str) -> None:
    """Every agent call made by this process will carry a Keycloak token for this service."""
    global _identity
    _identity = client_id


async def auth_headers() -> dict:
    """The Authorization header for an outgoing agent call ({} when sign-in is switched off)."""
    if not config.AUTH_ENABLED or _identity is None:
        return {}
    return {"Authorization": f"Bearer {await service_token(_identity).get()}"}


def failure(exc: Exception) -> dict:
    """The outcome for a call that could not be made. A refused token (401/403) is reported as
    'unauthorized', so it isn't mistaken for an agent that is down."""
    text = f"{type(exc).__name__}: {exc}"
    refused = any(code in text for code in ("401", "403")) or "nauthorized" in text or "orbidden" in text
    return {"state": "unauthorized" if refused else "unreachable", "data": None, "text": text}


def _state_name(state) -> str:
    """TASK_STATE_INPUT_REQUIRED -> 'input_required'"""
    return TaskState.Name(state).removeprefix("TASK_STATE_").lower()


def _first_data(parts):
    found = get_data_parts(parts)
    return found[0] if found else None


async def call_agent(card, payload: dict, timeout: float = 15.0) -> dict:
    """Send one structured request to another agent and wait for its final answer.

    Always returns a dict and never raises:
        {"state": "completed" | "input_required" | "rejected" | "failed" | "unauthorized" | "unreachable" | ...,
         "data":  the structured result (artifact first, else the status message), or None,
         "text":  the human-readable text of the status message, or ""}
    """
    message = Message(
        role=Role.ROLE_USER,
        message_id=str(uuid.uuid4()),
        parts=[new_data_part(payload)],
    )
    outcome = {"state": "unknown", "data": None, "text": ""}
    client = None
    http = None
    try:
        http = httpx.AsyncClient(headers=await auth_headers(), timeout=httpx.Timeout(timeout, connect=5))
        client = await create_client(card, client_config=ClientConfig(streaming=False, httpx_client=http))

        async def run() -> None:
            async for event in client.send_message(SendMessageRequest(message=message)):
                if event.HasField("task"):
                    task = event.task
                    outcome["state"] = _state_name(task.status.state)
                    for artifact in task.artifacts:
                        outcome["data"] = outcome["data"] or _first_data(artifact.parts)
                    if task.status.HasField("message"):
                        parts = task.status.message.parts
                        outcome["data"] = outcome["data"] or _first_data(parts)
                        outcome["text"] = " ".join(get_text_parts(parts))
                elif event.HasField("message"):
                    outcome["state"] = "message"
                    outcome["data"] = _first_data(event.message.parts)
                    outcome["text"] = " ".join(get_text_parts(event.message.parts))

        await asyncio.wait_for(run(), timeout)
    except Exception as exc:  # network down, timeout, token refused, bad reply...
        return failure(exc)
    finally:
        if client is not None:
            await client.close()
        if http is not None:
            await http.aclose()
    return outcome