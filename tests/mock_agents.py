"""Mock A2A agents (built with the real a2a-sdk server) used to test buyer-agent.

They speak real A2A over HTTP, so the buyer's discovery, messaging and parsing
code runs exactly as it does against the deployed services.
"""

from collections.abc import Callable

from starlette.applications import Starlette

from a2a.helpers import get_data_parts, new_data_part, new_task_from_user_message, new_text_part
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill

# reply_fn(received_offer_dict, round_number) -> reply offer dict
ReplyFn = Callable[[dict, int], dict]


class RecordingExecutor(AgentExecutor):
    """Answers each message with one artifact built by `respond`, and records what it received."""

    def __init__(self, artifact_name: str, respond: Callable[[dict], dict]) -> None:
        self.artifact_name = artifact_name
        self.respond = respond
        self.received: list[dict] = []
        self.context_ids: list[str] = []

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task or new_task_from_user_message(context.message)
        if not context.current_task:
            await event_queue.enqueue_event(task)
        data = next(d for d in get_data_parts(context.message.parts) if isinstance(d, dict))
        self.received.append(data)
        self.context_ids.append(task.context_id)
        updater = TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id)
        reply = self.respond(data)
        await updater.add_artifact(
            parts=[new_text_part(reply.get("message", "ok")), new_data_part(reply)],
            name=self.artifact_name,
        )
        await updater.complete()

    async def cancel(self, context, event_queue) -> None:
        raise NotImplementedError


def build_mock_app(name: str, skill_id: str, url: str, executor: AgentExecutor) -> Starlette:
    card = AgentCard(
        name=name,
        description=f"Mock {name} for tests",
        version="0.0.1",
        default_input_modes=["application/json", "text/plain"],
        default_output_modes=["application/json", "text/plain"],
        capabilities=AgentCapabilities(streaming=True),
        supported_interfaces=[AgentInterface(protocol_binding="JSONRPC", url=url, protocol_version="1.0")],
        skills=[AgentSkill(id=skill_id, name=skill_id, description="mock", tags=["mock"])],
    )
    handler = DefaultRequestHandler(agent_executor=executor, task_store=InMemoryTaskStore(), agent_card=card)
    return Starlette(routes=[*create_agent_card_routes(card), *create_jsonrpc_routes(handler, "/")])


def mock_supplier(url: str, reply_fn: ReplyFn) -> tuple[Starlette, RecordingExecutor]:
    """A supplier that answers each buyer offer with {"offer": reply_fn(offer, round)}."""
    executor = RecordingExecutor("counter_offer", lambda data: {})
    executor.respond = lambda data: {"offer": reply_fn(data["offer"], len(executor.received))}
    return build_mock_app("Mock Supplier", "quote_and_negotiate", url, executor), executor


def mock_referee(url: str, verdict: dict) -> tuple[Starlette, RecordingExecutor]:
    """A referee that always answers with the same verdict."""
    executor = RecordingExecutor("verdict", lambda data: verdict)
    return build_mock_app("Mock Referee", "evaluate_deal", url, executor), executor
