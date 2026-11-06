"""Talk to ANY A2A agent from the terminal.

    python -m scripts.a2a_cli card http://localhost:9101
    python -m scripts.a2a_cli send http://localhost:9101 '{"action": "check_menu"}'
    python -m scripts.a2a_cli send http://localhost:9101 'veg dishes under 250' --as dev-console      # plain words
    python -m scripts.a2a_cli send http://localhost:9101 '{"action": "check_menu"}' --no-stream
    python -m scripts.a2a_cli task http://localhost:9001 <task_id>     # where is that task right now?

The agents want a Keycloak token (the card is public, everything else is not). Say who you are with --as:
    python -m scripts.a2a_cli send http://localhost:9002 '{"action": "list_restaurants"}' --as client-agent
    python -m scripts.a2a_cli send http://localhost:9101 '{"action": "check_menu"}' --as platform-zomato
(who may call whom: client-agent -> Swiggy / Zomato,  platform-swiggy / platform-zomato -> restaurants)
"""

import argparse
import asyncio
import json
import uuid

import httpx
from google.protobuf.json_format import MessageToDict

from a2a.client import A2ACardResolver, ClientConfig, create_client
from a2a.helpers import get_data_parts, get_text_parts, new_data_part
from a2a.types import GetTaskRequest, Message, Part, Role, SendMessageRequest, TaskState

from food_agents.common.auth import service_token


def show_parts(prefix: str, parts) -> None:
    for text in get_text_parts(parts):
        print(f"{prefix} text: {text}")
    for data in get_data_parts(parts):
        print(f"{prefix} data: {json.dumps(data, indent=2, ensure_ascii=False)}")


async def fetch_card(url: str):
    async with httpx.AsyncClient() as http:
        return await A2ACardResolver(http, url).get_agent_card()


async def open_client(card, streaming: bool, identity: str | None):
    """An A2A client for this card. With `identity`, every request carries that service's Keycloak token."""
    headers = {}
    if identity:
        headers["Authorization"] = f"Bearer {await service_token(identity).get()}"
    http = httpx.AsyncClient(headers=headers, timeout=httpx.Timeout(60, connect=5))
    return await create_client(card, client_config=ClientConfig(streaming=streaming, httpx_client=http)), http


async def cmd_card(url: str) -> None:
    card = await fetch_card(url)
    print(json.dumps(MessageToDict(card), indent=2, ensure_ascii=False))


async def cmd_task(url: str, task_id: str, identity: str | None) -> None:
    card = await fetch_card(url)
    client, http = await open_client(card, False, identity)
    try:
        task = await client.get_task(GetTaskRequest(id=task_id))
        print(f"[task] {TaskState.Name(task.status.state)}")
        if task.status.HasField("message"):
            show_parts("  status", task.status.message.parts)
        for artifact in task.artifacts:
            show_parts(f"  artifact[{artifact.name}]", artifact.parts)
    finally:
        await client.close()
        await http.aclose()


async def cmd_send(url: str, payload: str, streaming: bool, identity: str | None) -> None:
    card = await fetch_card(url)
    print(f"-> {card.name}")

    # JSON that is an object goes as structured data; anything else is sent as plain words.
    try:
        request = json.loads(payload)
    except ValueError:
        request = None
    part = new_data_part(request) if isinstance(request, dict) else Part(text=payload)
    message = Message(role=Role.ROLE_USER, message_id=str(uuid.uuid4()), parts=[part])

    client, http = await open_client(card, streaming, identity)
    try:
        async for event in client.send_message(SendMessageRequest(message=message)):
            if event.HasField("task"):
                task = event.task
                print(f"[task] {TaskState.Name(task.status.state)}")
                if task.status.HasField("message"):
                    show_parts("  status", task.status.message.parts)
                for artifact in task.artifacts:
                    show_parts(f"  artifact[{artifact.name}]", artifact.parts)
            elif event.HasField("status_update"):
                status = event.status_update.status
                print(f"[status] {TaskState.Name(status.state)}")
                if status.HasField("message"):
                    show_parts("  ", status.message.parts)
            elif event.HasField("artifact_update"):
                artifact = event.artifact_update.artifact
                show_parts(f"[artifact:{artifact.name}]", artifact.parts)
            elif event.HasField("message"):
                show_parts("[message]", event.message.parts)
    finally:
        await client.close()
        await http.aclose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Minimal A2A client")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_card = sub.add_parser("card", help="fetch and print an agent's card")
    p_card.add_argument("url")

    p_send = sub.add_parser("send", help="send a request: JSON, or plain words")
    p_send.add_argument("url")
    p_send.add_argument("payload", help="JSON like '{\"action\": \"check_menu\"}', or plain words like 'show me the menu'")
    p_send.add_argument("--no-stream", action="store_true")
    p_send.add_argument("--as", dest="identity", help="who is calling: client-agent, platform-swiggy, platform-zomato or dev-console")

    p_task = sub.add_parser("task", help="look up a task by id (A2A get-task)")
    p_task.add_argument("url")
    p_task.add_argument("task_id")
    p_task.add_argument("--as", dest="identity", help="who is calling: client-agent, platform-swiggy or platform-zomato")

    args = parser.parse_args()
    if args.cmd == "card":
        asyncio.run(cmd_card(args.url))
    elif args.cmd == "task":
        asyncio.run(cmd_task(args.url, args.task_id, args.identity))
    else:
        asyncio.run(cmd_send(args.url, args.payload, streaming=not args.no_stream, identity=args.identity))


if __name__ == "__main__":
    main()
