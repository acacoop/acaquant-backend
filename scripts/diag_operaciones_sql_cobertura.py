"""scripts/diag_operaciones_sql_cobertura.py — verifica operaciones.operaciones (SQL)
antes de dropear Mongo CashFlow.Operaciones (migración Operaciones→SQL).

Read-only. Reporta: total, rango de fechas, filas por año, cobertura de
enriquecimiento (mercado/segmento/mep no nulos) y boletos del último día hábil
(para confirmar que el WRITER SQL está escribiendo lo nuevo, no solo el histórico).

    python -m scripts.diag_operaciones_sql_cobertura
"""
from __future__ import annotations

from core.postgres import get_pool


def main() -> None:
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*), min(concertacion), max(concertacion) FROM operaciones")
        total, fmin, fmax = cur.fetchone()
        print("\n=== operaciones.operaciones (SQL) ===")
        print(f"   filas:  {total:,}")
        print(f"   rango:  {fmin} → {fmax}")

        print("\n   filas por año:")
        cur.execute(
            "SELECT extract(year FROM concertacion)::int AS y, count(*) "
            "FROM operaciones WHERE concertacion IS NOT NULL GROUP BY y ORDER BY y")
        for y, n in cur.fetchall():
            print(f"     {y}:  {n:,}")

        cur.execute(
            "SELECT count(*) FILTER (WHERE mercado IS NOT NULL AND mercado <> ''), "
            "count(*) FILTER (WHERE segmento IS NOT NULL AND segmento <> ''), "
            "count(*) FILTER (WHERE mep IS NOT NULL) FROM operaciones")
        mer, seg, mep = cur.fetchone()
        print(f"\n   con mercado:   {mer:,}")
        print(f"   con segmento:  {seg:,}")
        print(f"   con mep:       {mep:,}")

        # Boletos del último día con datos → ¿el writer SQL está escribiendo lo nuevo?
        print(f"\n   boletos del último día ({fmax}):")
        cur.execute("SELECT count(*) FROM operaciones WHERE concertacion = %s", (fmax,))
        print(f"     {cur.fetchone()[0]:,}")
    print("\n→ Comparar 'filas' con lo que tenía Mongo (~487k pre-purga OTC). El rango "
          "debe llegar a HOY/ayer hábil tras correr el writer. Si el último día tiene "
          "boletos, el writer SQL está OK.\n")


if __name__ == "__main__":
    main()
