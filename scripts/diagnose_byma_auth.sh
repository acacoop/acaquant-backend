#!/bin/bash
# scripts/diagnose_byma_auth.sh — probar 8 variantes del OAuth2 de BYMA.
#
# Cargá el .env con las credenciales y corré este script. Prueba cada
# combinación razonable y loggea status + body. El que dé HTTP_CODE=200
# con un access_token en el body es la forma correcta.
#
# Uso:
#     bash /root/TradingAV/scripts/diagnose_byma_auth.sh
#
# El script NO expone las credenciales en el output — solo los primeros
# y últimos 4 caracteres a modo de referencia.

set -e

ENV_FILE="/root/TradingAV/.env"

if [ ! -f "$ENV_FILE" ]; then
  echo "No encontré $ENV_FILE" >&2
  exit 1
fi

# Cargar vars del .env al entorno (respeta = al medio, ignora comentarios)
set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

URL="${BYMA_TOKEN_URL:-https://apigw.byma.com.ar/oauth/token/}"
CID="${BYMA_CLIENT_ID:-}"
CSE="${BYMA_CLIENT_SECRET:-}"

if [ -z "$CID" ] || [ -z "$CSE" ]; then
  echo "BYMA_CLIENT_ID o BYMA_CLIENT_SECRET vacíos en $ENV_FILE" >&2
  exit 1
fi

redact() {
  local s="$1"
  local len=${#s}
  if [ "$len" -le 8 ]; then
    echo "***"
  else
    echo "${s:0:4}...${s: -4}"
  fi
}

run_test() {
  local label="$1"
  shift
  echo ""
  echo "── $label ──"
  # Separar output HTTP del body mediante marker único.
  RESP=$(curl -s -w "\n__HTTP_CODE__=%{http_code}\n__SIZE__=%{size_download}\n" "$@" 2>&1 || true)
  # Extraer y mostrar
  echo "$RESP" | sed 's/^/    /'
}

echo "============================================================"
echo " BYMA OAuth2 — diagnóstico de autenticación"
echo "============================================================"
echo " URL:                $URL"
echo " BYMA_CLIENT_ID:     $(redact "$CID")  (len=${#CID})"
echo " BYMA_CLIENT_SECRET: $(redact "$CSE")  (len=${#CSE})"
echo ""

# ─────────────────────────────────────────────────────────────────
# Check 1 — contenido crudo del .env para detectar CRLF / espacios
# ─────────────────────────────────────────────────────────────────
echo "── Check 1: whitespace en .env (buscá ^M$ o espacios al final) ──"
cat -A "$ENV_FILE" | grep -E '^(BYMA_|#)' | sed 's/^/    /' || echo "  (sin líneas BYMA)"

# ─────────────────────────────────────────────────────────────────
# Variantes de request
# ─────────────────────────────────────────────────────────────────

# Variante A: exacto como la doc (body-encoded, sin Basic Auth)
run_test "A) body form-urlencoded (como la doc)" \
  --location --request POST "$URL" \
  --header 'Content-Type: application/x-www-form-urlencoded' \
  --data-urlencode "client_id=$CID" \
  --data-urlencode "client_secret=$CSE" \
  --data-urlencode 'grant_type=client_credentials' \
  --data-urlencode 'scope=bymaPrimariasPlacements.read'

# Variante B: Basic Auth en header (sin credenciales en body)
run_test "B) Basic Auth en header (-u client_id:secret)" \
  --location --request POST "$URL" \
  -u "$CID:$CSE" \
  --header 'Content-Type: application/x-www-form-urlencoded' \
  --data-urlencode 'grant_type=client_credentials' \
  --data-urlencode 'scope=bymaPrimariasPlacements.read'

# Variante C: Basic Auth + credenciales en body (mezcla, algunos servers lo exigen)
run_test "C) Basic Auth + body (ambos a la vez)" \
  --location --request POST "$URL" \
  -u "$CID:$CSE" \
  --header 'Content-Type: application/x-www-form-urlencoded' \
  --data-urlencode "client_id=$CID" \
  --data-urlencode "client_secret=$CSE" \
  --data-urlencode 'grant_type=client_credentials' \
  --data-urlencode 'scope=bymaPrimariasPlacements.read'

# Variante D: sin scope (por si el scope configurado no es ese)
run_test "D) body sin scope" \
  --location --request POST "$URL" \
  --header 'Content-Type: application/x-www-form-urlencoded' \
  --data-urlencode "client_id=$CID" \
  --data-urlencode "client_secret=$CSE" \
  --data-urlencode 'grant_type=client_credentials'

# Variante E: URL sin trailing slash
URL_NOSLASH="${URL%/}"
run_test "E) URL sin trailing slash: $URL_NOSLASH" \
  --location --request POST "$URL_NOSLASH" \
  --header 'Content-Type: application/x-www-form-urlencoded' \
  --data-urlencode "client_id=$CID" \
  --data-urlencode "client_secret=$CSE" \
  --data-urlencode 'grant_type=client_credentials' \
  --data-urlencode 'scope=bymaPrimariasPlacements.read'

# Variante F: con Accept: application/json
run_test "F) con Accept: application/json" \
  --location --request POST "$URL" \
  --header 'Content-Type: application/x-www-form-urlencoded' \
  --header 'Accept: application/json' \
  --data-urlencode "client_id=$CID" \
  --data-urlencode "client_secret=$CSE" \
  --data-urlencode 'grant_type=client_credentials' \
  --data-urlencode 'scope=bymaPrimariasPlacements.read'

# Variante G: scope con read distinto (algunas APIs usan 'placements.read' o similares)
run_test "G) scope='placements.read' (variante)" \
  --location --request POST "$URL" \
  --header 'Content-Type: application/x-www-form-urlencoded' \
  --data-urlencode "client_id=$CID" \
  --data-urlencode "client_secret=$CSE" \
  --data-urlencode 'grant_type=client_credentials' \
  --data-urlencode 'scope=placements.read'

# Variante H: GET en vez de POST (algunos portales raros)
run_test "H) GET en vez de POST (query string)" \
  --location "$URL?client_id=$(printf '%s' "$CID" | sed 's/ /%20/g')&client_secret=$(printf '%s' "$CSE" | sed 's/ /%20/g')&grant_type=client_credentials&scope=bymaPrimariasPlacements.read"

# Variante I: verbose del que diría que es el correcto (A), para ver headers de vuelta
echo ""
echo "── Variante A verbose (headers completos) ──"
curl -v --location --request POST "$URL" \
  --header 'Content-Type: application/x-www-form-urlencoded' \
  --data-urlencode "client_id=$CID" \
  --data-urlencode "client_secret=$CSE" \
  --data-urlencode 'grant_type=client_credentials' \
  --data-urlencode 'scope=bymaPrimariasPlacements.read' 2>&1 \
  | grep -E '^[<>]' | sed 's/^/    /'

echo ""
echo "============================================================"
echo " Fin. La variante que dé HTTP_CODE=200 con 'access_token'"
echo " en el body es la correcta. Pegame el output completo."
echo "============================================================"
