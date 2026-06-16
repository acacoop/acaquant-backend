"""scripts/diag_negocio_sql_cobertura.py — verifica que operaciones.negocio_movimientos
(SQL) tenga TODA la historia tras dropear Mongo CashFlow.NegocioMovimientos.

Read-only. Reporta: total de filas, rango de fechas, filas por año, y cobertura de
arancel (cuántos boletos tienen arancel cargado).

    python -m scripts.diag_negocio_sql_cobertura
"""
from __future__ import annotations

from core.postgres import get_pool


def main() -> None:
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*), min(fecha), max(fecha) FROM negocio_movimientos")
        total, fmin, fmax = cur.fetchone()
        print("\n=== operaciones.negocio_movimientos ===")
        print(f"   filas:  {total:,}")
        print(f"   rango:  {fmin} → {fmax}")

        print("\n   filas por año:")
        cur.execute(
            "SELECT extract(year FROM fecha)::int AS y, count(*) "
            "FROM negocio_movimientos GROUP BY y ORDER BY y")
        for y, n in cur.fetchall():
            print(f"     {y}:  {n:,}")

        cur.execute(
            "SELECT count(*) FILTER (WHERE arancel IS NOT NULL AND arancel > 0), "
            "count(*) FILTER (WHERE arancel IS NULL OR arancel <= 0) "
            "FROM negocio_movimientos")
        con, sin = cur.fetchone()
        print(f"\n   arancel cargado:  {con:,}")
        print(f"   arancel vacío:    {sin:,}")
    print("\n→ Comparar 'filas' con los ~341.242 que tenía Mongo. Si coincide, historia OK.\n")


if __name__ == "__main__":
    main()
