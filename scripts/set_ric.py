"""set_ric.py — carga MANUAL del RIC (Refinitiv) en el catálogo de CEDEARs.

El RIC es cómo Refinitiv/LSEG identifica el subyacente (ej. RKLB → 'RKLB.O').
La capa ANÁLISIS/RESEARCH lo usa para pedir fundamentals con lseg-data.
Carga manual, idempotente. Ver docs/INTEGRACION_REUTERS.md.

Uso:
    python -m scripts.set_ric RKLB RKLB.O      # asigna el RIC
    python -m scripts.set_ric RKLB --clear     # lo borra
    python -m scripts.set_ric --list           # lista los CEDEARs con RIC cargado
"""
from __future__ import annotations

import argparse

from core.postgres import get_pool


def _list() -> None:
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT ticker_corto, ric FROM mercado.cedears "
            "WHERE ric IS NOT NULL AND ric <> '' ORDER BY ticker_corto"
        )
        rows = cur.fetchall()
    if not rows:
        print("Sin RICs cargados todavía.")
        return
    print(f"{len(rows)} CEDEAR(s) con RIC:")
    for corto, ric in rows:
        print(f"  {corto:<8} → {ric}")


def _set(ticker_corto: str, ric: str | None) -> None:
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE mercado.cedears SET ric = %s WHERE ticker_corto = %s",
            (ric, ticker_corto.upper()),
        )
        n = cur.rowcount
    if n == 0:
        print(f"⚠ No encontré ningún CEDEAR con ticker_corto='{ticker_corto.upper()}' "
              "en mercado.cedears. ¿Está bien escrito? ¿Existe en el master?")
        return
    accion = "borrado" if ric is None else f"seteado a '{ric}'"
    print(f"✓ RIC {accion} en {n} fila(s) de {ticker_corto.upper()}.")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("ticker_corto", nargs="?", help="ticker corto del CEDEAR (RKLB)")
    ap.add_argument("ric", nargs="?", help="RIC de Refinitiv (RKLB.O)")
    ap.add_argument("--clear", action="store_true", help="borra el RIC del ticker")
    ap.add_argument("--list", action="store_true", help="lista los RICs cargados")
    a = ap.parse_args()

    if a.list:
        _list()
        return
    if not a.ticker_corto:
        ap.error("falta ticker_corto (o usá --list)")
    if a.clear:
        _set(a.ticker_corto, None)
        return
    if not a.ric:
        ap.error("falta el RIC (o usá --clear)")
    _set(a.ticker_corto, a.ric)


if __name__ == "__main__":
    main()
