"""Small A2A client: discovers an agent through its Agent Card, starts a negotiation with
streaming, and prints every status update and artifact (DataParts as pretty JSON).

Usage:
  python client/client.py                                   # local buyer on :8080
  python client/client.py --url https://buyer-agent-115741533268.us-central1.run.app
  python client/client.py --data '{"quantity_kg": 800, "max_price_per_kg": 8.5}'
"""

import argparse
import asyncio
import json

import httpx

from a2a.client import A2ACardResolver, ClientConfig, create_client
from a2a.helpers import get_data_parts, get_text_parts, new_data_part, new_message, new_text_part
from a2a.types import AgentCard, Artifact, Role, SendMessageRequest, StreamResponse, TaskState

PROMPT = "Start the negotiation."


async def discover(http: httpx.AsyncClient, url: str) -> AgentCard:
    """A2A discovery: GET /.well-known/agent-card.json and print who we are talking to."""
    card = await A2ACardResolver(httpx_client=http, base_url=url).get_agent_card()
    print(f"Agent:       {card.name} (v{card.version})")
    print(f"Description: {card.description}")
    print(f"Endpoint:    {card.supported_interfaces[0].url}")
    print(f"Skills:      {', '.join(s.id for s in card.skills)}")
    print(f"Streaming:   {card.capabilities.streaming}\n")
    return card


def build_message(overrides: dict | None):
    """The user message: the prompt as a TextPart plus optional brief overrides as a DataPart."""
    parts = [new_text_part(PROMPT, media_type="text/plain")]
    if overrides:
        parts.append(new_data_part(overrides, media_type="application/json"))
    return new_message(parts, role=Role.ROLE_USER)


def tidy_numbers(value):
    """DataPart numbers arrive as floats (protobuf Value), so print 500.0 as 500."""
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, dict):
        return {k: tidy_numbers(v) for k, v in value.items()}
    if isinstance(value, list):
        return [tidy_numbers(v) for v in value]
    return value


def print_artifact(artifact: Artifact) -> None:
    print(f"\n=== Artifact: {artifact.name} ===")
    for text in get_text_parts(artifact.parts):
        print(text)
    for data in get_data_parts(artifact.parts):
        print(json.dumps(tidy_numbers(data), indent=2))


def print_event(event: StreamResponse) -> None:
    """Prints one streaming event: status update, artifact, full task or message."""
    if event.HasField("status_update"):
        status = event.status_update.status
        text = "\n".join(get_text_parts(status.message.parts)) if status.HasField("message") else ""
        print(f"[{TaskState.Name(status.state).removeprefix('TASK_STATE_')}] {text}")
    elif event.HasField("artifact_update"):
        print_artifact(event.artifact_update.artifact)
    elif event.HasField("task"):
        task = event.task
        print(f"[task {task.id}] state={TaskState.Name(task.status.state).removeprefix('TASK_STATE_')}")
    elif event.HasField("message"):
        print("\n".join(get_text_parts(event.message.parts)))


async def main() -> None:
    parser = argparse.ArgumentParser(description="Talk to an A2A agent with streaming.")
    parser.add_argument("--url", default="http://localhost:8080", help="Base URL of the agent")
    parser.add_argument("--data", help="Optional JSON object sent as a DataPart (brief overrides)")
    args = parser.parse_args()
    overrides = json.loads(args.data) if args.data else None

    async with httpx.AsyncClient(timeout=httpx.Timeout(900.0)) as http:
        card = await discover(http, args.url)
        client = await create_client(card, client_config=ClientConfig(streaming=True, httpx_client=http))
        try:
            print(f"> {PROMPT}\n")
            async for event in client.send_message(SendMessageRequest(message=build_message(overrides))):
                print_event(event)
        finally:
            await client.close()


if __name__ == "__main__":
    asyncio.run(main())
