"""referee-agent: code rule checks, verdict building and the A2A contract. Claude is stubbed."""

from starlette.testclient import TestClient

from conftest import FakeClaude, load_agent

referee = load_agent("referee-agent", "config", "rules", "judge", "agent_executor", "main")

DEAL = {
    "outcome": "DEAL", "rounds": 3, "max_rounds": 6, "requested_quantity_kg": 500,
    "final_terms": {"price_per_kg": 8.0, "quantity_kg": 500, "delivery_days": 20,
                    "payment_terms_days": 30, "status": "ACCEPT", "message": "Deal"},
    "total_price": 4000.0,
}


def test_valid_deal_has_no_violations():
    assert referee.rules.check_rules(DEAL) == []


def test_rule_violations_are_detected():
    bad = {**DEAL, "rounds": 9, "total_price": 1.0}
    violations = referee.rules.check_rules(bad)
    assert any("rounds" in v for v in violations)
    assert any("Total price" in v for v in violations)
    assert referee.rules.check_rules({"outcome": "DEAL"}) == ["Outcome is DEAL but no final terms were reported."]


async def test_verdict_combines_code_and_claude():
    fake = FakeClaude([{"rule_violations": [], "buyer_score": 12, "supplier_score": 4, "reasoning": "ok"}])
    verdict = await referee.judge.Referee(client=fake).evaluate({**DEAL, "total_price": 1.0}, "transcript")
    assert verdict["valid"] is False and verdict["rule_violations"]
    assert verdict["buyer_score"] == 10            # clamped to 1-10
    assert verdict["winner"] == "BUYER"
    assert "8.25" in fake.requests[0]["system"]    # market reference price in the prompt
    assert fake.requests[0]["output_config"]["format"]["type"] == "json_schema"


def test_tie():
    assert referee.judge.pick_winner(5, 5) == "TIE"


def test_agent_card():
    card = TestClient(referee.main.app).get("/.well-known/agent-card.json").json()
    assert card["name"] == "Referee Agent"
    assert card["skills"][0]["id"] == "evaluate_deal"
    assert "Claude" in card["description"]
