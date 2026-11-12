"""Shared A2A client helper used by the Buyer Agent to talk to other agents.

This is the "client side" of the Agent2Agent protocol:
  1. Discovery: fetch the remote agent's Agent Card from /.well-known/agent-card.json
  2. Messaging: send an A2A `SendMessage` request (TextPart + DataPart)
  3. Result: read the Task (or Message) the remote agent returns and expose its artifacts

SupplierClient and RefereeClient build on top of RemoteAgentClient.
"""

import asyncio
import logging
from dataclasses import dataclass, field

import httpx

from a2a.client import A2ACardResolver, ClientConfig, create_client
from a2a.helpers import get_data_parts, get_text_parts, new_data_part, new_message, new_text_part
from a2a.types import AgentCard, Part, Role, SendMessageRequest, StreamResponse

import config

log = logging.getLogger("buyer-agent.a2a-client")


def fetch_cloud_run_id_token(audience: str) -> str:
    """Gets a Google-signed ID token so this service can call a private Cloud Run service."""
    import google.auth.transport.requests
    import google.oauth2.id_token

    return google.oauth2.id_token.fetch_id_token(google.auth.transport.requests.Request(), audience)


@dataclass
class RemoteArtifact:
    """One artifact returned by a remote agent, already split into text and data."""

    name: str
    texts: list[str] = field(default_factory=list)
    data: list = field(default_factory=list)


@dataclass
class RemoteReply:
    """What a remote agent answered: its artifacts plus any status/message text."""

    artifacts: list[RemoteArtifact] = field(default_factory=list)
    status_text: str = ""

    def artifact(self, name: str) -> RemoteArtifact | None:
        """Returns the last artifact with the given name, if any."""
        matches = [a for a in self.artifacts if a.name == name]
        return matches[-1] if matches else None

    def all_texts(self) -> str:
        texts = [t for a in self.artifacts for t in a.texts]
        return "\n".join(texts + ([self.status_text] if self.status_text else []))

    def all_data(self) -> list:
        return [d for a in self.artifacts for d in a.data]


class RemoteAgentClient:
    """Discovers a remote agent through its Agent Card and sends it A2A messages."""

    def __init__(self, base_url: str, timeout_seconds: float = 120.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self._agent_card: AgentCard | None = None

    async def _build_http_client(self) -> httpx.AsyncClient:
        """Creates the HTTP client, adding a Cloud Run ID token when USE_ID_TOKEN=true."""
        headers = {}
        if config.USE_ID_TOKEN:
            token = await asyncio.to_thread(fetch_cloud_run_id_token, self.base_url)
            headers["Authorization"] = f"Bearer {token}"
        return httpx.AsyncClient(headers=headers, timeout=httpx.Timeout(self.timeout_seconds))

    async def discover(self) -> AgentCard:
        """A2A discovery: download and cache the remote agent's Agent Card."""
        if self._agent_card is None:
            async with await self._build_http_client() as http:
                resolver = A2ACardResolver(httpx_client=http, base_url=self.base_url)
                self._agent_card = await resolver.get_agent_card()
            log.info("Discovered agent via A2A: %s (%s)", self._agent_card.name, self.base_url)
        return self._agent_card

    async def send(self, text: str, data: dict, context_id: str | None = None) -> RemoteReply:
        """Sends one TextPart + DataPart message and waits for the final Task or Message.

        Note: the A2A client posts to the URL advertised in the remote Agent Card
        (supported_interfaces[].url), so the remote service must set AGENT_URL correctly.
        """
        agent_card = await self.discover()
        message = new_message(
            parts=[
                new_text_part(text, media_type="text/plain"),
                new_data_part(data, media_type="application/json"),
            ],
            context_id=context_id,
            role=Role.ROLE_USER,
        )
        async with await self._build_http_client() as http:
            a2a_client = await create_client(
                agent_card, client_config=ClientConfig(streaming=False, httpx_client=http)
            )
            try:
                reply = RemoteReply()
                async for event in a2a_client.send_message(SendMessageRequest(message=message)):
                    reply = to_remote_reply(event)
            finally:
                await a2a_client.close()
        return reply


def to_remote_reply(event: StreamResponse) -> RemoteReply:
    """Converts an A2A response (Task or Message) into a RemoteReply."""
    if event.HasField("task"):
        task = event.task
        artifacts = [to_remote_artifact(a.name, a.parts) for a in task.artifacts]
        status_text = ""
        if task.status.HasField("message"):
            status_text = "\n".join(get_text_parts(task.status.message.parts))
        return RemoteReply(artifacts=artifacts, status_text=status_text)
    if event.HasField("message"):
        return RemoteReply(artifacts=[to_remote_artifact("message", event.message.parts)])
    return RemoteReply()


def to_remote_artifact(name: str, parts: list[Part]) -> RemoteArtifact:
    return RemoteArtifact(name=name, texts=get_text_parts(parts), data=get_data_parts(parts))
