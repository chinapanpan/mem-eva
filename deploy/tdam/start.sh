#!/usr/bin/env bash
# Start TDAM memory-core (+ LiteLLM→Bedrock gateway) on one host.
#   TDAM_API_KEY=<random> ./start.sh            # key is required; clients send "Authorization: Bearer <key>"
# Env: TDAM_IMAGE (default pinned digest), TDAM_BIND (default 0.0.0.0; restrict with security groups), DATA_VOLUME
set -euo pipefail
cd "$(dirname "$0")"
: "${TDAM_API_KEY:?set TDAM_API_KEY}"
TDAM_IMAGE="${TDAM_IMAGE:-agentmemory/memory-core@sha256:342331d5ccdb31ce84ac1ba205aa0b1a8b16a13fe71b6a104d17d41197eb7112}"
TDAM_BIND="${TDAM_BIND:-0.0.0.0}"
DATA_VOLUME="${DATA_VOLUME:-tdam-bench-data}"
TDAM_PORT="${TDAM_PORT:-8420}"
CONTAINER="${CONTAINER:-tdam-core}"
# LiteLLM key is persisted so re-running this script can never leave TDAM and LiteLLM with different keys
[ -f .litellm_key ] || echo "sk-$(head -c 12 /dev/urandom | od -An -tx1 | tr -d ' \n')" > .litellm_key
LITELLM_MASTER_KEY="${LITELLM_MASTER_KEY:-$(cat .litellm_key)}"
export LITELLM_MASTER_KEY MEMEVA_LITELLM_USAGE="${MEMEVA_LITELLM_USAGE:-$PWD/litellm_usage.jsonl}"

# 1) LiteLLM gateway (always (re)started with the persisted key, unless SKIP_LITELLM=1 for an extra instance)
if [ "${SKIP_LITELLM:-0}" != 1 ]; then
pkill -f "litellm --config" 2>/dev/null || true
[ -d .venv ] || { python3.12 -m venv .venv && .venv/bin/pip install -q 'litellm[proxy]==1.110.0' boto3; }
PYTHONPATH="$PWD" setsid nohup .venv/bin/litellm --config litellm.yaml --host 0.0.0.0 --port 4000 > litellm.log 2>&1 < /dev/null &
for _ in $(seq 90); do curl -sf -m 3 localhost:4000/health/liveliness >/dev/null && break; sleep 2; done
# smoke-check the key TDAM will use
curl -sf -m 60 localhost:4000/v1/embeddings -H "Authorization: Bearer $LITELLM_MASTER_KEY" -H 'content-type: application/json' \
  -d '{"model":"tdam-embed","input":"ping"}' >/dev/null || { echo "LiteLLM key/embedding check FAILED"; exit 1; }
fi

# 2) memory-core config (rendered with the LiteLLM key)
sed "s#__LLM_KEY__#${LITELLM_MASTER_KEY}#g" tdai-gateway.yaml.tmpl > tdai-gateway.yaml
docker volume create "$DATA_VOLUME" >/dev/null
docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
docker run -d --name "$CONTAINER" --restart unless-stopped --add-host host.docker.internal:host-gateway \
  -p "${TDAM_BIND}:${TDAM_PORT}:8420" -v "$DATA_VOLUME:/data/tdai-memory" \
  -v "$PWD/tdai-gateway.yaml:/data/config/tdai-gateway.yaml:ro" \
  -e TDAI_GATEWAY_PORT=8420 -e TDAI_GATEWAY_HOST=0.0.0.0 -e TDAI_GATEWAY_API_KEY="$TDAM_API_KEY" \
  -e TDAI_DATA_DIR=/data/tdai-memory -e STORE_MODE=sqlite "$TDAM_IMAGE" >/dev/null
for _ in $(seq 60); do curl -sf -m 3 localhost:${TDAM_PORT}/health >/dev/null && break; sleep 2; done
curl -s localhost:${TDAM_PORT}/health; echo
