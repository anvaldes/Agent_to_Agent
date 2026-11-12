"""The Referee's judgment: code rule checks + Claude's scoring of the negotiation.

The verdict returned to callers:
  {"valid": bool, "rule_violations": [str], "buyer_score": 1-10, "supplier_score": 1-10,
   "winner": "BUYER" | "SUPPLIER" | "TIE", "reasoning": str}
"""

import json
import logging

from anthropic import AsyncAnthropic

import config
from rules import check_rules

log = logging.getLogger("referee-agent.judge")

# Server-side refusal fallback: if the model declines, the API retries on a fallback model.
FALLBACK_BETA = "server-side-fallback-2026-07-01"

SYSTEM_PROMPT = """You are a neutral referee for a B2B negotiation between a buyer agent
(Central Coffee Co.) and a supplier agent (Andes Roasters) over roasted specialty coffee beans.
You do not know either side's private limits; judge only what is in the deal summary and transcript.

Public market reference price: ${market_price:.2f}/kg, standard delivery 20 days, standard payment 30 days.

Scoring (integers from 1 to 10):
- buyer_score is high when the final price is well below the market price and the terms favour
  the buyer (faster delivery, longer payment terms, the quantity the buyer asked for).
- supplier_score is high when the final price is at or above the market price and the terms
  favour the supplier (standard or slower delivery, shorter payment terms, larger quantity).
- With no deal, score each side on how reasonably it negotiated (usually 1-5).

rule_violations lists only clear problems visible in the transcript, for example: accepted terms
that differ from the last offer on the table, an agent reneging on an acceptance, or offers that
contradict each other. Do not repeat the code-checked violations you are given. Use an empty list
when there are none.

reasoning is two to four sentences explaining the scores."""

VERDICT_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "rule_violations": {"type": "array", "items": {"type": "string"}},
        "buyer_score": {"type": "integer"},
        "supplier_score": {"type": "integer"},
        "reasoning": {"type": "string"},
    },
    "required": ["rule_violations", "buyer_score", "supplier_score", "reasoning"],
    "additionalProperties": False,
}


class Referee:
    """Evaluates a finished negotiation and returns the verdict dict."""

    def __init__(self, client: AsyncAnthropic | None = None) -> None:
        self._client = client

    @property
    def client(self) -> AsyncAnthropic:
        if self._client is None:
            self._client = AsyncAnthropic()  # reads ANTHROPIC_API_KEY
        return self._client

    async def evaluate(self, deal_summary: dict, transcript: str) -> dict:
        code_violations = check_rules(deal_summary)
        judgment = await self._ask_claude(build_prompt(deal_summary, transcript, code_violations))
        return build_verdict(code_violations, judgment)

    async def _ask_claude(self, prompt: str) -> dict:
        """One Claude call with a JSON schema, so the reply is always parseable."""
        response = await self.client.beta.messages.create(
            model=config.REFEREE_MODEL,
            max_tokens=16000,
            system=SYSTEM_PROMPT.format(market_price=config.MARKET_PRICE_PER_KG),
            messages=[{"role": "user", "content": prompt}],
            output_config={
                "effort": config.REFEREE_EFFORT,
                "format": {"type": "json_schema", "schema": VERDICT_JSON_SCHEMA},
            },
            betas=[FALLBACK_BETA],
            fallbacks="default",
        )
        if response.stop_reason == "refusal":
            raise RuntimeError(f"Claude declined to judge: {response.stop_details}")
        if response.stop_reason == "max_tokens":
            raise RuntimeError("Claude ran out of output tokens before answering.")
        text = next((b.text for b in response.content if b.type == "text"), "")
        return json.loads(text)


def build_prompt(deal_summary: dict, transcript: str, code_violations: list[str]) -> str:
    return (
        f"Deal summary:\n{json.dumps(deal_summary, indent=2)}\n\n"
        f"Transcript:\n{transcript or '(no transcript provided)'}\n\n"
        f"Violations already found by code checks: {json.dumps(code_violations)}"
    )


def build_verdict(code_violations: list[str], judgment: dict) -> dict:
    """Merges code checks with Claude's judgment. Scores are clamped and the winner is derived."""
    violations = code_violations + [str(v) for v in judgment.get("rule_violations", []) if str(v).strip()]
    buyer_score = clamp_score(judgment.get("buyer_score"))
    supplier_score = clamp_score(judgment.get("supplier_score"))
    return {
        "valid": not violations,
        "rule_violations": violations,
        "buyer_score": buyer_score,
        "supplier_score": supplier_score,
        "winner": pick_winner(buyer_score, supplier_score),
        "reasoning": str(judgment.get("reasoning", "")),
        "market_price_per_kg": config.MARKET_PRICE_PER_KG,
    }


def clamp_score(value) -> int:
    try:
        return max(1, min(10, int(round(float(value)))))
    except (TypeError, ValueError):
        return 1


def pick_winner(buyer_score: int, supplier_score: int) -> str:
    if buyer_score > supplier_score:
        return "BUYER"
    if supplier_score > buyer_score:
        return "SUPPLIER"
    return "TIE"
