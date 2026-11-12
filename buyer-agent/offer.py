"""The offer contract shared by the Buyer and Supplier agents.

Every negotiation message sent over A2A carries two parts:
  * a TextPart  -> a short human-readable message
  * a DataPart  -> {"offer": {...}} with the structured fields below

Using a DataPart (instead of parsing free text) is what lets two agents built on
different LLMs agree on terms reliably.
"""

import json
import re
from dataclasses import asdict, dataclass, replace

# Allowed values for Offer.status
COUNTER_OFFER = "COUNTER_OFFER"
ACCEPT = "ACCEPT"
WITHDRAW = "WITHDRAW"
VALID_STATUSES = {COUNTER_OFFER, ACCEPT, WITHDRAW}

# JSON Schema of one offer, used to constrain the LLM's answer (structured outputs).
OFFER_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "price_per_kg": {"type": "number"},
        "quantity_kg": {"type": "integer"},
        "delivery_days": {"type": "integer"},
        "payment_terms_days": {"type": "integer"},
        "status": {"type": "string", "enum": [COUNTER_OFFER, ACCEPT, WITHDRAW]},
        "message": {"type": "string"},
    },
    "required": ["price_per_kg", "quantity_kg", "delivery_days", "payment_terms_days", "status", "message"],
    "additionalProperties": False,
}


@dataclass
class Offer:
    price_per_kg: float
    quantity_kg: int
    delivery_days: int
    payment_terms_days: int
    status: str = COUNTER_OFFER
    message: str = ""

    @property
    def total_price(self) -> float:
        return round(self.price_per_kg * self.quantity_kg, 2)

    @property
    def is_final(self) -> bool:
        return self.status in (ACCEPT, WITHDRAW)

    def to_dict(self) -> dict:
        return asdict(self)

    def with_status(self, status: str, message: str | None = None) -> "Offer":
        """Returns a copy with the same terms and a new status (and optionally a new message)."""
        return replace(self, status=status, message=self.message if message is None else message)

    def same_terms_as(self, other: "Offer") -> bool:
        """True when both offers have identical commercial terms (status and message ignored)."""
        return (
            round(self.price_per_kg, 2) == round(other.price_per_kg, 2)
            and self.quantity_kg == other.quantity_kg
            and self.delivery_days == other.delivery_days
            and self.payment_terms_days == other.payment_terms_days
        )

    @classmethod
    def from_dict(cls, data: dict) -> "Offer":
        status = str(data.get("status", COUNTER_OFFER)).upper()
        return cls(
            price_per_kg=float(data["price_per_kg"]),
            # DataParts store numbers as doubles, so integers come back as floats.
            quantity_kg=int(data["quantity_kg"]),
            delivery_days=int(data["delivery_days"]),
            payment_terms_days=int(data["payment_terms_days"]),
            status=status if status in VALID_STATUSES else COUNTER_OFFER,
            message=str(data.get("message", "")),
        )

    def summary(self) -> str:
        return (
            f"{self.status}: ${self.price_per_kg:.2f}/kg x {self.quantity_kg} kg "
            f"(total ${self.total_price:,.2f}), delivery {self.delivery_days} days, "
            f"payment {self.payment_terms_days} days"
        )


def extract_json_object(text: str) -> dict | None:
    """Finds the first JSON object in a block of text (used to read LLM output)."""
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
