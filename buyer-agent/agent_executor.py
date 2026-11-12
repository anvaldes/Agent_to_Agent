"""A2A server side: turns incoming A2A requests into negotiations.

The AgentExecutor is the bridge between the A2A protocol and our agent logic:
  * it receives the caller's message (RequestContext)
  * it publishes Task status updates while the negotiation runs (EventQueue)
  * it returns the deal summary, the transcript and (optionally) the referee's
    verdict as Task artifacts
"""

import logging

from a2a.helpers import get_data_parts, new_data_part, new_task_from_user_message, new_text_message, new_text_part
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.tasks import TaskUpdater
from a2a.types import Message, TaskState
from a2a.utils.errors import UnsupportedOperationError

import config
from negotiation import NegotiationResult, NegotiationSession, build_deal_summary
from referee_client import RefereeClient
from supplier_client import SupplierClient

log = logging.getLogger("buyer-agent.executor")


class BuyerAgentExecutor(AgentExecutor):
    def __init__(self, supplier: SupplierClient | None = None, referee: RefereeClient | None = None) -> None:
        self.supplier = supplier or SupplierClient()
        self.referee = referee or (RefereeClient(config.REFEREE_AGENT_URL) if config.REFEREE_AGENT_URL else None)

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        # 1. Get (or create) the A2A Task for this request
        task = context.current_task
        if not task:
            task = new_task_from_user_message(context.message)
            await event_queue.enqueue_event(task)
        updater = TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id)

        async def report_progress(text: str) -> None:
            log.info(text)
            await updater.update_status(
                state=TaskState.TASK_STATE_WORKING,
                message=new_text_message(text, context_id=task.context_id, task_id=task.id),
            )

        # 2. Build the buyer's brief (callers may override fields with a DataPart)
        brief = build_brief(context.message)
        await report_progress(
            f"Starting negotiation for {brief.quantity_kg} kg of {brief.product} on behalf of {brief.company}"
        )

        # 3. Negotiate with the Supplier Agent over A2A
        try:
            session = NegotiationSession(brief, self.supplier, context_id=task.context_id)
            result = await session.run(report_progress)
        except Exception as exc:
            log.exception("Negotiation failed")
            await updater.failed(
                new_text_message(f"Negotiation failed: {exc}", context_id=task.context_id, task_id=task.id)
            )
            return

        # 4. Return the results as A2A artifacts
        deal_summary = build_deal_summary(result, brief)
        await updater.add_artifact(
            parts=[new_data_part(deal_summary, media_type="application/json")],
            name="deal_summary",
        )
        await updater.add_artifact(
            parts=[new_text_part(result.transcript(), media_type="text/plain")],
            name="transcript",
        )

        # 5. Optionally ask the Referee Agent (over A2A) to score the deal
        if self.referee:
            await self._add_referee_verdict(updater, report_progress, deal_summary, result)

        await updater.complete()

    async def _add_referee_verdict(self, updater: TaskUpdater, report_progress, deal_summary: dict,
                                   result: NegotiationResult) -> None:
        """Sends the result to the referee and adds its verdict as a third artifact.

        A referee failure does not fail the negotiation: the deal already happened.
        """
        await report_progress("Sending the result to the Referee Agent over A2A")
        try:
            verdict = await self.referee.evaluate(deal_summary, result.transcript())
        except Exception as exc:
            log.exception("Referee evaluation failed")
            await report_progress(f"Referee unavailable, no verdict: {exc}")
            return
        await updater.add_artifact(
            parts=[new_data_part(verdict, media_type="application/json")],
            name="verdict",
        )

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        raise UnsupportedOperationError(message="Cancel is not supported.")


def build_brief(message: Message) -> config.BuyerBrief:
    """The default brief, with any fields overridden by DataParts in the caller's message."""
    brief = config.BuyerBrief()
    for data in get_data_parts(message.parts):
        if isinstance(data, dict):
            brief = brief.update_from(data)
    return brief
