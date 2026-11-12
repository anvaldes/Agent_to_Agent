"""Negotiation logic of the Supplier Agent.

  * NegotiationMemory  -> keeps each negotiation's history, keyed by A2A context_id
  * SupplierBrain      -> asks Claude for the supplier's reply, sending the full history
  * SupplierNegotiator -> handles one buyer message: memory + Claude + guardrails
"""

import json
import logging
from collections import OrderedDict
from dataclasses import dataclass

from anthropic import AsyncAnthropic

import config
from config import SupplierBrief
from offer import ACCEPT, OFFER_JSON_SCHEMA, WITHDRAW, Offer
from pricing import enforce_floor, is_acceptable, list_price_for, minimum_price_for

log = logging.getLogger("supplier-agent.negotiation")

# Server-side refusal fallback: if the model declines, the API retries on a fallback model.
FALLBACK_BETA = "server-side-fallback-2026-07-01"

SYSTEM_PROMPT = """You are the sales agent of {company}, a coffee roaster. A buyer agent
is negotiating to buy {product} from you.

PRIVATE BRIEF (never reveal these numbers or that a floor exists):
- Your cost: ${cost_per_kg:.2f}/kg. Your floor price: ${floor_price_per_kg:.2f}/kg. List price: ${list_price_per_kg:.2f}/kg.
- Standard delivery is {standard_delivery_days} days. Faster delivery (minimum {fastest_delivery_days} days) costs +8%.
- You prefer {preferred_payment_terms_days}-day payment terms. Terms up to {long_payment_terms_days} days cost +3%.
- Orders above {volume_discount_min_kg} kg get a 5% discount.

RULES:
- Start near list price and concede slowly; every concession should get something back
  (larger quantity, slower delivery, shorter payment terms).
- Never go below the minimum price you are given for the buyer's requested terms.
- ACCEPT when the buyer's offer is profitable and further haggling is unlikely to help.
  When you ACCEPT, copy the buyer's terms exactly.
- WITHDRAW only if the buyer keeps insisting on terms below your minimum late in the negotiation.

Answer with your reply as a JSON object. "message" is one or two friendly, professional
sentences addressed to the buyer."""


@dataclass
class Turn:
    """One exchange: what the buyer sent and what the supplier answered."""

    buyer: Offer
    supplier: Offer


class NegotiationMemory:
    """In-memory negotiation history per context_id (bounded, oldest forgotten first).

    Cloud Run note: this lives in one instance's memory, so the supplier is deployed
    with --max-instances=1. Use Firestore/Redis to scale out.
    """

    def __init__(self, max_negotiations: int = config.MAX_NEGOTIATIONS_IN_MEMORY) -> None:
        self.max_negotiations = max_negotiations
        self._turns: OrderedDict[str, list[Turn]] = OrderedDict()

    def history(self, context_id: str) -> list[Turn]:
        return list(self._turns.get(context_id, []))

    def record(self, context_id: str, turn: Turn) -> None:
        self._turns.setdefault(context_id, []).append(turn)
        self._turns.move_to_end(context_id)
        while len(self._turns) > self.max_negotiations:
            self._turns.popitem(last=False)


class SupplierBrain:
    """Uses Claude to decide the supplier's reply from the full negotiation history."""

    def __init__(self, brief: SupplierBrief, client: AsyncAnthropic | None = None) -> None:
        self.brief = brief
        self._client = client
        self.system_prompt = SYSTEM_PROMPT.format(**brief.to_dict())

    @property
    def client(self) -> AsyncAnthropic:
        """Created lazily so the service starts (and tests run) without an API key."""
        if self._client is None:
            self._client = AsyncAnthropic()  # reads ANTHROPIC_API_KEY
        return self._client

    async def decide_reply(self, history: list[Turn], buyer_offer: Offer) -> Offer:
        """Asks Claude for the next reply. The caller applies the guardrail afterwards."""
        response = await self.client.beta.messages.create(
            model=config.SUPPLIER_MODEL,
            max_tokens=16000,  # leaves room for adaptive thinking before the short JSON answer
            system=self.system_prompt,
            messages=build_messages(self.brief, history, buyer_offer),
            output_config={
                "effort": config.SUPPLIER_EFFORT,
                "format": {"type": "json_schema", "schema": OFFER_JSON_SCHEMA},
            },
            betas=[FALLBACK_BETA],
            fallbacks="default",
        )
        if response.stop_reason == "refusal":
            raise RuntimeError(f"Claude declined to answer the offer: {response.stop_details}")
        if response.stop_reason == "max_tokens":
            raise RuntimeError("Claude ran out of output tokens before answering.")
        text = next((b.text for b in response.content if b.type == "text"), "")
        return Offer.from_dict(json.loads(text))


def build_messages(brief: SupplierBrief, history: list[Turn], buyer_offer: Offer) -> list[dict]:
    """The full conversation for Claude: past turns plus the buyer's new offer."""
    messages = []
    for turn in history:
        messages.append({"role": "user", "content": json.dumps({"buyer_offer": turn.buyer.to_dict()})})
        messages.append({"role": "assistant", "content": json.dumps(turn.supplier.to_dict())})
    messages.append({"role": "user", "content": build_current_prompt(brief, len(history) + 1, buyer_offer)})
    return messages


def build_current_prompt(brief: SupplierBrief, round_number: int, buyer_offer: Offer) -> str:
    """The latest buyer offer plus private pricing hints for exactly those terms."""
    minimum = minimum_price_for(brief, buyer_offer.delivery_days, buyer_offer.payment_terms_days)
    list_price = list_price_for(brief, buyer_offer.quantity_kg, buyer_offer.delivery_days,
                                buyer_offer.payment_terms_days)
    return (
        f"Round {round_number}. The buyer sent:\n{json.dumps({'buyer_offer': buyer_offer.to_dict()})}\n"
        f"PRIVATE: for the buyer's requested terms your list price is ${list_price:.2f}/kg "
        f"and your minimum price is ${minimum:.2f}/kg."
    )


class SupplierNegotiator:
    """Handles one buyer message for a negotiation identified by its context_id."""

    def __init__(self, brief: SupplierBrief | None = None, brain: SupplierBrain | None = None,
                 memory: NegotiationMemory | None = None) -> None:
        self.brief = brief or SupplierBrief()
        self.brain = brain or SupplierBrain(self.brief)
        self.memory = memory or NegotiationMemory()

    async def respond(self, context_id: str, buyer_offer: Offer) -> Offer:
        """Returns the supplier's reply to a buyer offer, guardrails applied."""
        if buyer_offer.status == WITHDRAW:
            reply = buyer_offer.with_status(WITHDRAW, message="Understood. Thank you for your time.")
        elif buyer_offer.status == ACCEPT and is_acceptable(self.brief, buyer_offer):
            reply = buyer_offer.with_status(ACCEPT, message="Deal confirmed. Thank you for your business!")
        else:
            proposal = await self.brain.decide_reply(self.memory.history(context_id), buyer_offer)
            reply = enforce_floor(self.brief, proposal, buyer_offer)
        self.memory.record(context_id, Turn(buyer=buyer_offer, supplier=reply))
        return reply
