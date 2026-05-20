"""Diagnóstico — qué devuelve pyRofex.get_all_orders_status(account=X).

Sin esto no se puede saber si las 17 órdenes que veo en el frontend son:
  (a) realmente lo que el broker tiene → bug en otra parte (cancel
      crea nuevas órdenes?).
  (b) duplicadas porque el broker manda un ER por cada cambio de
      estado y nuestro dedup no funciona.

Uso:
    cd /root/TradingAV && /root/TradingAV/venv/bin/python -m scripts.diag_all_orders --account 805
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter

import pyRofex
from dotenv import load_dotenv

load_dotenv()


def _login() -> None:
    user = os.getenv("ROFEX_USER") or ""
    password = os.getenv("ROFEX_PASSWORD") or ""
    account = os.getenv("ROFEX_ACCOUNT") or ""
    api_url = (os.getenv("ROFEX_API_URL") or "").strip()
    ws_url = (os.getenv("ROFEX_WS_URL") or "").strip()
    if api_url:
        pyRofex._set_environment_parameter("url", api_url, pyRofex.Environment.LIVE)
    if ws_url:
        pyRofex._set_environment_parameter("ws", ws_url, pyRofex.Environment.LIVE)
    pyRofex.initialize(
        user=user, password=password, account=account,
        environment=pyRofex.Environment.LIVE,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--account", required=True)
    args = parser.parse_args()

    _login()
    resp = pyRofex.get_all_orders_status(account=args.account)

    orders = (resp or {}).get("orders", []) or []
    print(f"\n=== get_all_orders_status(account={args.account}) ===")
    print(f"status: {(resp or {}).get('status')}")
    print(f"total entries en orders[]: {len(orders)}")

    # ¿Cuántos clOrdId distintos?
    cl_ord_ids = []
    statuses = Counter()
    tickers = Counter()
    for o in orders:
        rep = o.get("orderReport", o)
        cid = rep.get("clOrdId")
        if cid:
            cl_ord_ids.append(cid)
        statuses[rep.get("status") or "?"] += 1
        tk = (rep.get("instrumentId") or {}).get("symbol") or "?"
        tickers[tk] += 1

    print(f"clOrdIds UNICOS: {len(set(cl_ord_ids))}")
    print(f"clOrdIds repetidos (mismo orden mandó varios ERs):")
    repetidos = Counter(cl_ord_ids).most_common()
    for cid, n in repetidos:
        if n > 1:
            print(f"  {cid}: {n} ERs")

    print(f"\nStatus distribution:")
    for s, n in statuses.most_common():
        print(f"  {s}: {n}")

    print(f"\nTickers distribution:")
    for t, n in tickers.most_common():
        print(f"  {t}: {n}")

    print("\n=== Primeras 3 entries completas (raw) ===")
    for o in orders[:3]:
        print(json.dumps(o, indent=2, default=str))
        print("---")

    return 0


if __name__ == "__main__":
    sys.exit(main())
