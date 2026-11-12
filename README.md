# Agent2Agent (A2A) test repo on Cloud Run

> **This is a test / learning repository.** It exists to try out the Agent2Agent (A2A) protocol end to
> end: three AI agents, three separate Cloud Run services, talking to each other only through A2A.
> It is a demo, not production code. Some shortcuts are deliberate: state lives in memory, the services
> are public (`--allow-unauthenticated`), and there is a single shared API key.

## 1. What this repo is

A small B2B negotiation game used as a test bed for A2A:

- **Central Coffee Co.** (buyer agent) wants to buy 500 kg of roasted specialty coffee beans.
- **Andes Roasters** (supplier agent) wants to sell them at a good margin.
- A neutral **referee agent** checks the final deal and scores both sides.

Each agent is its own service, with its own LLM calls, private instructions and deployment. All three
use Claude. They never share code, memory or databases: everything they know about each other comes
through A2A messages. The protocol version is 1.0 (JSON-RPC binding), implemented with the official
Python SDK `a2a-sdk==1.3.0`.

## 2. What is Agent2Agent (A2A)?

[Agent2Agent (A2A)](https://a2a-protocol.org) is an open protocol that lets AI agents discover each other
and work together, even when different teams build them with different frameworks, LLMs or vendors.
Google introduced it in April 2025, and it is now an open-source project under the Linux Foundation.

The idea: an agent should be able to delegate work to another agent the way a browser talks to any web
server, through a shared contract and without knowing how the other side is built. Each agent is treated
as an **opaque** service: you see what it can do, not its prompts, tools or internal state.

### Core concepts

| Concept | What it is | Where you see it in this repo |
|---|---|---|
| **Agent Card** | A JSON "business card" at `/.well-known/agent-card.json`: the agent's name, description, endpoint URL, capabilities and skills. Clients read it to discover the agent. | `build_agent_card()` in each `main.py` |
| **Skill** | One thing the agent can do, described in its Agent Card. | `negotiate_purchase`, `quote_and_negotiate`, `evaluate_deal` |
| **Message** | One turn of communication, from a user (client) or from an agent. | Each offer the buyer sends to the supplier |
| **Part** | The content of a message or artifact: a `TextPart` (text), a `DataPart` (structured JSON) or a file. | Every offer is a TextPart plus a DataPart |
| **Task** | A unit of work with an id and a lifecycle (`SUBMITTED → WORKING → COMPLETED / FAILED / REJECTED…`). | One Task per negotiation round, and one for the whole negotiation |
| **Artifact** | The output a Task produces. | `counter_offer`, `deal_summary`, `transcript`, `verdict` |
| **contextId** | Groups several Tasks and messages into one conversation. | The buyer reuses one `context_id` for every round |
| **Transport** | How requests travel: JSON-RPC 2.0, gRPC or HTTP+JSON, over HTTPS. Streaming uses Server-Sent Events (SSE). | JSON-RPC on `POST /`, streaming status updates to `client.py` |

### A2A and MCP

A2A is often mentioned alongside the [Model Context Protocol (MCP)](https://modelcontextprotocol.io).
They solve different problems and work well together:

- **MCP** connects an agent to its **tools and data** (databases, APIs, files). The agent controls the tool.
- **A2A** connects an agent to **other agents**. Each agent is autonomous and decides how to do the work.

In this repo, the supplier could use MCP internally to read real stock levels from an ERP, and it would
still talk to the buyer over A2A.

## 3. Why A2A? Benefits

| Benefit | What it means | How this repo shows it |
|---|---|---|
| **Interoperability** | Agents built with different LLMs, frameworks or vendors can work together through one standard. | The supplier was first built on Gemini and later switched to Claude, with no change to the buyer: the contract is A2A, not the model. |
| **Opacity and privacy** | Agents work together without exposing their prompts, tools or internal data. | Each side's private brief (budget, cost, floor price) never leaves its service. Only offers cross the wire. |
| **Dynamic discovery** | A client learns what an agent can do, and where to call it, from its Agent Card. | The buyer reads the supplier's and referee's Agent Cards at runtime instead of hard-coding their APIs. |
| **Independent deployment and scaling** | Each agent is its own service, with its own lifecycle, team and scaling rules. | Three Cloud Run services, three images and per-agent settings (for example `--max-instances=1` only on the supplier). |
| **Long-running work with feedback** | Tasks have states, streaming updates and artifacts, so a multi-step job can report progress. | `client.py` shows each negotiation round live as a status update, then the final artifacts. |
| **Structured, reliable exchange** | DataParts carry typed JSON next to human-readable text, so agents agree on terms without parsing prose. | The `{"offer": {...}}` contract shared by buyer and supplier. |
| **Built on web standards** | HTTP, JSON-RPC, SSE and standard authentication schemes; it runs anywhere an HTTP service runs. | Plain Starlette apps behind uvicorn on Cloud Run, testable with `curl`. |

## 4. How the code works

### Architecture

```
                         A2A (JSON-RPC over HTTPS)
  ┌──────────────┐  "Start the negotiation."  ┌───────────────────────────┐
  │ client.py    │ ─────────────────────────▶ │ buyer-agent      (Claude) │
  │ (or curl)    │ ◀───────────────────────── │ Central Coffee Co.        │
  └──────────────┘  streaming status updates  └──────┬──────────────┬─────┘
                    + 3 artifacts                    │              │
                                   1 Task per round  │              │ after the
                    offer ⇄ counter_offer (same      │              │ negotiation
                    context_id for every round)      ▼              ▼
                              ┌──────────────────────────┐  ┌───────────────────────┐
                              │ supplier-agent  (Claude) │  │ referee-agent (Claude)│
                              │ Andes Roasters           │  │ evaluate_deal         │
                              │ counter_offer artifact   │  │ verdict artifact      │
                              └──────────────────────────┘  └───────────────────────┘

  Every agent exposes:  GET /.well-known/agent-card.json   (A2A discovery)
                        POST /                              (A2A JSON-RPC: SendMessage, SendStreamingMessage, GetTask, ...)
                        GET /health                         (plain health check)
```

### The agents

| Service | LLM | Agent Card name | Skill | What it does |
|---|---|---|---|---|
| `buyer-agent` | Claude (`BUYER_MODEL`, default `claude-sonnet-5-5`) | Buyer Agent - Central Coffee Co. | `negotiate_purchase` | Discovers the supplier through its Agent Card, runs up to `MAX_ROUNDS` (6) rounds within a private budget (target $7.50/kg, max $9.00/kg, delivery ≤ 30 days), then sends the result to the referee. Returns the artifacts `deal_summary`, `transcript` and `verdict`. |
| `supplier-agent` | Claude (`SUPPLIER_MODEL`, default `claude-opus-5-5`) | Supplier Agent - Andes Roasters | `quote_and_negotiate` | Answers each offer with a `counter_offer` artifact. Keeps the history per `context_id` and sends all of it to Claude every turn. Private brief: cost $6.20, floor $6.80, list $9.50/kg; delivery in less than 20 days +8%; payment terms beyond 30 days +3%; 5% off above 800 kg. |
| `referee-agent` | Claude (`REFEREE_MODEL`, default `claude-opus-5-5`) | Referee Agent | `evaluate_deal` | Runs rule checks in code, then asks Claude to score both sides against a public market price (`MARKET_PRICE_PER_KG`, default $8.25). Returns a `verdict` artifact. |

**Guardrails in code, not just in prompts.** Each LLM proposes a move, and code checks it before anything is sent:
- Buyer: never offers more than `max_price_per_kg`, never accepts delivery later than allowed, and an `ACCEPT` always copies the supplier's last terms.
- Supplier: never offers or accepts less than the floor price, raised by 8% for rush delivery and by 3% for long payment terms. An `ACCEPT` always copies the buyer's terms.

### The offer contract

Every negotiation message (buyer → supplier and supplier → buyer) has two parts:

- a **TextPart**: a short human-readable message
- a **DataPart**:

```json
{"offer": {"price_per_kg": 7.8, "quantity_kg": 500, "delivery_days": 20,
           "payment_terms_days": 30, "status": "COUNTER_OFFER", "message": "..."}}
```

`status` is `COUNTER_OFFER`, `ACCEPT` or `WITHDRAW`. DataParts are `google.protobuf.Value`, so integers come
back as floats (`500.0`); the receivers cast them back to `int`. The buyer reuses the same `context_id`
for every round of one negotiation. Each round is a new A2A Task, and the supplier completes it with a
`counter_offer` artifact. A deal exists only after the supplier answers `ACCEPT`: when the buyer
accepts, the supplier confirms and closes that Task.

Referee request: DataPart `{"deal_summary": {...}}` + TextPart with the transcript. Referee response (`verdict` artifact):

```json
{"valid": true, "rule_violations": [], "buyer_score": 7, "supplier_score": 5,
 "winner": "BUYER", "reasoning": "...", "market_price_per_kg": 8.25}
```

## 5. Run locally

Prerequisites: Python 3.12+ and an Anthropic API key (all three agents use it).

```bash
# 1. Credentials (never commit them: .env is git-ignored)
echo 'ANTHROPIC_API_KEY=sk-ant-...' > .env

# 2. Start the three agents (buyer :8080, supplier :8081, referee :8082)
./scripts/run_local.sh

# 3. In another terminal, start a negotiation with streaming
.venv/bin/python client/client.py
.venv/bin/python client/client.py --data '{"quantity_kg": 900, "max_price_per_kg": 8.5}'   # override the brief
```

`client/client.py` discovers the agent through its Agent Card, sends `Start the negotiation.` with
streaming, and prints every status update and artifact. DataParts are printed as pretty JSON.

### Tests

```bash
.venv/bin/python -m pip install -r buyer-agent/requirements.txt -r supplier-agent/requirements.txt \
  -r referee-agent/requirements.txt pytest pytest-asyncio
.venv/bin/python -m pytest
```

The buyer tests run the real buyer A2A server against a **mock A2A supplier** and a **mock A2A referee**
over real HTTP, with Claude stubbed. They cover: a deal, a supplier `WITHDRAW`, max rounds reached, the
max-price guardrail and the referee verdict. Supplier and referee tests cover the floor-price guardrail,
history per `context_id` and the rule checks, with Claude stubbed.

## 6. Deployment (GitHub Actions → Cloud Run)

Pushing to `main` runs [`.github/workflows/deploy.yml`](.github/workflows/deploy.yml):

1. **changes**: `dorny/paths-filter` detects which agent folders changed. A change to the workflow redeploys all agents.
2. **sync-secrets**: copies the GitHub secret `ANTHROPIC_API_KEY` into Secret Manager, adding a new version only when the value changed.
3. **deploy** (one matrix job per changed agent): builds the agent folder with Docker, pushes it to
   Artifact Registry (`us-central1-docker.pkg.dev/lab-experiments-514316/agent2agent/<agent>`), and runs
   `gcloud run deploy` with the shared settings (runtime SA `agent2agent@…`, 256Mi, `gen1`,
   `--allow-unauthenticated`) plus per-agent settings:

| Agent | Env vars | Secret | Extra |
|---|---|---|---|
| `buyer-agent` | `AGENT_URL`, `SUPPLIER_AGENT_URL`, `REFEREE_AGENT_URL`, `USE_ID_TOKEN=false` | `ANTHROPIC_API_KEY` | `--timeout=900` (a negotiation spans many LLM calls) |
| `supplier-agent` | `AGENT_URL` | `ANTHROPIC_API_KEY` | `--max-instances=1` (history is in memory) |
| `referee-agent` | `AGENT_URL` | `ANTHROPIC_API_KEY` | none |

Convention: **folder name = image name = Cloud Run service name**, and service URLs are
`https://<service>-115741533268.us-central1.run.app`. `AGENT_URL` matters: A2A clients send requests to
the URL advertised in the Agent Card.

## 7. Try the deployed agents

```bash
BUYER=https://buyer-agent-115741533268.us-central1.run.app
SUPPLIER=https://supplier-agent-115741533268.us-central1.run.app
REFEREE=https://referee-agent-115741533268.us-central1.run.app

# Health checks
curl -s $BUYER/health; curl -s $SUPPLIER/health; curl -s $REFEREE/health

# Agent Cards (A2A discovery)
curl -s $BUYER/.well-known/agent-card.json | python3 -m json.tool
curl -s $SUPPLIER/.well-known/agent-card.json | python3 -m json.tool
curl -s $REFEREE/.well-known/agent-card.json | python3 -m json.tool

# Full negotiation with streaming
.venv/bin/python client/client.py --url $BUYER

# Ask the supplier for one quote directly (JSON-RPC SendMessage, A2A 1.0)
curl -s $SUPPLIER/ -H 'Content-Type: application/json' -H 'A2A-Version: 1.0' -d '{
  "jsonrpc": "2.0", "id": 1, "method": "SendMessage",
  "params": {"message": {"messageId": "m-1", "role": "ROLE_USER", "contextId": "demo-1", "parts": [
    {"text": "Opening offer"},
    {"data": {"offer": {"price_per_kg": 7.0, "quantity_kg": 500, "delivery_days": 20,
              "payment_terms_days": 30, "status": "COUNTER_OFFER", "message": "Opening offer"}}}
  ]}}}' | python3 -m json.tool
```

## 8. Repository layout

```
buyer-agent/      main.py · config.py · agent_executor.py · negotiation.py · offer.py
                  a2a_client.py (shared A2A client helper) · supplier_client.py · referee_client.py
supplier-agent/   main.py · config.py · agent_executor.py · negotiation.py · pricing.py · offer.py
referee-agent/    main.py · config.py · agent_executor.py · judge.py · rules.py
client/client.py  streaming A2A client
scripts/run_local.sh
tests/            pytest suite (mock A2A agents, stubbed LLMs)
```

Each agent folder also has `requirements.txt`, a `Dockerfile` (python:3.12-slim + uvicorn on `$PORT`) and
a `.dockerignore`. `offer.py` is duplicated in buyer and supplier because each service builds as its own
image. Keep both copies in sync.
