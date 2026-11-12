#!/usr/bin/env bash
# Starts the three agents locally:
#   buyer-agent    -> http://localhost:8080  (Claude)
#   supplier-agent -> http://localhost:8081  (Gemini on Vertex AI)
#   referee-agent  -> http://localhost:8082  (Claude)
#
# Requirements:
#   * Python 3.12+ (the script creates .venv and installs dependencies on first run)
#   * ANTHROPIC_API_KEY exported, or in a .env file at the repo root (never commit it)
#   * Vertex AI credentials for the supplier: gcloud auth application-default login
#
# Then, in another terminal:  .venv/bin/python client/client.py
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

# Optional local secrets (e.g. ANTHROPIC_API_KEY=...)
if [ -f .env ]; then set -a; . ./.env; set +a; fi

PYTHON="${PYTHON:-python3.12}"
if [ ! -x .venv/bin/python ]; then
  echo "Creating .venv with $PYTHON"
  "$PYTHON" -m venv .venv
fi
.venv/bin/python -m pip install -q -r buyer-agent/requirements.txt \
  -r supplier-agent/requirements.txt -r referee-agent/requirements.txt

[ -n "${ANTHROPIC_API_KEY:-}" ] || echo "WARNING: ANTHROPIC_API_KEY is not set: buyer and referee will fail on their first Claude call."

GCP_PROJECT="${GOOGLE_CLOUD_PROJECT:-lab-experiments-514316}"
PIDS=()
cleanup() { echo; echo "Stopping agents"; kill "${PIDS[@]}" 2>/dev/null || true; }
trap cleanup EXIT INT TERM

start_agent() {  # start_agent <folder> <port> [ENV=VALUE ...]
  local folder="$1" port="$2"; shift 2
  (cd "$folder" && env PORT="$port" AGENT_URL="http://localhost:$port" "$@" \
     "$ROOT/.venv/bin/python" -m uvicorn main:app --host 127.0.0.1 --port "$port" \
     2>&1 | sed -u "s/^/[$folder] /") &
  PIDS+=("$!")
}

start_agent supplier-agent 8081 \
  GOOGLE_GENAI_USE_VERTEXAI=true GOOGLE_CLOUD_PROJECT="$GCP_PROJECT" GOOGLE_CLOUD_LOCATION=global
start_agent referee-agent 8082
start_agent buyer-agent 8080 \
  SUPPLIER_AGENT_URL=http://localhost:8081 REFEREE_AGENT_URL=http://localhost:8082 USE_ID_TOKEN=false

echo "Agents starting. Run the client in another terminal:  .venv/bin/python client/client.py"
wait
