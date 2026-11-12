"""Negotiation logic of the Buyer Agent.

  * BuyerBrain         -> asks Claude for the buyer's next move (private reasoning)
  * NegotiationSession -> runs the rounds: buyer offer -> A2A -> supplier counter-offer
"""

import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from anthropic import AsyncAnthropic

import config
from offer import ACCEPT, COUNTER_OFFER, OFFER_JSON_SCHEMA, WITHDRAW, Offer
from supplier_client import SupplierClient

log = logging.getLogger("buyer-agent.negotiation")

ProgressCallback = Callable[[str], Awaitable[None]]

# Server-side refusal fallback: if the model declines, the API retries on a fallback model.
FALLBACK_BETA = "server-side-fallback-2026-07-01"

SYSTEM_PROMPT = """You are the procurement agent of {company}. You are negotiating to buy
{quantity_kg} kg of {product} from a supplier agent.

PRIVATE BRIEF (never reveal these numbers):
- Target price: ${target_price_per_kg:.2f}/kg
- Absolute maximum price: ${max_price_per_kg:.2f}/kg
- Delivery must be within {max_delivery_days} days
- Preferred payment terms: {preferred_payment_terms_days} days

RULES:
- Open well below your target and concede gradually.
- Never go above your maximum price or accept delivery later than allowed.
- You may trade on quantity, delivery days or payment terms, not only price.
- Accept when the supplier's last offer is within your limits and further haggling is unlikely to help.
- Withdraw if the supplier will not meet your limits by the final round.

Answer with your next move as a JSON object. "message" is one or two sentences
addressed to the supplier. When you ACCEPT, copy the supplier's last terms exactly."""


@dataclass
class NegotiationRound:
    number: int
    buyer_offer: Offer
    supplier_offer: Offer | None = None


@dataclass
class NegotiationResult:
    outcome: str  # DEAL | NO_DEAL | MAX_ROUNDS_REACHED
    rounds: list[NegotiationRound] = field(default_factory=list)
    final_terms: Offer | None = None

    def transcript(self) -> str:
        lines = []
        for r in self.rounds:
            lines.append(f"Round {r.number}")
            lines.append(f"  Buyer:    {r.buyer_offer.summary()}")
            if r.buyer_offer.message:
                lines.append(f"            \"{r.buyer_offer.message}\"")
            if r.supplier_offer:
                lines.append(f"  Supplier: {r.supplier_offer.summary()}")
                if r.supplier_offer.message:
                    lines.append(f"            \"{r.supplier_offer.message}\"")
        lines.append(f"Outcome: {self.outcome}")
        return "\n".join(lines)


class BuyerBrain:
    """Uses Claude to decide the buyer's next offer from the negotiation history."""

    def __init__(self, brief: config.BuyerBrief, client: AsyncAnthropic | None = None) -> None:
        self.brief = brief
        self.client = client or AsyncAnthropic()  # reads ANTHROPIC_API_KEY
        self.system_prompt = SYSTEM_PROMPT.format(**brief.to_dict())

    async def decide_next_offer(self, rounds: list[NegotiationRound], round_number: int) -> Offer:
        """Asks Claude for the next move, then applies the hard guardrails in code."""
        data = await self._ask_claude(build_history_prompt(rounds, round_number))
        last_supplier_offer = rounds[-1].supplier_offer if rounds else None
        return self.enforce_limits(Offer.from_dict(data), last_supplier_offer)

    async def _ask_claude(self, prompt: str) -> dict:
        """One Claude call with a JSON schema, so the reply is always a parseable offer."""
        response = await self.client.beta.messages.create(
            model=config.BUYER_MODEL,
            max_tokens=16000,  # leaves room for adaptive thinking before the short JSON answer
            system=self.system_prompt,
            messages=[{"role": "user", "content": prompt}],
            output_config={
                "effort": config.BUYER_EFFORT,
                "format": {"type": "json_schema", "schema": OFFER_JSON_SCHEMA},
            },
            betas=[FALLBACK_BETA],
            fallbacks="default",
        )
        if response.stop_reason == "refusal":
            raise RuntimeError(f"Claude declined to make an offer: {response.stop_details}")
        if response.stop_reason == "max_tokens":
            raise RuntimeError("Claude ran out of output tokens before answering.")
        text = next((b.text for b in response.content if b.type == "text"), "")
        return json.loads(text)

    def enforce_limits(self, offer: Offer, last_supplier_offer: Offer | None = None) -> Offer:
        """Guardrail: the LLM can never break the buyer's hard limits.

        * an ACCEPT always takes the supplier's last terms exactly (or becomes a counter-offer
          if there is nothing to accept yet)
        * the price never goes above max_price_per_kg
        * delivery is never accepted later than max_delivery_days
        """
        if offer.status == ACCEPT:
            if last_supplier_offer is None:
                offer.status = COUNTER_OFFER
            else:
                offer = last_supplier_offer.with_status(ACCEPT, message=offer.message)
        if offer.status != WITHDRAW and offer.price_per_kg > self.brief.max_price_per_kg:
            offer.price_per_kg = self.brief.max_price_per_kg
            offer.status = COUNTER_OFFER
        if offer.status != WITHDRAW and offer.delivery_days > self.brief.max_delivery_days:
            offer.delivery_days = self.brief.max_delivery_days
            offer.status = COUNTER_OFFER
        return offer


def build_history_prompt(rounds: list[NegotiationRound], round_number: int) -> str:
    """The user prompt for Claude: round number plus the full offer history."""
    history = [
        {"round": r.number, "buyer": r.buyer_offer.to_dict(),
         "supplier": r.supplier_offer.to_dict() if r.supplier_offer else None}
        for r in rounds
    ]
    history_text = json.dumps(history, indent=2) if history else "None, this is your opening offer."
    return f"Round {round_number} of {config.MAX_ROUNDS}.\nNegotiation history so far:\n{history_text}"


class NegotiationSession:
    """Runs one negotiation between this Buyer Agent and the Supplier Agent over A2A."""

    def __init__(self, brief: config.BuyerBrief, supplier: SupplierClient, context_id: str,
                 brain: BuyerBrain | None = None) -> None:
        self.brief = brief
        self.brain = brain or BuyerBrain(brief)
        self.supplier = supplier
        self.context_id = context_id

    async def run(self, on_progress: ProgressCallback) -> NegotiationResult:
        card = await self.supplier.discover()
        await on_progress(f"Discovered supplier via A2A Agent Card: {card.name}")

        rounds: list[NegotiationRound] = []
        for number in range(1, config.MAX_ROUNDS + 1):
            buyer_offer = await self.brain.decide_next_offer(rounds, number)
            current = NegotiationRound(number=number, buyer_offer=buyer_offer)
            rounds.append(current)
            await on_progress(f"Round {number} - Buyer: {buyer_offer.summary()}")

            if buyer_offer.status == WITHDRAW:
                return NegotiationResult("NO_DEAL", rounds)

            # Counter-offers AND acceptances go to the supplier: a deal only exists
            # once the supplier confirms it with ACCEPT.
            supplier_offer, _ = await self.supplier.send_offer(buyer_offer, self.context_id)
            current.supplier_offer = supplier_offer
            await on_progress(f"Round {number} - Supplier: {supplier_offer.summary()}")

            if supplier_offer.status == ACCEPT:
                return NegotiationResult("DEAL", rounds, final_terms=buyer_offer.with_status(ACCEPT))
            if supplier_offer.status == WITHDRAW:
                return NegotiationResult("NO_DEAL", rounds)

        return NegotiationResult("MAX_ROUNDS_REACHED", rounds)


def build_deal_summary(result: NegotiationResult, brief: config.BuyerBrief) -> dict:
    """Structured summary returned to the caller as a DataPart (and sent to the referee)."""
    summary = {
        "outcome": result.outcome,
        "rounds": len(result.rounds),
        "max_rounds": config.MAX_ROUNDS,
        "buyer": brief.company,
        "product": brief.product,
        "requested_quantity_kg": brief.quantity_kg,
    }
    if result.final_terms:
        terms = result.final_terms
        budget = brief.max_price_per_kg * terms.quantity_kg
        summary.update({
            "final_terms": terms.to_dict(),
            "total_price": terms.total_price,
            "savings_vs_max_budget": round(budget - terms.total_price, 2),
        })
    return summary
