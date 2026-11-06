import asyncio

from a2a.helpers import new_data_part
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.tasks import TaskUpdater
from a2a.types import Part, Task, TaskState, TaskStatus

from food_agents.common.a2a_helpers import get_request_data
from food_agents.common.messages import describe_issues
from food_agents.restaurant import tools

HELP = (
    "Send a structured (data) request with one of these actions: "
    "get_info, check_menu, search_menu, quote_items, accept_order. "
    'Example: {"action": "quote_items", "items": [{"name": "Raita", "qty": 2}]}. '
    "Free-text requests are not supported yet."
)


class RestaurantExecutor(AgentExecutor):
    def __init__(self, restaurant_id: str):
        self.restaurant_id = restaurant_id

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        updater = TaskUpdater(event_queue, context.task_id, context.context_id)

        # A brand-new task must be announced first, with state "submitted".
        if context.current_task is None:
            await event_queue.enqueue_event(
                Task(
                    id=context.task_id,
                    context_id=context.context_id,
                    status=TaskStatus(state=TaskState.TASK_STATE_SUBMITTED),
                    history=[context.message],
                )
            )

        request = get_request_data(context.message)
        if request is None:
            await updater.reject(updater.new_agent_message([Part(text=HELP)]))
            return

        try:
            await self._handle(request, updater)
        except Exception as exc:  # e.g. MongoDB is down: fail the task, don't hang it
            await updater.failed(updater.new_agent_message([Part(text=f"Restaurant error: {exc}")]))

    async def _handle(self, request: dict, updater: TaskUpdater) -> None:
        rid = self.restaurant_id
        action = request.get("action")
        await updater.start_work()

        # asyncio.to_thread runs the (blocking) MongoDB call off the event loop,
        # so one slow query can't freeze every other request.
        if action == "get_info":
            info = await asyncio.to_thread(tools.get_info, rid)
            await updater.add_artifact([new_data_part(info)], name="info")
            await updater.complete()

        elif action == "check_menu":
            menu = await asyncio.to_thread(tools.check_menu, rid, bool(request.get("only_available", False)))
            await updater.add_artifact([new_data_part({"restaurant_id": rid, "menu": menu})], name="menu")
            await updater.complete()

        elif action == "search_menu":
            found = await asyncio.to_thread(
                tools.search_menu, rid, request.get("is_veg"), request.get("max_price"), request.get("tag")
            )
            await updater.add_artifact([new_data_part({"restaurant_id": rid, "items": found})], name="search")
            await updater.complete()

        elif action == "quote_items":
            result = await asyncio.to_thread(tools.quote_items, rid, request.get("items", []))
            if result["status"] == "ok":
                await updater.add_artifact([new_data_part(result)], name="quote")
                await updater.complete()
            elif result["status"] == "input_required":
                text = "I need your input: " + describe_issues(result["issues"])
                await updater.requires_input(updater.new_agent_message([Part(text=text), new_data_part(result)]))
            else:  # rejected: unknown or offline restaurant
                await updater.reject(updater.new_agent_message([Part(text=result["reason"])]))

        elif action == "accept_order":
            order_id, items = request.get("order_id"), request.get("items")
            if not order_id or not items:
                await updater.reject(updater.new_agent_message([Part(text="accept_order needs 'order_id' and 'items'.")]))
                return
            result = await asyncio.to_thread(tools.accept_order, rid, order_id, items)
            if result["status"] == "accepted":
                await updater.add_artifact([new_data_part(result)], name="acceptance")
                await updater.complete()
            else:
                await updater.reject(updater.new_agent_message([Part(text=result["reason"])]))

        else:
            await updater.reject(updater.new_agent_message([Part(text=f"Unknown action '{action}'. " + HELP)]))

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        await TaskUpdater(event_queue, context.task_id, context.context_id).cancel()