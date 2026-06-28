"""scripts/drop_mongo_migradas.py — DROP de colecciones Mongo ya cutover-eadas a SQL.

LIBRO DE DROPS de la migración Mongo→SQL. Cada entrada de DROPS es una colección que
cumple las 3 condiciones del cutover (verificadas a mano antes de agregarla):
  1. su WRITER ya escribe Postgres-native (o nadie la escribe → huérfana),
  2. NO la lee `jobs/sync_postgres.py` (no rompe el espejo),
  3. la API ya lee la tabla SQL equivalente (flag en 🟢).

Default DRY-RUN: imprime qué dropearía + el conteo actual. Con `--apply` dropea de verdad.
Idempotente: una colección ya dropeada se saltea. Read-only salvo `--apply`.

    python -m scripts.drop_mongo_migradas            # dry-run (no toca nada)
    python -m scripts.drop_mongo_migradas --apply    # dropea de verdad

Irreversible con --apply. Correr fuera de rueda. Borrar del repo cuando la migración cierre.
"""
from __future__ import annotations

import sys

# (db, coll, motivo) — agregar SOLO tras verificar las 3 condiciones del cutover.
DROPS: list[tuple[str, str, str]] = [
    ("Trading", "CedearsSnapshot",
     "motor_cedears escribe mercado.cedears_snapshot SQL-native; nadie escribe Mongo; "
     "no la toca sync_postgres; SCANNER_SQL=1 lee SQL. Huérfana."),
    ("Valuaciones", "ConsolidadoCuentas",
     "jobs/consolidado_cuentas.py escribe valuaciones.consolidado SQL-native (cutover "
     "2026-06-26); fuera de sync_postgres; VALUACIONES_SQL=1 lee SQL. Huérfana."),
    ("Valuaciones", "PnLTotalesCache",
     "jobs/pnl_totales_precompute.py escribe valuaciones.pnl_totales_cache SQL-native "
     "(cutover 2026-06-26); fuera de sync_postgres; requiere PNL_TOTALES_SQL=1 (leer SQL). Huérfana."),
    ("Trading", "ONSnapshot",
     "Legacy: los ONs se sirven desde Trading.Curvas (curva=on_*). 0 lectores y 0 escritores "
     "en el código (grep), última escritura 2026-04-13. Huérfana pura."),

    # ── Decomiso 2026-06-28: writers Y readers ya SQL-native (sin Mongo) ──────────
    ("Valuaciones", "DolarOficialLive",
     "core/dolar_oficial.py escribe/lee valuaciones.dolar_oficial_live SQL; backfilleada; "
     "sin sync. /ingest persiste SQL."),
    ("Valuaciones", "DolarSnapshot",
     "engines/dolares.py escribe valuaciones.dolar_snapshot SQL; readers (macro/argy/scanner/"
     "curvas) leen SQL vía core/dolar_sql; sin sync."),
    ("Valuaciones", "Dolar",
     "engines/dolar_mep.py escribe valuaciones.dolar SQL; backfilleada (2003 filas); "
     "sync_dolar ELIMINADO; readers SQL (core/dolar_sql, _mep, carry_trade)."),
    ("Trading", "UVA",
     "macro.get_ultimo_uva lee macro.uva SQL; correr scripts.backfill_uva_sql --apply ANTES. "
     "Carga nueva vía scripts.insertar_uva."),
    ("Manager", "WatchdogAlertas",
     "jobs/watchdog.py escribe/lee manager.watchdog_alertas SQL; sin sync; sin otros readers."),
    ("Manager", "HealthReports",
     "jobs/informe_salud.py escribe/lee manager.health_reports SQL; sin sync; sin otros readers."),
    ("Trading", "SnapshotsSinteticos",
     "jobs/snapshot_sinteticos.py escribe mercado.snapshots_sinteticos SQL; 0 readers; sin sync."),
    ("Manager", "PortfolioSnapshotLog",
     "engines/portfolio_snapshot.py escribe manager.portfolio_snapshot_log SQL; audit, 0 readers."),
    ("Trading", "AdhocSubscriptions",
     "core/adhoc_subscriptions.py escribe/lee mercado.adhoc_subscriptions SQL; sin sync; "
     "TTL por read-filter + prune cron."),
    ("Manager", "AranceelesJobRuns",
     "jobs/aranceles.py + manager/aunesa.py escriben/leen manager.aranceles_job_runs SQL; sin sync."),
    ("Trading", "CedearsTimeSales",
     "engines/motor_cedears.py escribe mercado.cedears_time_sales SQL; readers (scanner/"
     "day_trading) SQL; intradía (se vacía al cierre); sin sync."),
    # NO incluida: Trading.PortfolioSnapshot — pnl.py (rama legacy gateada por PNL_SQL) aún
    # lee Mongo. Verificar el lunes con PNL_SQL=1 antes de dropear.
]


def main() -> int:
    apply = "--apply" in sys.argv
    from core.mongo import get_mongo_client

    cli = get_mongo_client()
    modo = "APPLY (DROP REAL)" if apply else "DRY-RUN (no toca nada)"
    print(f"drop_mongo_migradas — modo {modo}\n")

    existentes = {db: set(cli[db].list_collection_names()) for db in {d for d, _, _ in DROPS}}
    dropeadas = 0
    for db, coll, motivo in DROPS:
        if coll not in existentes.get(db, set()):
            print(f"  ⏭  {db}.{coll:<24} ya no existe (idempotente)")
            continue
        try:
            n = cli[db][coll].estimated_document_count()
        except Exception:
            n = -1
        if apply:
            cli[db].drop_collection(coll)
            print(f"  🗑  {db}.{coll:<24} DROPEADA (~{n:,} docs) — {motivo}")
            dropeadas += 1
        else:
            print(f"  •  {db}.{coll:<24} se dropearía (~{n:,} docs) — {motivo}")

    print()
    if apply:
        print(f"Listo: {dropeadas} colección(es) dropeada(s).")
    else:
        print("DRY-RUN. Nada se tocó. Re-correr con --apply para dropear.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
