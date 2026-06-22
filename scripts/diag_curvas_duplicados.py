"""diag_curvas_duplicados.py — docs DUPLICADOS en Trading.Curvas (mismo `ticker`).

Read-only. El gate de renta fija detectó un instrumento (MERV - XMEV - PLC5D - 24hs)
con DOS docs en Curvas: ticker_corto 'PLC50' (cero) y 'PLC5O' (letra O), con sectores
distintos. Esto los busca TODOS: instrumentos (`ticker`) con más de un doc, y dumpea
ticker_corto/curva/sector/actualizado_at de cada uno para decidir cuál es el bueno.

    python -m scripts.diag_curvas_duplicados
"""
from __future__ import annotations

from collections import defaultdict

from core.mongo import get_mongo_client_read


def main() -> int:
    curvas = get_mongo_client_read()["Trading"]["Curvas"]
    por_ticker: dict[str, list[dict]] = defaultdict(list)
    for d in curvas.find({}, {"_id": 0, "ticker": 1, "ticker_corto": 1, "curva": 1,
                              "sector": 1, "actualizado_at": 1, "actualizado_por": 1}):
        tk = d.get("ticker")
        if tk:
            por_ticker[tk].append(d)

    dups = {tk: docs for tk, docs in por_ticker.items() if len(docs) > 1}
    print(f"Trading.Curvas: {sum(len(v) for v in por_ticker.values())} docs · "
          f"{len(por_ticker)} instrumentos únicos\n")
    if not dups:
        print("✅ Sin duplicados (cada `ticker` tiene 1 solo doc).")
        return 0

    print(f"⚠️  {len(dups)} instrumento(s) con MÁS de un doc:\n")
    for tk, docs in sorted(dups.items()):
        print(f"  {tk}")
        for d in docs:
            ts = d.get("actualizado_at")
            print(f"      ticker_corto={d.get('ticker_corto')!r:<10} curva={d.get('curva')!r:<14} "
                  f"sector={d.get('sector')!r:<12} actualizado_at={ts} por={d.get('actualizado_por')}")
    print("\nEl que tenga ticker_corto terminado en '0' (cero) suele ser el ERRÓNEO "
          "(las ONs terminan en letra 'O'/'D'/'C'). Confirmá y te armo el borrado scopeado.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
