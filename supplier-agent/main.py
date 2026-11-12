"""Supplier Agent - Agent2Agent (A2A) protocol server.

Endpoints (A2A protocol 1.0, JSON-RPC binding):
  GET  /.well-known/agent-card.json  -> Agent Card: who this agent is and how to talk to it
  POST /                             -> JSON-RPC methods: SendMessage, SendStreamingMessage,
                                        GetTask, CancelTask, ...
Extra:
  GET  /health                       -> simple health check
"""

import logging

from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill

import config
from agent_executor import SupplierAgentExecutor

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("supplier-agent")


def build_agent_card() -> AgentCard:
    """The Agent Card is how other agents discover this agent (A2A discovery)."""
    quote_skill = AgentSkill(
        id="quote_and_negotiate",
        name="Quote and negotiate",
        description=(
            "Answers purchase offers for roasted specialty coffee beans with a counter-offer, "
            "an acceptance or a withdrawal. Send a DataPart {\"offer\": {...}} and reuse the "
            "same context_id for every round of one negotiation."
        ),
        input_modes=["application/json", "text/plain"],
        output_modes=["application/json", "text/plain"],
        tags=["sales", "negotiation", "coffee", "a2a"],
        examples=[
            '{"offer": {"price_per_kg": 7.0, "quantity_kg": 500, "delivery_days": 20, '
            '"payment_terms_days": 30, "status": "COUNTER_OFFER", "message": "Opening offer."}}',
        ],
    )
    return AgentCard(
        name="Supplier Agent - Andes Roasters",
        description=(
            "Sales agent for Andes Roasters, powered by Gemini on Vertex AI. "
            "Quotes and negotiates coffee bean orders with buyer agents using the Agent2Agent (A2A) protocol."
        ),
        version="1.0.0",
        default_input_modes=["application/json", "text/plain"],
        default_output_modes=["application/json", "text/plain"],
        capabilities=AgentCapabilities(streaming=True),
        supported_interfaces=[
            AgentInterface(protocol_binding="JSONRPC", url=config.AGENT_URL, protocol_version="1.0")
        ],
        skills=[quote_skill],
    )


async def health(request) -> JSONResponse:
    """Liveness check used by humans and load balancers (not part of A2A)."""
    return JSONResponse({"status": "ok", "agent": "supplier-agent", "model": config.SUPPLIER_MODEL})


def create_app() -> Starlette:
    """Builds the Starlette app: /health + Agent Card route + A2A JSON-RPC endpoint."""
    agent_card = build_agent_card()
    request_handler = DefaultRequestHandler(
        agent_executor=SupplierAgentExecutor(),
        task_store=InMemoryTaskStore(),
        agent_card=agent_card,
    )
    routes = [Route("/health", health, methods=["GET"])]
    routes.extend(create_agent_card_routes(agent_card))          # A2A discovery
    routes.extend(create_jsonrpc_routes(request_handler, "/"))   # A2A JSON-RPC endpoint
    log.info("Supplier Agent ready at %s (model: %s)", config.AGENT_URL, config.SUPPLIER_MODEL)
    return Starlette(routes=routes)


app = create_app()

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=config.PORT)
