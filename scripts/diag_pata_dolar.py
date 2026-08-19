"""scripts/diag_pata_dolar.py — ¿CUÁNTOS de los «cotiza en pesos» tienen pata USD?

Doc madre: `docs/AV_AGENT.md` §0.x. **READ-ONLY**: no escribe una sola fila.

POR QUÉ EXISTE
==============

El hallazgo `cotiza_en_pesos` cerraba con *«No encontré una pata en dólares para
este ticker»* después de mirar UNA tabla (`mercado.especies`), que se siembra a
mano. Ahora mira también el catálogo de Primary — pero **cuántos de los 43 casos
cambian de veredicto con eso es un dato de prod, y no se puede suponer**
(REGLA #2). Este diag lo mide.

Contesta, por bono de curva USD que hoy cotiza en pesos:

    sembrada          ya tenemos la pata D/C en `mercado.especies`
    solo_en_primary   existe en Primary y NO la teníamos  ← lo que el fix destapa
    sin_pata          no está en ninguna de las dos: es el instrumento
    ya_pedida         además, alguien ya la está escuchando (y si llegó precio)

Uso:
    python -m scripts.diag_pata_dolar            # el resumen + las primeras 40
    python -m scripts.diag_pata_dolar --todos    # todas las filas
    python -m scripts.diag_pata_dolar AO29 CO32  # solo esos tickers
"""
from __future__ import annotations

import sys

from api.services import av_agent
from core.postgres import get_pool

_SUF = ("D", "C")


def _corto(sim: str) -> str:
    p = (sim or "").split(" - ")
    return p[2].strip().upper() if len(p) >= 3 else (sim or "").strip()


def main() -> int:
    pedidos = {a.strip().upper() for a in sys.argv[1:] if not a.startswith("--")}
    todos = "--todos" in sys.argv

    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT ticker, instrumento, curva FROM mercado.curvas "
                    "WHERE upper(coalesce(moneda_eje, '')) = 'USD' "
                    "  AND instrumento IS NOT NULL ORDER BY ticker")
        bonos = [(r[0], r[1], r[2]) for r in cur.fetchall()]

        cur.execute("SELECT upper(ticker), simbolo FROM mercado.especies "
                    "WHERE upper(moneda) = 'USD'")
        sembradas: dict[str, list[str]] = {}
        for tk, sim in cur.fetchall():
            sembradas.setdefault(tk, []).append(sim)

        cur.execute("SELECT ticker FROM mercado.adhoc_subscriptions "
                    "WHERE expires_at > now()")
        pedidas = {r[0] for r in cur.fetchall()}

        cur.execute("SELECT ticker, last_price FROM mercado.market_snapshot "
                    "WHERE last_price IS NOT NULL AND last_price > 0")
        precios = {r[0]: float(r[1]) for r in cur.fetchall()}

    primary = av_agent.simbolos_primary()
    if primary is None:
        print("\n  ⚠️  No se pudo leer el catálogo de Primary "
              "(`manager.pyrofex_instruments`).\n"
              "      SIN ÉL ESTE DIAG NO PUEDE CONCLUIR NADA — que es justo el "
              "punto del fix.\n"
              "      Refrescarlo: python -m scripts.discovery_pyrofex\n")
        return 1

    filas, resumen = [], {"sembrada": 0, "solo_en_primary": 0, "sin_pata": 0}
    for tk, simbolo, curva in bonos:
        TK = (tk or "").strip().upper()
        if pedidos and TK not in pedidos:
            continue
        base = _corto(simbolo)
        # ¿Cotiza HOY por una pata que no es de dólares? El precio en pesos es lo
        # que dispara el hallazgo; sin precio el caso es otro (`sin_precio`).
        px = precios.get(simbolo)
        if px is None:
            continue
        if base[-1:].upper() in _SUF:
            continue                      # ya cotiza por su pata en dólares

        mias = sembradas.get(TK) or []
        en_primary = [x for suf in _SUF for x in primary if f" - {base}{suf} - " in x]
        nuevas = [x for x in en_primary if x not in set(mias)]

        origen = ("sembrada" if mias else
                  "solo_en_primary" if nuevas else "sin_pata")
        resumen[origen] += 1
        cand = (sorted(mias) or sorted(nuevas) or [""])[0]
        filas.append((TK, base, origen, _corto(cand) if cand else "—",
                      "sí" if cand in pedidas else "no",
                      f"{precios[cand]:,.2f}" if cand in precios else "—",
                      curva or "—"))

    n = len(filas)
    print(f"\n{'=' * 82}\nBONOS DE CURVA USD QUE HOY COTIZAN EN PESOS: {n}\n{'=' * 82}")
    for k, v in resumen.items():
        pct = f"{v / n * 100:5.1f}%" if n else "    —"
        print(f"  {k:<18} {v:>4}  {pct}")
    print("\n  → `solo_en_primary` es lo que el fix destapa: casos donde el "
          "hallazgo\n    decía «no encontré una pata en dólares» y la pata "
          "EXISTE.\n")

    if filas:
        print(f"{'TICKER':<9}{'PIDE HOY':<11}{'VEREDICTO':<18}"
              f"{'PATA USD':<11}{'PEDIDA':<8}{'PRECIO':<12}CURVA")
        print("-" * 82)
        for f in (filas if todos else filas[:40]):
            print(f"{f[0]:<9}{f[1]:<11}{f[2]:<18}{f[3]:<11}{f[4]:<8}{f[5]:<12}{f[6]}")
        if not todos and n > 40:
            print(f"\n  … {n - 40} más. Verlas todas: --todos")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
