"""A2A calls that watch a long-running task (streaming), and a lookup of a task by id."""

import asyncio
import uuid

import httpx

from a2a.client import ClientConfig, create_client
from a2a.helpers import get_text_parts, new_data_part
from a2a.types import GetTaskRequest, Message, Role, SendMessageRequest

from food_agents.common.a2a_client import _first_data, _state_name, auth_headers, failure


async def stream_agent(card, payload: dict, on_update=None, timeout: float = 300.0) -> dict:
    """Send one request and follow the task until it ends.

    on_update (optional, async) is called as on_update(state, data, text) for every status
    update the agent streams. Like call_agent, this never raises. It returns:
        {"state": final state or "unreachable", "data": data of the last status message,
         "text": its text, "task_id": ..., "artifact": data of the last artifact, or None}
    """
    message = Message(role=Role.ROLE_USER, message_id=str(uuid.uuid4()), parts=[new_data_part(payload)])
    outcome = {"state": "unknown", "data": None, "text": "", "task_id": None, "artifact": None}
    client = None
    http = None
    try:
        # the default timeout (5 s) is too short for a stream
        http = httpx.AsyncClient(headers=await auth_headers(), timeout=httpx.Timeout(60, connect=5))
        client = await create_client(card, client_config=ClientConfig(streaming=True, httpx_client=http))

        async def run() -> None:
            async for event in client.send_message(SendMessageRequest(message=message)):
                if event.HasField("task"):
                    outcome["task_id"] = event.task.id
                    outcome["state"] = _state_name(event.task.status.state)
                elif event.HasField("status_update"):
                    update = event.status_update
                    outcome["task_id"] = update.task_id
                    outcome["state"] = _state_name(update.status.state)
                    if update.status.HasField("message"):
                        parts = update.status.message.parts
                        outcome["data"] = _first_data(parts)
                        outcome["text"] = " ".join(get_text_parts(parts))
                    if on_update is not None:
                        await on_update(outcome["state"], outcome["data"], outcome["text"])
                elif event.HasField("artifact_update"):
                    outcome["artifact"] = _first_data(event.artifact_update.artifact.parts) or outcome["artifact"]

        await asyncio.wait_for(run(), timeout)
    except Exception as exc:
        return {**outcome, **failure(exc)}
    finally:
        if client is not None:
            await client.close()
        if http is not None:
            await http.aclose()
    return outcome


async def fetch_task(card, task_id: str) -> dict:
    """Ask an agent where a task stands right now (A2A's built-in 'get task'). Never raises."""
    client = None
    http = None
    try:
        http = httpx.AsyncClient(headers=await auth_headers(), timeout=httpx.Timeout(15, connect=5))
        client = await create_client(card, client_config=ClientConfig(streaming=False, httpx_client=http))
        task = await client.get_task(GetTaskRequest(id=task_id))
        data, text = None, ""
        if task.status.HasField("message"):
            data = _first_data(task.status.message.parts)
            text = " ".join(get_text_parts(task.status.message.parts))
        return {"state": _state_name(task.status.state), "data": data, "text": text}
    except Exception as exc:
        return failure(exc)
    finally:
        if client is not None:
            await client.close()
        if http is not None:
            await http.aclose()