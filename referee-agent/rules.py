"""Deterministic rule checks on a deal summary.

These checks do not need an LLM, so they run in code and are always applied.
Claude adds any violations it finds in the transcript on top of these.
"""

VALID_OUTCOMES = {"DEAL", "NO_DEAL", "MAX_ROUNDS_REACHED"}
TERM_FIELDS = ("price_per_kg", "quantity_kg", "delivery_days", "payment_terms_days")


def check_rules(deal_summary: dict) -> list[str]:
    """Returns a list of human-readable rule violations (empty when the deal is valid)."""
    violations = []
    outcome = deal_summary.get("outcome")
    terms = deal_summary.get("final_terms")

    if outcome not in VALID_OUTCOMES:
        violations.append(f"Unknown outcome {outcome!r}.")
    violations += check_round_limit(deal_summary)

    if outcome == "DEAL":
        violations += check_final_terms(deal_summary, terms)
    elif terms:
        violations.append(f"Outcome is {outcome} but final terms were reported.")
    return violations


def check_round_limit(deal_summary: dict) -> list[str]:
    rounds, max_rounds = deal_summary.get("rounds"), deal_summary.get("max_rounds")
    if rounds is not None and max_rounds is not None and int(rounds) > int(max_rounds):
        return [f"Negotiation used {int(rounds)} rounds, more than the limit of {int(max_rounds)}."]
    return []


def check_final_terms(deal_summary: dict, terms: dict | None) -> list[str]:
    """A DEAL needs complete, positive, internally consistent final terms."""
    if not isinstance(terms, dict):
        return ["Outcome is DEAL but no final terms were reported."]
    violations = []
    missing = [f for f in TERM_FIELDS if f not in terms]
    if missing:
        return [f"Final terms are missing: {', '.join(missing)}."]
    for name in TERM_FIELDS:
        if float(terms[name]) <= 0:
            violations.append(f"Final {name} must be positive, got {terms[name]}.")
    if terms.get("status") not in (None, "ACCEPT"):
        violations.append(f"Final terms have status {terms.get('status')}, expected ACCEPT.")
    total = deal_summary.get("total_price")
    expected = round(float(terms["price_per_kg"]) * float(terms["quantity_kg"]), 2)
    if total is not None and abs(float(total) - expected) > 0.01:
        violations.append(f"Total price {total} does not match price x quantity ({expected}).")
    return violations
