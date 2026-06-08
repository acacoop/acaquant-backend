"""scripts/diag_mep_sql.py — verifica que la columna `mep` aterrizó bien en la
tabla Postgres `operaciones` tras el sync (migrate_pg_operaciones_mep + sync_postgres --full).

POR QUÉ: antes de dolarizar el volumen de OPERACIONES desde SQL hay que confirmar
(no asumir, REGLA #2) que el `mep` por-boleto realmente se copió. Mide, read-only,
la cobertura de `mep`>0 partido por moneda — tiene que dar igual que el diag de
Mongo (diag_dolarizar_cobertura): ARS ~87.7%, USD ~83%.

Read-only. Correr en el Droplet:
    python -m scripts.diag_mep_sql
"""
from __future__ import annotations

from core.postgres import connect


def main() -> int:
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM operaciones")
        total = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM operaciones WHERE mep IS NOT NULL")
        con_mep = cur.fetchone()[0]
        cur.execute(
            "SELECT COALESCE(moneda, '(sin moneda)') AS moneda, "
            "count(*) AS total, "
            "count(*) FILTER (WHERE mep > 0) AS mep_ok "
            "FROM operaciones GROUP BY moneda ORDER BY total DESC"
        )
        filas = cur.fetchall()

    print("=" * 60)
    print(f"Postgres.operaciones  (total: {total:,})")
    print(f"  con `mep` NOT NULL: {con_mep:,}")
    print("=" * 60)
    print(f"  {'moneda':<14}{'total':>10}{'mep>0':>10}{'sin mep':>10}{'% ok':>8}")
    print("  " + "-" * 50)
    for moneda, t, ok in filas:
        sin = t - ok
        pct = (ok / t * 100) if t else 0
        print(f"  {str(moneda):<14}{t:>10,}{ok:>10,}{sin:>10,}{pct:>7.1f}%")
    print()
    print("Comparar con diag_dolarizar_cobertura (Mongo): ARS ~87.7%, USD ~83%.")
    print("Si coincide → el backfill de mep está OK → se puede dolarizar desde SQL.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
