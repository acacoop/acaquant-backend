#!/usr/bin/env bash
# Diagnostico de /api/portfolio/pnl-todas pegando contra localhost (sin nginx ni CF)
# Lee API_KEY del .env del repo. Uso: bash scripts/diag_pnl_todas.sh

URL="http://localhost:8000/api/portfolio/pnl-todas"
RESP="/tmp/pnl_todas_resp.json"

API_KEY=$(grep -E '^API_KEY=' .env 2>/dev/null | head -1 | cut -d'=' -f2- | tr -d '"' | tr -d "'")
ADMIN_EMAIL=$(grep -E '^MANAGER_EMAILS=' .env 2>/dev/null | head -1 | cut -d'=' -f2- | tr -d '"' | tr -d "'" | cut -d',' -f1)
if [ -z "$API_KEY" ]; then
  echo "[diag] WARNING: no encontré API_KEY en .env — el endpoint va a tirar 401"
fi
if [ -z "$ADMIN_EMAIL" ]; then
  echo "[diag] WARNING: no encontré MANAGER_EMAILS en .env — el endpoint va a tirar 403 (anon)"
fi

echo "[diag] hitting $URL (max 300s, con API key=${API_KEY:+SI}${API_KEY:-NO}, user=$ADMIN_EMAIL)"
START=$(date +%s)
HTTP_STATUS=$(curl -sS -o "$RESP" -m 300 \
  -H "Authorization: Bearer $API_KEY" \
  -H "x-acaquant-user-email: $ADMIN_EMAIL" \
  -w "%{http_code}" "$URL" || echo "000")
END=$(date +%s)
DUR=$((END - START))

echo "[diag] status=$HTTP_STATUS duration=${DUR}s body_size=$(wc -c < "$RESP")b"
echo "[diag] --- first 600b of response ---"
head -c 600 "$RESP"
echo
echo "[diag] --- end response ---"

if [ "$HTTP_STATUS" = "200" ]; then
  python3 -c "
import json, sys
try:
    d = json.load(open('$RESP'))
    t = d.get('totales', {}) or {}
    print(f\"[diag] OK · n_cuentas={t.get('n_cuentas')} n_filas={t.get('n_filas')} pnl_total={t.get('pnl_total')}\")
except Exception as e:
    print(f'[diag] failed parsing response: {e}', file=sys.stderr)
"
fi

echo "[diag] --- últimas líneas de api.service log con 'pnl-todas' o errores ---"
journalctl -u api.service -n 500 --no-pager 2>/dev/null | grep -E "pnl-todas|ERROR|Traceback|Exception" | tail -30
