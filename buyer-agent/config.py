"""Configuration for the Buyer Agent.

Everything that can change between environments is read from environment variables.
The buyer's private negotiation brief lives here too: it is never sent to the supplier.
"""

import os
from dataclasses import asdict, dataclass, fields

# --- Service settings ---------------------------------------------------------
PORT = int(os.getenv("PORT", "8080"))

# Public URL of this agent, advertised in its Agent Card (A2A discovery).
# On Cloud Run: https://buyer-agent-<PROJECT_NUMBER>.<REGION>.run.app
# A2A clients send their requests to this URL, so it must be reachable by callers.
AGENT_URL = os.getenv("AGENT_URL", f"http://localhost:{PORT}")

# Base URL of the Supplier Agent. The buyer discovers it through its Agent Card.
SUPPLIER_AGENT_URL = os.getenv("SUPPLIER_AGENT_URL", "http://localhost:8081")

# Base URL of the Referee Agent. Optional: when empty, no verdict is requested.
REFEREE_AGENT_URL = os.getenv("REFEREE_AGENT_URL", "").strip()

# Set to "true" when the other agents are private Cloud Run services (IAM-protected).
USE_ID_TOKEN = os.getenv("USE_ID_TOKEN", "false").lower() == "true"

# --- LLM settings (Claude) ----------------------------------------------------
# ANTHROPIC_API_KEY is injected by Cloud Run from Secret Manager.
BUYER_MODEL = os.getenv("BUYER_MODEL", "claude-sonnet-5-5")
# Reasoning depth for each move: low | medium | high | xhigh | max.
BUYER_EFFORT = os.getenv("BUYER_EFFORT", "medium")

# --- Negotiation rules ---------------------------------------------------------
MAX_ROUNDS = int(os.getenv("MAX_ROUNDS", "6"))


@dataclass
class BuyerBrief:
    """Private constraints of the buyer. The supplier never sees these values."""

    company: str = "Central Coffee Co."
    product: str = "Specialty coffee beans (roasted)"
    quantity_kg: int = 500
    target_price_per_kg: float = 7.50
    max_price_per_kg: float = 9.00
    max_delivery_days: int = 30
    preferred_payment_terms_days: int = 60

    def update_from(self, overrides: dict) -> "BuyerBrief":
        """Returns a copy of the brief with any known fields overridden (e.g. from a request's data part).

        Values are cast to each field's type, because numbers in A2A DataParts arrive as floats.
        """
        values = asdict(self)
        for f in fields(self):
            if f.name in overrides and overrides[f.name] is not None:
                values[f.name] = _cast(f.type, overrides[f.name])
        return BuyerBrief(**values)

    def to_dict(self) -> dict:
        return asdict(self)


def _cast(type_name, value):
    """Casts a DataPart value to a dataclass field type ('int', 'float' or 'str')."""
    caster = {"int": int, "float": float, "str": str}.get(getattr(type_name, "__name__", str(type_name)), str)
    return caster(value)
