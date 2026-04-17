#!/bin/bash
# Pausa / resume del cluster Atlas para ahorrar compute en horas sin mercado.
#
# Uso:
#   atlas_cluster.sh pause
#   atlas_cluster.sh resume
#
# Requiere variables en /root/TradingAV/.env:
#   ATLAS_PUBLIC_KEY
#   ATLAS_PRIVATE_KEY
#   ATLAS_PROJECT_ID     (Group ID, visible en Atlas → Project Settings)
#   ATLAS_CLUSTER_NAME   (ej: ACAQuant)

set -u

ACTION="${1:-}"
if [[ "$ACTION" != "pause" && "$ACTION" != "resume" ]]; then
  echo "usage: $0 {pause|resume}" >&2
  exit 2
fi

# Cargar .env
ENV_FILE="/root/TradingAV/.env"
if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
fi

: "${ATLAS_PUBLIC_KEY:?ATLAS_PUBLIC_KEY no definida}"
: "${ATLAS_PRIVATE_KEY:?ATLAS_PRIVATE_KEY no definida}"
: "${ATLAS_PROJECT_ID:?ATLAS_PROJECT_ID no definida}"
: "${ATLAS_CLUSTER_NAME:?ATLAS_CLUSTER_NAME no definida}"

if [[ "$ACTION" == "pause" ]]; then
  PAUSED="true"
else
  PAUSED="false"
fi

URL="https://cloud.mongodb.com/api/atlas/v2/groups/${ATLAS_PROJECT_ID}/clusters/${ATLAS_CLUSTER_NAME}"
TS=$(date -u '+%Y-%m-%d %H:%M:%S UTC')

echo "[$TS] Atlas ${ACTION} → ${ATLAS_CLUSTER_NAME}"

RESPONSE=$(curl -sS --user "${ATLAS_PUBLIC_KEY}:${ATLAS_PRIVATE_KEY}" --digest \
  -H "Accept: application/vnd.atlas.2024-08-05+json" \
  -H "Content-Type: application/vnd.atlas.2024-08-05+json" \
  -X PATCH "$URL" \
  -d "{\"paused\": ${PAUSED}}" \
  -w "\nHTTP_STATUS:%{http_code}")

HTTP_STATUS=$(echo "$RESPONSE" | grep "^HTTP_STATUS:" | cut -d: -f2)
BODY=$(echo "$RESPONSE" | sed '/^HTTP_STATUS:/d')

echo "HTTP $HTTP_STATUS"
echo "$BODY"

if [[ "$HTTP_STATUS" -ge 200 && "$HTTP_STATUS" -lt 300 ]]; then
  echo "[$TS] OK"
  exit 0
else
  echo "[$TS] FAIL" >&2
  exit 1
fi
