#!/usr/bin/env bash
# Diagnostico de /api/portfolio/pnl-todas pegando contra localhost (sin nginx ni CF)
# Uso: bash scripts/diag_pnl_todas.sh

URL="http://localhost:8000/api/portfolio/pnl-todas"
RESP="/tmp/pnl_todas_resp.json"

echo "[diag] hitting $URL (max 300s)"
START=$(date +%s)
HTTP_STATUS=$(curl -sS -o "$RESP" -m 300 -w "%{http_code}" "$URL" || echo "000")
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

echo "[diag] --- last 80 lines of api.service log ---"
journalctl -u api.service -n 80 --no-pager 2>/dev/null | tail -80
