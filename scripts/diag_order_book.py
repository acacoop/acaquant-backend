"""Diagnóstico — por qué /api/operar/order-book?ticker=AL30 no trae datos.

Uso (en el Droplet):
    cd /root/TradingAV && /root/TradingAV/venv/bin/python -m scripts.diag_order_book

Revisa, en orden, los 4 puntos donde puede romperse la cadena:
  1) resolver_ticker_exacto("AL30") → qué full ticker devuelve (o None).
  2) Trading.Curvas: hay match por ticker_corto? Qué ticker completo está
     persistido en `ticker`?
  3) Trading.MarketSnapshot: hay doc para ese ticker? Cuándo es su
     updated_at? Tiene book.bids/book.offers populados o están vacíos?
  4) get_order_book("AL30") → reproducción exacta de lo que devuelve la
     API al request del frontend.

Mismo chequeo para GD30 al final.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime

from api.services.order_book import get_order_book
from api.services.renta_fija import resolver_ticker_exacto
from core.mongo import get_mongo_client_read

TICKERS = ["AL30", "GD30"]


def _stale_str(updated_at) -> str:
    if not updated_at:
        return "(sin updated_at)"
    if isinstance(updated_at, datetime):
        ts = updated_at if updated_at.tzinfo else updated_at.replace(tzinfo=UTC)
        delta = (datetime.now(UTC) - ts).total_seconds()
        return f"hace {delta:.0f}s ({ts.isoformat()})"
    return str(updated_at)


def diag_uno(short: str) -> None:
    print(f"\n========== {short} ==========")
    client = get_mongo_client_read()

    # 1) resolver_ticker_exacto
    full = resolver_ticker_exacto(short)
    print(f"\n[1] resolver_ticker_exacto({short!r}) → {full!r}")
    if not full:
        print(f"    ❌ No matcheó ticker_corto={short!r} en Trading.Curvas.")
        cur_matches = list(
            client["Trading"]["Curvas"].find(
                {"ticker_corto": {"$regex": short, "$options": "i"}},
                {"_id": 0, "ticker_corto": 1, "ticker": 1, "curva": 1},
            ).limit(10)
        )
        print(f"    Curvas con ticker_corto ~ {short}:")
        for d in cur_matches:
            print(f"      - {d}")
        return

    # 2) Trading.Curvas - doc exacto
    doc_curva = client["Trading"]["Curvas"].find_one(
        {"ticker_corto": short}, {"_id": 0, "ticker_corto": 1, "ticker": 1, "curva": 1}
    )
    print(f"\n[2] Trading.Curvas[ticker_corto={short!r}] → {doc_curva}")

    # 3) MarketSnapshot
    snap = client["Trading"]["MarketSnapshot"].find_one(
        {"ticker": full},
        {
            "_id": 0,
            "ticker": 1,
            "updated_at": 1,
            "book": 1,
            "metrics.last_price": 1,
            "metrics.closing_price": 1,
        },
    )
    print(f"\n[3] Trading.MarketSnapshot[ticker={full!r}]:")
    if not snap:
        print(f"    ❌ Sin doc. El motor_rofex no escribió este ticker.")
        # Veamos cuántos docs hay en MarketSnapshot con el corto en el ticker
        regex_matches = list(
            client["Trading"]["MarketSnapshot"].find(
                {"ticker": {"$regex": f"- {short} -", "$options": "i"}},
                {"_id": 0, "ticker": 1, "updated_at": 1},
            ).limit(5)
        )
        print(f"    MarketSnapshot con ticker ~ '- {short} -':")
        for d in regex_matches:
            print(f"      - {d.get('ticker')}  updated_at={_stale_str(d.get('updated_at'))}")
        return

    print(f"    updated_at  : {_stale_str(snap.get('updated_at'))}")
    print(f"    last_price  : {snap.get('metrics', {}).get('last_price')}")
    print(f"    closing     : {snap.get('metrics', {}).get('closing_price')}")
    book = snap.get("book") or {}
    bids = book.get("bids") or []
    offers = book.get("offers") or []
    print(f"    book.bids   : {len(bids)} niveles")
    for b in bids[:3]:
        print(f"      bid  {b}")
    print(f"    book.offers : {len(offers)} niveles")
    for o in offers[:3]:
        print(f"      ask  {o}")
    if not bids and not offers:
        print(f"    ❌ BOOK VACÍO en MarketSnapshot. El motor no tiene depth=5 acá.")
        print(f"       (puede pasar fuera de horario o si pyRofex no manda BI/OF para este ticker)")

    # 4) Reproducción exacta de lo que devuelve la API
    print(f"\n[4] api.services.order_book.get_order_book({short!r}):")
    resp = get_order_book(short)
    if resp is None:
        print(f"    ❌ Devolvió None → la API tira 404.")
    else:
        # Imprimir tal cual lo que recibe el frontend.
        def _default(o):
            if isinstance(o, datetime):
                return o.isoformat()
            return str(o)

        print(json.dumps(resp, indent=2, ensure_ascii=False, default=_default))


def main() -> None:
    print("=" * 60)
    print("DIAG ORDER BOOK — backend /api/operar/order-book")
    print("=" * 60)
    print(f"now (UTC): {datetime.now(UTC).isoformat()}")

    # Verifica que el motor_rofex haya escrito algo recientemente.
    client = get_mongo_client_read()
    last_ms = client["Trading"]["MarketSnapshot"].find_one(
        {}, {"_id": 0, "ticker": 1, "updated_at": 1}, sort=[("updated_at", -1)]
    )
    print(f"\nMarketSnapshot más reciente: {last_ms.get('ticker') if last_ms else '—'}")
    print(f"  updated_at: {_stale_str(last_ms.get('updated_at')) if last_ms else '—'}")

    for t in TICKERS:
        diag_uno(t)

    print("\n" + "=" * 60)
    print("FIN. Si todos los chequeos pasan acá pero el endpoint sigue")
    print("devolviendo vacío, mirá los logs:")
    print("  journalctl -u api.service -n 100 --no-pager | grep operar")
    print("=" * 60)


if __name__ == "__main__":
    main()
