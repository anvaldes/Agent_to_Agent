"""A2A client used by the Buyer Agent to talk to the Supplier Agent.

Each offer is sent as a TextPart (human-readable) + DataPart ({"offer": {...}}).
The Supplier answers with a Task whose "counter_offer" artifact uses the same contract.
"""

import logging

from a2a.types import AgentCard

import config
from a2a_client import RemoteAgentClient, RemoteReply
from offer import Offer, extract_json_object

log = logging.getLogger("buyer-agent.supplier-client")

COUNTER_OFFER_ARTIFACT = "counter_offer"


class SupplierClient:
    """Talks to the Supplier Agent using the A2A protocol."""

    def __init__(self, base_url: str = config.SUPPLIER_AGENT_URL) -> None:
        self.remote = RemoteAgentClient(base_url)

    async def discover(self) -> AgentCard:
        """A2A discovery: download and cache the Supplier's Agent Card."""
        return await self.remote.discover()

    async def send_offer(self, offer: Offer, context_id: str) -> tuple[Offer, str]:
        """Sends an offer over A2A and returns (supplier_counter_offer, supplier_text).

        The same `context_id` is reused on every round, so the Supplier Agent sees
        the whole negotiation as one conversation.
        """
        reply = await self.remote.send(
            text=offer.message or offer.summary(),
            data={"offer": offer.to_dict()},
            context_id=context_id,
        )
        return parse_supplier_reply(reply)


def parse_supplier_reply(reply: RemoteReply) -> tuple[Offer, str]:
    """Reads the counter-offer from a DataPart, falling back to JSON inside the text."""
    preferred = reply.artifact(COUNTER_OFFER_ARTIFACT)
    data_items = preferred.data if preferred else reply.all_data()
    text = "\n".join(preferred.texts) if preferred else reply.all_texts()

    for data in reversed(data_items):
        if isinstance(data, dict) and "offer" in data:
            return Offer.from_dict(data["offer"]), text

    fallback = extract_json_object(text)
    if fallback:
        return Offer.from_dict(fallback.get("offer", fallback)), text
    raise ValueError(f"Supplier reply did not include an offer: {text[:300]}")
