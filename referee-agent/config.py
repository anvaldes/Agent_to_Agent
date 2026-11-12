"""Configuration for the Referee Agent. Everything is read from environment variables."""

import os

# --- Service settings ---------------------------------------------------------
PORT = int(os.getenv("PORT", "8082"))

# Public URL of this agent, advertised in its Agent Card (A2A discovery).
# On Cloud Run: https://referee-agent-<PROJECT_NUMBER>.<REGION>.run.app
# A2A clients send their requests to this URL, so it must be reachable by callers.
AGENT_URL = os.getenv("AGENT_URL", f"http://localhost:{PORT}")

# --- LLM settings (Claude) ----------------------------------------------------
# ANTHROPIC_API_KEY is injected by Cloud Run from Secret Manager.
REFEREE_MODEL = os.getenv("REFEREE_MODEL", "claude-opus-5-5")
# Reasoning depth for the verdict: low | medium | high | xhigh | max.
REFEREE_EFFORT = os.getenv("REFEREE_EFFORT", "medium")

# --- Scoring -------------------------------------------------------------------
# Public market reference price used to judge who got the better deal.
MARKET_PRICE_PER_KG = float(os.getenv("MARKET_PRICE_PER_KG", "8.25"))
