"""Buyer Agent - Agent2Agent (A2A) protocol server.

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
from agent_executor import BuyerAgentExecutor

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("buyer-agent")


def build_agent_card() -> AgentCard:
    """The Agent Card is how other agents discover this agent (A2A discovery)."""
    negotiate_skill = AgentSkill(
        id="negotiate_purchase",
        name="Negotiate purchase",
        description=(
            "Negotiates the purchase of coffee beans with a supplier agent over A2A, "
            "within a private budget, and returns the deal summary and transcript."
        ),
        input_modes=["text/plain", "application/json"],
        output_modes=["application/json", "text/plain"],
        tags=["procurement", "negotiation", "a2a"],
        examples=[
            "Start the negotiation.",
            '{"quantity_kg": 800, "max_price_per_kg": 8.5}',
        ],
    )
    return AgentCard(
        name="Buyer Agent - Central Coffee Co.",
        description=(
            "Procurement agent for Central Coffee Co., powered by Claude. "
            "Negotiates coffee bean purchases with supplier agents using the Agent2Agent protocol."
        ),
        version="1.0.0",
        default_input_modes=["text/plain", "application/json"],
        default_output_modes=["application/json", "text/plain"],
        capabilities=AgentCapabilities(streaming=True),
        supported_interfaces=[
            AgentInterface(protocol_binding="JSONRPC", url=config.AGENT_URL, protocol_version="1.0")
        ],
        skills=[negotiate_skill],
    )


async def health(request) -> JSONResponse:
    """Liveness check used by humans and load balancers (not part of A2A)."""
    return JSONResponse({
        "status": "ok",
        "agent": "buyer-agent",
        "supplier": config.SUPPLIER_AGENT_URL,
        "referee": config.REFEREE_AGENT_URL or None,
    })


def create_app() -> Starlette:
    """Builds the Starlette app: /health + Agent Card route + A2A JSON-RPC endpoint."""
    agent_card = build_agent_card()
    request_handler = DefaultRequestHandler(
        agent_executor=BuyerAgentExecutor(),
        task_store=InMemoryTaskStore(),
        agent_card=agent_card,
    )
    routes = [Route("/health", health, methods=["GET"])]
    routes.extend(create_agent_card_routes(agent_card))          # A2A discovery
    routes.extend(create_jsonrpc_routes(request_handler, "/"))   # A2A JSON-RPC endpoint
    log.info("Buyer Agent ready at %s (supplier: %s)", config.AGENT_URL, config.SUPPLIER_AGENT_URL)
    return Starlette(routes=routes)


app = create_app()

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=config.PORT)
