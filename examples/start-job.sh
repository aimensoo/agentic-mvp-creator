#!/usr/bin/env bash
set -euo pipefail

curl -X POST "${BACKEND_API_URL:-http://127.0.0.1:8000/api/v1}/webhook/trigger" \
  -H "X-Webhook-Secret: ${WEBHOOK_SECRET:?WEBHOOK_SECRET is required}" \
  -H "Content-Type: application/json" \
  -d @examples/webhook-request.json
