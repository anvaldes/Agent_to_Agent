"""A2A server side: turns a finished negotiation into a verdict.

Input : a DataPart {"deal_summary": {...}} (the buyer's deal summary) + a TextPart transcript
Output: a Task artifact named "verdict" with a DataPart holding the verdict
"""

import logging

from a2a.helpers import get_data_parts, get_text_parts, new_data_part, new_task_from_user_message, new_text_message
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.tasks import TaskUpdater
from a2a.types import Message, TaskState
from a2a.utils.errors import UnsupportedOperationError

from judge import Referee

log = logging.getLogger("referee-agent.executor")

VERDICT_ARTIFACT = "verdict"


class RefereeAgentExecutor(AgentExecutor):
    def __init__(self, referee: Referee | None = None) -> None:
        self.referee = referee or Referee()

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        # 1. Get (or create) the A2A Task for this request
        task = context.current_task
        if not task:
            task = new_task_from_user_message(context.message)
            await event_queue.enqueue_event(task)
        updater = TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id)

        # 2. Read the deal summary (DataPart) and the transcript (TextPart)
        deal_summary = read_deal_summary(context.message)
        if deal_summary is None:
            await updater.reject(new_text_message(
                'Expected a DataPart {"deal_summary": {...}} and a TextPart with the transcript.',
                context_id=task.context_id, task_id=task.id,
            ))
            return
        transcript = "\n".join(get_text_parts(context.message.parts))
        await updater.update_status(
            state=TaskState.TASK_STATE_WORKING,
            message=new_text_message("Evaluating the deal", context_id=task.context_id, task_id=task.id),
        )

        # 3. Judge the deal (code rules + Claude)
        try:
            verdict = await self.referee.evaluate(deal_summary, transcript)
        except Exception as exc:
            log.exception("Evaluation failed")
            await updater.failed(
                new_text_message(f"Referee error: {exc}", context_id=task.context_id, task_id=task.id)
            )
            return
        log.info("Verdict: winner=%s valid=%s", verdict["winner"], verdict["valid"])

        # 4. Return the verdict as an artifact and close the Task
        await updater.add_artifact(
            parts=[new_data_part(verdict, media_type="application/json")],
            name=VERDICT_ARTIFACT,
        )
        await updater.complete()

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        raise UnsupportedOperationError(message="Cancel is not supported.")


def read_deal_summary(message: Message) -> dict | None:
    """Accepts {"deal_summary": {...}} or a bare deal summary with an "outcome" field."""
    for data in get_data_parts(message.parts):
        if isinstance(data, dict):
            if isinstance(data.get("deal_summary"), dict):
                return data["deal_summary"]
            if "outcome" in data:
                return data
    return None
