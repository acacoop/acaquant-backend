"""scripts/sql_vacuum_tune.py — bloat + autovacuum de las tablas de ALTA ROTACIÓN.

CONTEXTO (medido 2026-06-29): `mercado.market_snapshot` (~3000 filas vivas) hace
Seq Scan con cost 54746 ≈ 440 MB → BLOAT. Los motores la upsertean cada ~1s; en
Postgres cada UPDATE deja una tupla muerta y el autovacuum default no da abasto en
tablas tan calientes → se acumulan páginas muertas y el seq scan las lee TODAS.
(Mongo updateaba in-place; este costo no existía.) Esto hace lenta a get_renta_fija
y a TODA vista que lee market_snapshot.

Tablas afectadas (1 fila/ticker, upsert por tick — propensas a bloat):
  mercado.market_snapshot, cedears_snapshot, adr_snapshot, agro_snapshot,
  agro_opciones_snapshot, futuros_dlr_snapshot, caucion_snapshot,
  options_snapshot, snapshots_sinteticos, home.market_quotes, valuaciones.portfolio_snapshot

MODOS:
  python -m scripts.sql_vacuum_tune --report       # bloat actual (read-only)
  python -m scripts.sql_vacuum_tune --tune         # autovacuum agresivo + fillfactor (online, seguro)
  python -m scripts.sql_vacuum_tune --vacuum       # VACUUM (ANALYZE) — online, no bloquea
  python -m scripts.sql_vacuum_tune --vacuum-full  # VACUUM FULL — COMPACTA pero BLOQUEA la tabla
                                                   #   (1-2s c/u) → correr FUERA DE RUEDA / motores apagados

Plan recomendado (una vez): --report → --tune → (fuera de rueda) --vacuum-full → --report.
El --tune deja el autovacuum/fillfactor para que NO se vuelva a bloatear.
"""
from __future__ import annotations

import sys

# Tablas calientes (schema.tabla). Upsert por tick → bloat.
_HOT = [
    "mercado.market_snapshot",
    "mercado.cedears_snapshot",
    "mercado.adr_snapshot",
    "mercado.agro_snapshot",
    "mercado.agro_opciones_snapshot",
    "mercado.futuros_dlr_snapshot",
    "mercado.caucion_snapshot",
    "mercado.options_snapshot",
    "mercado.snapshots_sinteticos",
    "home.market_quotes",
    "valuaciones.portfolio_snapshot",
]


def _conn():
    # autocommit: VACUUM no corre dentro de una transacción.
    import psycopg

    from core.postgres import _SEARCH_PATH, get_postgres_uri
    return psycopg.connect(get_postgres_uri(), autocommit=True,
                           options=f"-c search_path={_SEARCH_PATH}")


def _report() -> None:
    print("\n=== BLOAT / autovacuum (pg_stat_user_tables) ===")
    print(f"{'tabla':<40}{'vivas':>9}{'muertas':>9}{'%dead':>7}{'tamaño':>10}  últ.autovacuum")
    with _conn() as c, c.cursor() as cur:
        for full in _HOT:
            sch, tbl = full.split(".")
            cur.execute(
                "SELECT n_live_tup, n_dead_tup, last_autovacuum, "
                "pg_size_pretty(pg_total_relation_size(%s::regclass)) "
                "FROM pg_stat_user_tables WHERE schemaname=%s AND relname=%s",
                (full, sch, tbl),
            )
            r = cur.fetchone()
            if not r:
                print(f"{full:<40}  (sin stats / tabla inexistente)")
                continue
            live, dead, last_av, size = r
            pct = round(100 * dead / (live + dead), 1) if (live or dead) else 0
            flag = " 🔴" if pct > 20 else ("" if pct < 5 else " 🟡")
            av = last_av.strftime("%m-%d %H:%M") if last_av else "NUNCA"
            print(f"{full:<40}{live or 0:>9}{dead or 0:>9}{pct:>6}%{size:>10}  {av}{flag}")
    print("\n🔴 >20% muertas = bloat: necesita --vacuum-full (1 vez) + --tune (permanente).")


def _tune() -> None:
    """Autovacuum agresivo + fillfactor 80 (deja lugar para HOT updates → menos bloat)."""
    print("\n=== TUNE (online, seguro) — autovacuum agresivo + fillfactor 80 ===")
    with _conn() as c, c.cursor() as cur:
        for full in _HOT:
            try:
                cur.execute(
                    f"ALTER TABLE {full} SET ("
                    "autovacuum_vacuum_scale_factor = 0.02, "   # vacuum al 2% (default 20%)
                    "autovacuum_vacuum_threshold = 50, "
                    "autovacuum_analyze_scale_factor = 0.02, "
                    "fillfactor = 80)"                          # 20% libre → UPDATE HOT (sin bloat de índice)
                )
                print(f"  ✓ {full}")
            except Exception as e:
                print(f"  ✗ {full}: {str(e).splitlines()[0][:80]}")
    print("fillfactor solo aplica a páginas NUEVAS → correr --vacuum-full una vez para compactar lo viejo.")


def _vacuum(full_mode: bool) -> None:
    verbo = "VACUUM (FULL, ANALYZE)" if full_mode else "VACUUM (ANALYZE)"
    if full_mode:
        print("\n⚠️  VACUUM FULL BLOQUEA cada tabla mientras la reescribe (1-2s c/u).")
        print("    Correr FUERA DE RUEDA (motores apagados). Ctrl-C para abortar.\n")
    print(f"=== {verbo} ===")
    with _conn() as c, c.cursor() as cur:
        for full in _HOT:
            try:
                import time
                t0 = time.perf_counter()
                cur.execute(f"{verbo} {full}")
                dt = round((time.perf_counter() - t0) * 1000)
                print(f"  ✓ {full}  ({dt}ms)")
            except Exception as e:
                print(f"  ✗ {full}: {str(e).splitlines()[0][:80]}")


def main() -> int:
    args = set(sys.argv[1:])
    if not args or "--help" in args:
        print(__doc__)
        return 0
    if "--report" in args:
        _report()
    if "--tune" in args:
        _tune()
    if "--vacuum-full" in args:
        _vacuum(full_mode=True)
    elif "--vacuum" in args:
        _vacuum(full_mode=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
