"""Let an agent understand plain language, without touching the agent's own code.

A2A messages can carry text or data. Our agents work on data (a JSON request). PlainLanguage wraps
an agent's executor:

    - a data message goes straight through, exactly as before;
    - a text message ("show me the veg dishes under 250") is first turned into the data request the
      agent already understands, then handed to the same executor.

Turning text into a request is the job of one function per agent kind (`understand`), see
restaurant/language.py and platforms/language.py.
"""

import json

from a2a.helpers import get_text_parts, new_data_part
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.tasks import TaskUpdater
from a2a.types import Part, Task, TaskState, TaskStatus

from food_agents.common.a2a_helpers import get_request_data


class PlainLanguage(AgentExecutor):
    def __init__(self, inner: AgentExecutor, understand):
        """`understand(text)` is an async function returning (request, note):
        request = the data request for the inner executor, or None if the text can't be turned into one;
        note    = what to tell the person when request is None."""
        self.inner = inner
        self.understand = understand

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        message = context.message
        text = " ".join(get_text_parts(message.parts)).strip()

        # A structured request (or an empty message): nothing to translate.
        if get_request_data(message) is not None or not text:
            await self.inner.execute(context, event_queue)
            return

        updater = TaskUpdater(event_queue, context.task_id, context.context_id)

        # Announce the task ourselves, and tell the inner executor it has been announced.
        if context.current_task is None:
            task = Task(
                id=context.task_id,
                context_id=context.context_id,
                status=TaskStatus(state=TaskState.TASK_STATE_SUBMITTED),
                history=[message],
            )
            await event_queue.enqueue_event(task)
            context.current_task = task

        try:
            request, note = await self.understand(text)
        except Exception as exc:  # e.g. no GROQ_API_KEY, or the model call failed
            await updater.failed(updater.new_agent_message([Part(text=f"Could not read that request: {exc}")]))
            return

        if request is None:
            await updater.reject(updater.new_agent_message([Part(text=note)]))
            return

        # Show how the text was understood, then give the inner executor the request it knows.
        await updater.update_status(
            TaskState.TASK_STATE_WORKING,
            updater.new_agent_message([Part(text="Understood: " + json.dumps(request)), new_data_part(request)]),
        )
        message.parts.append(new_data_part(request))
        await self.inner.execute(context, event_queue)

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        await self.inner.cancel(context, event_queue)
