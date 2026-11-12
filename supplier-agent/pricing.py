"""Pricing rules and the hard guardrail of the Supplier Agent.

The LLM proposes; this module disposes. Whatever Gemini answers, the supplier
never offers or accepts a price below its floor, adjusted for costly terms:

  minimum price = floor x (1 + rush surcharge, if delivery is faster than standard)
                        x (1 + long-payment surcharge, if payment terms exceed 30 days)

The volume discount is a commercial concession, so it is applied to the list
price (see list_price_for) but it can never push a price below the floor.
"""

from config import SupplierBrief
from offer import ACCEPT, COUNTER_OFFER, WITHDRAW, Offer


def term_multiplier(brief: SupplierBrief, delivery_days: int, payment_terms_days: int) -> float:
    """Cost multiplier caused by rush delivery and long payment terms."""
    multiplier = 1.0
    if delivery_days < brief.standard_delivery_days:
        multiplier *= 1 + brief.rush_delivery_surcharge
    if payment_terms_days > brief.preferred_payment_terms_days:
        multiplier *= 1 + brief.long_payment_surcharge
    return multiplier


def minimum_price_for(brief: SupplierBrief, delivery_days: int, payment_terms_days: int) -> float:
    """Lowest price per kg the supplier may offer or accept for these terms."""
    return round(brief.floor_price_per_kg * term_multiplier(brief, delivery_days, payment_terms_days), 2)


def list_price_for(brief: SupplierBrief, quantity_kg: int, delivery_days: int, payment_terms_days: int) -> float:
    """Opening (list) price per kg for these terms, including the volume discount."""
    price = brief.list_price_per_kg * term_multiplier(brief, delivery_days, payment_terms_days)
    if quantity_kg > brief.volume_discount_min_kg:
        price *= 1 - brief.volume_discount
    return round(max(price, minimum_price_for(brief, delivery_days, payment_terms_days)), 2)


def clamp_terms(brief: SupplierBrief, offer: Offer) -> Offer:
    """Keeps delivery and payment terms within what the supplier can actually do."""
    offer.delivery_days = max(offer.delivery_days, brief.fastest_delivery_days)
    offer.payment_terms_days = min(offer.payment_terms_days, brief.long_payment_terms_days)
    return offer


def enforce_floor(brief: SupplierBrief, reply: Offer, buyer_offer: Offer) -> Offer:
    """Guardrail applied to every reply the supplier sends.

    * an ACCEPT always takes the buyer's terms exactly; if those terms are below the
      minimum price, the reply becomes a COUNTER_OFFER at the minimum price instead
    * a COUNTER_OFFER is never below the minimum price for its own terms
    """
    if reply.status == WITHDRAW:
        return reply
    if reply.status == ACCEPT:
        reply = buyer_offer.with_status(ACCEPT, message=reply.message)
        if is_acceptable(brief, buyer_offer):
            return reply
        reply.status = COUNTER_OFFER
    reply = clamp_terms(brief, reply)
    minimum = minimum_price_for(brief, reply.delivery_days, reply.payment_terms_days)
    if reply.price_per_kg < minimum:
        reply.price_per_kg = minimum
    return reply


def is_acceptable(brief: SupplierBrief, offer: Offer) -> bool:
    """True when the supplier may accept these terms (price at or above the adjusted floor)."""
    if offer.delivery_days < brief.fastest_delivery_days:
        return False
    if offer.payment_terms_days > brief.long_payment_terms_days:
        return False
    return offer.price_per_kg >= minimum_price_for(brief, offer.delivery_days, offer.payment_terms_days)
