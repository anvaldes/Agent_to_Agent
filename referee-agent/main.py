"""Referee Agent - Agent2Agent (A2A) protocol server.

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
from agent_executor import RefereeAgentExecutor

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("referee-agent")


def build_agent_card() -> AgentCard:
    """The Agent Card is how other agents discover this agent (A2A discovery)."""
    evaluate_skill = AgentSkill(
        id="evaluate_deal",
        name="Evaluate deal",
        description=(
            "Checks a finished negotiation for rule violations and scores both sides against a "
            "public market reference price. Send a DataPart {\"deal_summary\": {...}} plus a "
            "TextPart with the transcript; the answer is a \"verdict\" artifact."
        ),
        input_modes=["application/json", "text/plain"],
        output_modes=["application/json"],
        tags=["referee", "evaluation", "negotiation", "a2a"],
        examples=['{"deal_summary": {"outcome": "DEAL", "final_terms": {"price_per_kg": 8.1, ...}}}'],
    )
    return AgentCard(
        name="Referee Agent",
        description=(
            "Neutral referee powered by Claude. Validates and scores B2B negotiation results "
            "received from other agents using the Agent2Agent (A2A) protocol."
        ),
        version="1.0.0",
        default_input_modes=["application/json", "text/plain"],
        default_output_modes=["application/json"],
        capabilities=AgentCapabilities(streaming=True),
        supported_interfaces=[
            AgentInterface(protocol_binding="JSONRPC", url=config.AGENT_URL, protocol_version="1.0")
        ],
        skills=[evaluate_skill],
    )


async def health(request) -> JSONResponse:
    """Liveness check used by humans and load balancers (not part of A2A)."""
    return JSONResponse({"status": "ok", "agent": "referee-agent", "model": config.REFEREE_MODEL,
                         "market_price_per_kg": config.MARKET_PRICE_PER_KG})


def create_app() -> Starlette:
    """Builds the Starlette app: /health + Agent Card route + A2A JSON-RPC endpoint."""
    agent_card = build_agent_card()
    request_handler = DefaultRequestHandler(
        agent_executor=RefereeAgentExecutor(),
        task_store=InMemoryTaskStore(),
        agent_card=agent_card,
    )
    routes = [Route("/health", health, methods=["GET"])]
    routes.extend(create_agent_card_routes(agent_card))          # A2A discovery
    routes.extend(create_jsonrpc_routes(request_handler, "/"))   # A2A JSON-RPC endpoint
    log.info("Referee Agent ready at %s (model: %s)", config.AGENT_URL, config.REFEREE_MODEL)
    return Starlette(routes=routes)


app = create_app()

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=config.PORT)
