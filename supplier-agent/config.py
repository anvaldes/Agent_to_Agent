"""Configuration for the Supplier Agent.

Everything that can change between environments is read from environment variables.
The supplier's private brief lives here too: it is never sent to the buyer.
"""

import os
from dataclasses import asdict, dataclass

# --- Service settings ---------------------------------------------------------
PORT = int(os.getenv("PORT", "8081"))

# Public URL of this agent, advertised in its Agent Card (A2A discovery).
# On Cloud Run: https://supplier-agent-<PROJECT_NUMBER>.<REGION>.run.app
# A2A clients send their requests to this URL, so it must be reachable by callers.
AGENT_URL = os.getenv("AGENT_URL", f"http://localhost:{PORT}")

# --- LLM settings (Claude) ----------------------------------------------------
# ANTHROPIC_API_KEY is injected by Cloud Run from Secret Manager.
SUPPLIER_MODEL = os.getenv("SUPPLIER_MODEL", "claude-opus-5-5")
# Reasoning depth for each reply: low | medium | high | xhigh | max.
SUPPLIER_EFFORT = os.getenv("SUPPLIER_EFFORT", "medium")

# How many negotiations (context_ids) to keep in memory before forgetting the oldest.
MAX_NEGOTIATIONS_IN_MEMORY = int(os.getenv("MAX_NEGOTIATIONS_IN_MEMORY", "200"))


@dataclass(frozen=True)
class SupplierBrief:
    """Private constraints of the supplier. The buyer never sees these values."""

    company: str = "Andes Roasters"
    product: str = "Specialty coffee beans (roasted)"
    cost_per_kg: float = 6.20
    floor_price_per_kg: float = 6.80
    list_price_per_kg: float = 9.50
    standard_delivery_days: int = 20
    fastest_delivery_days: int = 10
    rush_delivery_surcharge: float = 0.08     # delivery faster than standard costs +8%
    preferred_payment_terms_days: int = 30
    long_payment_terms_days: int = 60
    long_payment_surcharge: float = 0.03      # payment terms beyond 30 days (up to 60) cost +3%
    volume_discount_min_kg: int = 800
    volume_discount: float = 0.05             # 5% off for orders above 800 kg

    def to_dict(self) -> dict:
        return asdict(self)
