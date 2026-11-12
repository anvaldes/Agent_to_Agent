"""buyer-agent end to end over A2A: a client calls the buyer, the buyer negotiates with a
mock A2A supplier (and optionally a mock referee). Claude is stubbed with FakeClaude."""

import httpx
import pytest
from starlette.applications import Starlette
from starlette.routing import Route
from starlette.testclient import TestClient

from a2a.client import ClientConfig, create_client
from a2a.helpers import get_data_parts, get_text_parts, new_data_part, new_message, new_text_part
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import AgentInterface, Role, SendMessageRequest, TaskState

from conftest import FakeClaude, free_port, load_agent
from mock_agents import mock_referee, mock_supplier

buyer = load_agent("buyer-agent", "config", "negotiation", "agent_executor", "supplier_client",
                   "referee_client", "main")


def offer(price, status="COUNTER_OFFER", quantity=500, delivery=20, payment=30, message="..."):
    return {"price_per_kg": price, "quantity_kg": quantity, "delivery_days": delivery,
            "payment_terms_days": payment, "status": status, "message": message}


def build_buyer_app(url: str, supplier_url: str, referee_url: str | None) -> Starlette:
    """Same wiring as buyer-agent/main.py, pointed at the mock agents."""
    card = buyer.main.build_agent_card()
    card.supported_interfaces[0].CopyFrom(AgentInterface(protocol_binding="JSONRPC", url=url, protocol_version="1.0"))
    executor = buyer.agent_executor.BuyerAgentExecutor(
        supplier=buyer.supplier_client.SupplierClient(supplier_url),
        referee=buyer.referee_client.RefereeClient(referee_url) if referee_url else None,
    )
    handler = DefaultRequestHandler(agent_executor=executor, task_store=InMemoryTaskStore(), agent_card=card)
    return Starlette(routes=[*create_agent_card_routes(card), *create_jsonrpc_routes(handler, "/")])


async def call_buyer(url: str, overrides: dict | None = None):
    """Sends "Start the negotiation." to the buyer over A2A and returns the final Task."""
    parts = [new_text_part("Start the negotiation.")]
    if overrides:
        parts.append(new_data_part(overrides))
    async with httpx.AsyncClient(timeout=30) as http:
        client = await create_client(url, client_config=ClientConfig(streaming=False, httpx_client=http))
        task = None
        async for event in client.send_message(SendMessageRequest(message=new_message(parts, role=Role.ROLE_USER))):
            task = event.task
        await client.close()
    return task


def artifacts_by_name(task) -> dict:
    return {a.name: a for a in task.artifacts}


@pytest.fixture
def agents(serve, monkeypatch):
    """Starts buyer + mock supplier (+ mock referee). Returns a function to run one negotiation."""

    async def run(claude_answers, supplier_reply, verdict=None, overrides=None):
        fake = FakeClaude(claude_answers)
        monkeypatch.setattr(buyer.negotiation, "AsyncAnthropic", lambda: fake)
        supplier_port, buyer_port = free_port(), free_port()
        supplier_url, buyer_url = f"http://127.0.0.1:{supplier_port}", f"http://127.0.0.1:{buyer_port}"
        supplier_app, supplier = mock_supplier(supplier_url, supplier_reply)
        serve(supplier_app, supplier_port)
        referee, referee_url = None, None
        if verdict is not None:
            referee_port = free_port()
            referee_url = f"http://127.0.0.1:{referee_port}"
            referee_app, referee = mock_referee(referee_url, verdict)
            serve(referee_app, referee_port)
        serve(build_buyer_app(buyer_url, supplier_url, referee_url), buyer_port)
        task = await call_buyer(buyer_url, overrides)
        return task, supplier, referee, fake

    return run


async def test_deal(agents):
    task, supplier, _, fake = await agents(
        claude_answers=[offer(7.00), offer(7.80)],
        supplier_reply=lambda o, n: offer(8.60) if n == 1 else {**o, "status": "ACCEPT"},
    )
    assert task.status.state == TaskState.TASK_STATE_COMPLETED
    summary = get_data_parts(artifacts_by_name(task)["deal_summary"].parts)[0]
    assert summary["outcome"] == "DEAL"
    assert summary["final_terms"]["price_per_kg"] == 7.80
    assert summary["final_terms"]["status"] == "ACCEPT"
    assert summary["total_price"] == 3900.0
    assert summary["savings_vs_max_budget"] == 600.0
    # One negotiation = one context_id reused on every round
    assert len(supplier.received) == 2 and len(set(supplier.context_ids)) == 1
    # Ints survive the protobuf round trip as numbers the buyer casts back
    assert supplier.received[0]["offer"]["quantity_kg"] == 500
    transcript = get_text_parts(artifacts_by_name(task)["transcript"].parts)[0]
    assert "Outcome: DEAL" in transcript
    assert "verdict" not in artifacts_by_name(task)
    # The Claude request uses structured outputs and the refusal fallback
    request = fake.requests[0]
    assert request["output_config"]["format"]["type"] == "json_schema"
    assert request["fallbacks"] == "default"


async def test_buyer_accept_is_confirmed_by_supplier(agents):
    task, supplier, _, _ = await agents(
        claude_answers=[offer(7.00), offer(1.0, status="ACCEPT")],  # Claude ACCEPTs with wrong numbers
        supplier_reply=lambda o, n: offer(8.10, delivery=18) if n == 1 else {**o, "status": "ACCEPT"},
    )
    summary = get_data_parts(artifacts_by_name(task)["deal_summary"].parts)[0]
    assert summary["outcome"] == "DEAL"
    # The guardrail replaced Claude's numbers with the supplier's last terms
    assert supplier.received[1]["offer"]["price_per_kg"] == 8.10
    assert supplier.received[1]["offer"]["delivery_days"] == 18
    assert summary["final_terms"]["price_per_kg"] == 8.10


async def test_supplier_withdraws(agents):
    task, supplier, _, _ = await agents(
        claude_answers=[offer(5.00)],
        supplier_reply=lambda o, n: {**offer(9.50), "status": "WITHDRAW"},
    )
    summary = get_data_parts(artifacts_by_name(task)["deal_summary"].parts)[0]
    assert task.status.state == TaskState.TASK_STATE_COMPLETED
    assert summary["outcome"] == "NO_DEAL"
    assert "final_terms" not in summary
    assert len(supplier.received) == 1


async def test_max_rounds_reached(agents, monkeypatch):
    monkeypatch.setattr(buyer.config, "MAX_ROUNDS", 3)
    task, supplier, _, fake = await agents(
        claude_answers=[offer(6.50)],
        supplier_reply=lambda o, n: offer(9.40),
    )
    summary = get_data_parts(artifacts_by_name(task)["deal_summary"].parts)[0]
    assert summary["outcome"] == "MAX_ROUNDS_REACHED"
    assert summary["rounds"] == 3 and summary["max_rounds"] == 3
    assert len(supplier.received) == 3
    assert len(fake.requests) == 3


async def test_guardrail_never_exceeds_max_price(agents):
    task, supplier, _, _ = await agents(
        # Claude tries to overpay twice and then to accept a price above the maximum
        claude_answers=[offer(9.75), offer(12.00), offer(9.40, status="ACCEPT")],
        supplier_reply=lambda o, n: offer(9.40),
        overrides={"max_price_per_kg": 9.0},
    )
    prices = [r["offer"]["price_per_kg"] for r in supplier.received]
    statuses = [r["offer"]["status"] for r in supplier.received]
    assert prices and max(prices) <= 9.0
    assert "ACCEPT" not in statuses
    summary = get_data_parts(artifacts_by_name(task)["deal_summary"].parts)[0]
    assert summary["outcome"] == "MAX_ROUNDS_REACHED"


async def test_guardrail_uses_overridden_max_price(agents):
    _, supplier, _, _ = await agents(
        claude_answers=[offer(8.90)],
        supplier_reply=lambda o, n: {**offer(9.0), "status": "WITHDRAW"},
        overrides={"max_price_per_kg": 8.5, "quantity_kg": 800},
    )
    assert supplier.received[0]["offer"]["price_per_kg"] == 8.5


async def test_referee_verdict_is_third_artifact(agents):
    verdict = {"valid": True, "rule_violations": [], "buyer_score": 7, "supplier_score": 5,
               "winner": "BUYER", "reasoning": "Below market."}
    task, _, referee, _ = await agents(
        claude_answers=[offer(7.90)],
        supplier_reply=lambda o, n: {**o, "status": "ACCEPT"},
        verdict=verdict,
    )
    names = [a.name for a in task.artifacts]
    assert names == ["deal_summary", "transcript", "verdict"]
    assert get_data_parts(artifacts_by_name(task)["verdict"].parts)[0]["winner"] == "BUYER"
    sent = referee.received[0]
    assert sent["deal_summary"]["outcome"] == "DEAL"


def test_enforce_limits_unit():
    brain = buyer.negotiation.BuyerBrain(buyer.config.BuyerBrief(), client=object())
    Offer = buyer.negotiation.Offer
    capped = brain.enforce_limits(Offer(9.99, 500, 20, 60, "COUNTER_OFFER"))
    assert capped.price_per_kg == 9.0
    late = brain.enforce_limits(Offer(8.0, 500, 45, 60, "COUNTER_OFFER"))
    assert late.delivery_days == 30
    no_offer_yet = brain.enforce_limits(Offer(8.0, 500, 20, 60, "ACCEPT"))
    assert no_offer_yet.status == "COUNTER_OFFER"
    withdraw = brain.enforce_limits(Offer(99.0, 500, 20, 60, "WITHDRAW"))
    assert withdraw.status == "WITHDRAW"


def test_health_and_agent_card():
    client = TestClient(buyer.main.app)
    assert client.get("/health").json()["status"] == "ok"
    card = client.get("/.well-known/agent-card.json").json()
    assert card["name"] == "Buyer Agent - Central Coffee Co."
    assert card["skills"][0]["id"] == "negotiate_purchase"
