# Agent2Agent (A2A) negotiation demo on Cloud Run

Three independent AI agents, all powered by Claude and each its own Cloud Run service, play a B2B negotiation game.
They talk to each other **only** through the [Agent2Agent (A2A) protocol](https://a2a-protocol.org)
(spec 1.0, JSON-RPC binding, `a2a-sdk==1.3.0`).

- **Central Coffee Co.** (buyer, powered by Claude) wants to buy 500 kg of roasted specialty coffee beans.
- **Andes Roasters** (supplier, powered by Claude) wants to sell them at a good margin.
- A neutral **referee** (powered by Claude) checks the final deal and scores both sides.

## Architecture

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

## The agents

| Service | LLM | Agent Card name | Skill | What it does |
|---|---|---|---|---|
| `buyer-agent` | Claude (`BUYER_MODEL`, default `claude-sonnet-5-5`) | Buyer Agent - Central Coffee Co. | `negotiate_purchase` | Discovers the supplier through its Agent Card, runs up to `MAX_ROUNDS` (6) rounds within a private budget (target $7.50/kg, max $9.00/kg, delivery ≤ 30 days), then sends the result to the referee. Returns the artifacts `deal_summary`, `transcript` and `verdict`. |
| `supplier-agent` | Claude (`SUPPLIER_MODEL`, default `claude-opus-5-5`) | Supplier Agent - Andes Roasters | `quote_and_negotiate` | Answers each offer with a `counter_offer` artifact. Keeps the history per `context_id` and sends all of it to Claude every turn. Private brief: cost $6.20, floor $6.80, list $9.50/kg; delivery in less than 20 days +8%; payment terms beyond 30 days +3%; 5% off above 800 kg. |
| `referee-agent` | Claude (`REFEREE_MODEL`, default `claude-opus-5-5`) | Referee Agent | `evaluate_deal` | Runs rule checks in code, then asks Claude to score both sides against a public market price (`MARKET_PRICE_PER_KG`, default $8.25). Returns a `verdict` artifact. |

**Guardrails in code, not just in prompts.** Each LLM proposes a move, and code checks it before anything is sent:
- Buyer: never offers more than `max_price_per_kg`, never accepts delivery later than allowed, and an `ACCEPT` always copies the supplier's last terms.
- Supplier: never offers or accepts less than the floor price, raised by 8% for rush delivery and by 3% for long payment terms. An `ACCEPT` always copies the buyer's terms.

## The offer contract

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

## Run locally

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

## Deployment (GitHub Actions → Cloud Run)

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

## Try the deployed agents

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

## Repository layout

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
