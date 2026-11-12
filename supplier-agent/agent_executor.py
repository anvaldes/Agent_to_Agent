"""A2A server side: turns each buyer message into a supplier reply.

Every buyer message (one negotiation round) becomes one A2A Task. All rounds of
one negotiation share the same context_id, which is how the supplier finds the
history. The reply is a Task artifact named "counter_offer" with a TextPart +
DataPart in the shared offer contract. A buyer ACCEPT is acknowledged and the
Task is closed (completed) like any other round.
"""

import logging

from a2a.helpers import get_data_parts, get_text_parts, new_data_part, new_task_from_user_message, new_text_message, new_text_part
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.tasks import TaskUpdater
from a2a.types import Message, TaskState
from a2a.utils.errors import UnsupportedOperationError

from negotiation import SupplierNegotiator
from offer import Offer, extract_json_object

log = logging.getLogger("supplier-agent.executor")

COUNTER_OFFER_ARTIFACT = "counter_offer"


class SupplierAgentExecutor(AgentExecutor):
    def __init__(self, negotiator: SupplierNegotiator | None = None) -> None:
        self.negotiator = negotiator or SupplierNegotiator()

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        # 1. Get (or create) the A2A Task for this request
        task = context.current_task
        if not task:
            task = new_task_from_user_message(context.message)
            await event_queue.enqueue_event(task)
        updater = TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id)

        # 2. Read the buyer's offer from the DataPart
        buyer_offer = read_offer(context.message)
        if buyer_offer is None:
            await updater.reject(new_text_message(
                'Expected a DataPart {"offer": {...}} with price_per_kg, quantity_kg, '
                "delivery_days and payment_terms_days.",
                context_id=task.context_id, task_id=task.id,
            ))
            return
        log.info("[%s] Buyer: %s", task.context_id, buyer_offer.summary())
        await updater.update_status(
            state=TaskState.TASK_STATE_WORKING,
            message=new_text_message("Reviewing the offer", context_id=task.context_id, task_id=task.id),
        )

        # 3. Decide the reply (Claude + guardrails)
        try:
            reply = await self.negotiator.respond(task.context_id, buyer_offer)
        except Exception as exc:
            log.exception("Could not answer the offer")
            await updater.failed(
                new_text_message(f"Supplier error: {exc}", context_id=task.context_id, task_id=task.id)
            )
            return
        log.info("[%s] Supplier: %s", task.context_id, reply.summary())

        # 4. Return the reply as the "counter_offer" artifact and close the Task
        await updater.add_artifact(
            parts=[
                new_text_part(reply.message or reply.summary(), media_type="text/plain"),
                new_data_part({"offer": reply.to_dict()}, media_type="application/json"),
            ],
            name=COUNTER_OFFER_ARTIFACT,
        )
        await updater.complete()

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        raise UnsupportedOperationError(message="Cancel is not supported.")


def read_offer(message: Message) -> Offer | None:
    """Reads {"offer": {...}} from a DataPart, falling back to JSON inside the text."""
    candidates = [d for d in get_data_parts(message.parts) if isinstance(d, dict)]
    fallback = extract_json_object("\n".join(get_text_parts(message.parts)))
    if fallback:
        candidates.append(fallback)
    for data in candidates:
        try:
            return Offer.from_dict(data.get("offer", data))
        except (KeyError, TypeError, ValueError):
            continue
    return None
