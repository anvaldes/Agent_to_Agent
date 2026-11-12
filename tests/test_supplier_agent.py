"""supplier-agent: floor-price guardrail, history per context_id and the A2A contract.
Claude is stubbed."""

import httpx
from starlette.testclient import TestClient

from a2a.client import ClientConfig, create_client
from a2a.helpers import get_data_parts, new_data_part, new_message, new_text_part
from a2a.types import Role, SendMessageRequest, TaskState

from conftest import FakeClaude, free_port, load_agent

supplier = load_agent("supplier-agent", "config", "offer", "pricing", "negotiation", "agent_executor", "main")
Offer = supplier.offer.Offer
BRIEF = supplier.config.SupplierBrief()


def offer(price, status="COUNTER_OFFER", quantity=500, delivery=20, payment=30):
    return Offer(price, quantity, delivery, payment, status, "msg")


def negotiator_with(answers):
    fake = FakeClaude(answers)
    brain = supplier.negotiation.SupplierBrain(BRIEF, client=fake)
    return supplier.negotiation.SupplierNegotiator(BRIEF, brain=brain), fake


# --- pricing guardrail -------------------------------------------------------------

def test_minimum_price_includes_surcharges():
    assert supplier.pricing.minimum_price_for(BRIEF, 20, 30) == 6.80
    assert supplier.pricing.minimum_price_for(BRIEF, 10, 30) == 7.34    # +8% rush
    assert supplier.pricing.minimum_price_for(BRIEF, 20, 60) == 7.00    # +3% long payment
    assert supplier.pricing.minimum_price_for(BRIEF, 10, 60) == 7.56


def test_volume_discount_never_goes_below_floor():
    assert supplier.pricing.list_price_for(BRIEF, 1000, 20, 30) == round(9.50 * 0.95, 2)
    assert supplier.pricing.list_price_for(BRIEF, 500, 20, 30) == 9.50


def test_counter_offer_below_floor_is_raised():
    reply = supplier.pricing.enforce_floor(BRIEF, offer(6.00), buyer_offer=offer(6.00))
    assert reply.price_per_kg == 6.80 and reply.status == "COUNTER_OFFER"


def test_accept_below_floor_becomes_counter_offer():
    buyer_offer = offer(6.50, delivery=10)
    reply = supplier.pricing.enforce_floor(BRIEF, offer(6.50, status="ACCEPT"), buyer_offer)
    assert reply.status == "COUNTER_OFFER"
    assert reply.price_per_kg == 7.34


def test_accept_copies_buyer_terms():
    buyer_offer = offer(8.00, delivery=25, payment=45)
    reply = supplier.pricing.enforce_floor(BRIEF, offer(1.0, status="ACCEPT"), buyer_offer)
    assert reply.status == "ACCEPT" and reply.same_terms_as(buyer_offer)


def test_impossible_delivery_is_clamped():
    reply = supplier.pricing.enforce_floor(BRIEF, offer(9.0, delivery=3, payment=90), offer(7.0))
    assert reply.delivery_days == 10 and reply.payment_terms_days == 60


# --- negotiation memory + Claude -----------------------------------------------------

async def test_full_history_sent_to_claude_per_context():
    negotiator, fake = negotiator_with([offer(9.2).to_dict(), offer(8.8).to_dict()])
    await negotiator.respond("ctx-1", offer(7.0))
    await negotiator.respond("ctx-2", offer(6.0))   # another negotiation must not leak in
    await negotiator.respond("ctx-1", offer(7.5))
    last = fake.requests[-1]
    assert [m["role"] for m in last["messages"]] == ["user", "assistant", "user"]
    assert '"price_per_kg": 7.0' in last["messages"][0]["content"]
    assert "minimum price is $6.80" in last["messages"][-1]["content"]
    assert last["model"] == "claude-opus-5-5"
    assert last["output_config"]["format"]["type"] == "json_schema"
    assert last["fallbacks"] == "default"
    assert len(negotiator.memory.history("ctx-1")) == 2


async def test_claude_below_floor_is_corrected():
    negotiator, _ = negotiator_with([offer(5.10).to_dict()])
    reply = await negotiator.respond("ctx", offer(5.0))
    assert reply.price_per_kg >= 6.80


async def test_buyer_accept_is_acknowledged_without_llm():
    negotiator, fake = negotiator_with([offer(9.0).to_dict()])
    reply = await negotiator.respond("ctx", offer(8.10, status="ACCEPT"))
    assert reply.status == "ACCEPT" and reply.price_per_kg == 8.10
    assert fake.requests == []


async def test_memory_is_bounded():
    memory = supplier.negotiation.NegotiationMemory(max_negotiations=2)
    for ctx in ("a", "b", "c"):
        memory.record(ctx, supplier.negotiation.Turn(offer(7.0), offer(9.0)))
    assert memory.history("a") == [] and len(memory.history("c")) == 1


# --- A2A contract ------------------------------------------------------------------------

def test_agent_card():
    card = TestClient(supplier.main.app).get("/.well-known/agent-card.json").json()
    assert card["name"] == "Supplier Agent - Andes Roasters"
    assert card["skills"][0]["id"] == "quote_and_negotiate"
    assert "Claude" in card["description"]


async def test_counter_offer_artifact_over_a2a(serve):
    port = free_port()
    url = f"http://127.0.0.1:{port}"
    negotiator, _ = negotiator_with([offer(9.10).to_dict()])
    card = supplier.main.build_agent_card()
    card.supported_interfaces[0].url = url
    from a2a.server.request_handlers import DefaultRequestHandler
    from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
    from a2a.server.tasks import InMemoryTaskStore
    from starlette.applications import Starlette
    handler = DefaultRequestHandler(agent_executor=supplier.agent_executor.SupplierAgentExecutor(negotiator),
                                    task_store=InMemoryTaskStore(), agent_card=card)
    serve(Starlette(routes=[*create_agent_card_routes(card), *create_jsonrpc_routes(handler, "/")]), port)

    message = new_message([new_text_part("Opening offer"), new_data_part({"offer": offer(7.0).to_dict()})],
                          context_id="ctx-a2a", role=Role.ROLE_USER)
    async with httpx.AsyncClient() as http:
        client = await create_client(url, client_config=ClientConfig(streaming=False, httpx_client=http))
        task = [e.task async for e in client.send_message(SendMessageRequest(message=message))][-1]
    assert task.status.state == TaskState.TASK_STATE_COMPLETED
    assert task.context_id == "ctx-a2a"
    artifact = task.artifacts[0]
    assert artifact.name == "counter_offer"
    reply = get_data_parts(artifact.parts)[0]["offer"]
    assert reply["price_per_kg"] == 9.10 and reply["status"] == "COUNTER_OFFER"
    assert artifact.parts[0].text
