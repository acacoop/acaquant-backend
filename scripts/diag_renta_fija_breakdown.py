"""Desglose de latencia de get_renta_fija (SQL) — read-only.

El diag de pantalla marcó get_renta_fija como el cuello de botella (~668ms en
frío). Esta función hace 4 cosas; acá medimos cada una por separado para saber
QUÉ optimizar (no asumir). Especial foco: la columna `book` (order book JSONB
depth-5) que se trae para los ~301 instrumentos y que la vista /renta-fija NO usa.

Uso (Droplet, raíz): python -m scripts.diag_renta_fija_breakdown
"""
from __future__ import annotations

import time

from psycopg.rows import dict_row

from api.services.renta_fija_sql import _METRIC_COLS, _bonos_cer_fijados
from core.postgres import get_pool


def _t(label: str, fn) -> None:
    t0 = time.perf_counter()
    res = fn()
    ms = (time.perf_counter() - t0) * 1000
    n = len(res) if isinstance(res, (list, dict)) else res
    print(f"{label:42} {ms:8.0f} ms   (n={n})")


def _select(cols_with_book: bool) -> list:
    cols = ", ".join(c for c, _ in _METRIC_COLS)
    book = "book, " if cols_with_book else ""
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(f"SELECT ticker, {book}{cols} FROM mercado.market_snapshot")
        return cur.fetchall()


def main() -> None:
    print("Desglose get_renta_fija (cada uno en frío):\n")
    _t("SELECT market_snapshot CON book (actual)", lambda: _select(True))
    _t("SELECT market_snapshot SIN book", lambda: _select(False))
    _t("_bonos_cer_fijados() [Mongo DiasHabiles + SQL]", _bonos_cer_fijados)

    def _mep():
        from api.services.macro import get_ultimo_mep
        return [get_ultimo_mep()]
    _t("get_ultimo_mep() [Mongo]", _mep)

    def _flujos():
        with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute("SELECT ticker, flujo_vencimiento FROM mercado.curvas "
                        "WHERE curva = 'tasa_fija'")
            return cur.fetchall()
    _t("SELECT curvas flujo_vencimiento (tasa_fija)", _flujos)

    print("\nEl delta CON book vs SIN book = lo que se ahorra al no traer el "
          "order book\nque la vista no consume.")


if __name__ == "__main__":
    main()
