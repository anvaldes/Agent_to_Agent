"""A2A client used by the Buyer Agent to ask the Referee Agent for a verdict.

Request : DataPart {"deal_summary": {...}} + TextPart with the transcript
Response: Task with a "verdict" artifact holding a DataPart with the verdict
"""

import logging

from a2a_client import RemoteAgentClient

log = logging.getLogger("buyer-agent.referee-client")

VERDICT_ARTIFACT = "verdict"


class RefereeClient:
    """Sends the negotiation result to the Referee Agent over A2A."""

    def __init__(self, base_url: str) -> None:
        self.remote = RemoteAgentClient(base_url)

    async def evaluate(self, deal_summary: dict, transcript: str) -> dict:
        """Returns the referee's verdict as a dict (see referee-agent for the schema)."""
        reply = await self.remote.send(text=transcript, data={"deal_summary": deal_summary})
        artifact = reply.artifact(VERDICT_ARTIFACT)
        data_items = artifact.data if artifact else reply.all_data()
        for data in reversed(data_items):
            if isinstance(data, dict) and "winner" in data:
                return data
        raise ValueError(f"Referee reply did not include a verdict: {reply.all_texts()[:300]}")
