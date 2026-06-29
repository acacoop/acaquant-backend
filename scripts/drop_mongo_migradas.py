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

    # ── WAVE 2-5 (2026-06-28): writers SQL-native + readers SQL + sync neutralizado ──
    # PROTOCOLO: correr el DRY-RUN, hacer SMOKE de cada dominio EN RUEDA el lunes
    # (panel con data viva), y recién entonces --apply. Las gateadas van comentadas abajo.

    # Opciones (motor options.py + volatilidad_ggal SQL-native; sync_options_vr/metadata no-op)
    ("Opciones", "Data",            "options.py → mercado.options_data (append_native); OPCIONES_SQL."),
    ("Opciones", "OptionsSnapshot", "options.py → options_snapshot (write_native + purge SQL)."),
    ("Opciones", "Metadata",        "options/manager.options/update_tasa/volatilidad_ggal → options_metadata (merge_jsonb)."),
    ("Opciones", "VR-GGal",         "volatilidad_ggal.py → options_vr (replace_native); sync_options_vr no-op."),

    # Renta fija derivada (breakevens/forwards/caucion/futuros_dlr SQL-native; syncs no-op)
    ("Trading", "BreakevensLive",      "breakevens.py → mercado_hist (write_hist); MERCADO_HIST_SQL."),
    ("Trading", "BreakevensHistorico", "breakevens.py → mercado_hist coleccion=BreakevensHistorico."),
    ("Trading", "ForwardsLive",        "forwards.py → mercado_hist; MERCADO_HIST_SQL."),
    ("Trading", "ForwardsHistorico",   "forwards.py → mercado_hist coleccion=ForwardsHistorico."),
    ("Trading", "ForwardsZscore",      "forwards_zscore.py → forwards_zscore (lee fuente de mercado_hist); sync no-op."),
    ("Trading", "CaucionSnapshot",     "caucion.py → caucion_snapshot (write_native); sync_caucion_snapshot no-op."),
    ("Trading", "Caucion",             "caucion.py → mercado_hist coleccion=Caucion."),
    ("Trading", "FuturosDLRSnapshot",  "futuros_dlr.py → futuros_dlr_snapshot; sync no-op."),
    ("Trading", "FuturosDLR",          "futuros_dlr.py → mercado_hist coleccion=FuturosDLR."),

    # Agro (motores + carga manual SQL-native; 4 syncs no-op)
    ("Trading", "AgroSnapshot",         "motor_agro.py → agro_snapshot; sync no-op; AGRO_SQL."),
    ("Trading", "AgroOpcionesSnapshot", "motor_agro_opciones.py → agro_opciones_snapshot; sync no-op."),
    ("Derivados", "AgroPizarra",        "derivados_agro.set_pizarra → agro_pizarra; sync no-op."),
    ("Derivados", "CamaraCereales",     "camara_cereales.set_camara → camara_cereales; sync no-op."),
    ("Derivados", "AgroPizarraAudit",   "audit append-only sin readers; DROP con el padre."),
    ("Derivados", "CamaraCerealesAudit","audit append-only sin readers; DROP con el padre."),

    # Market (jobs SQL-native con merge_jsonb para anchors; sync_quotes/calendar no-op)
    ("Market", "Quotes",          "market_quotes/anchors → home.market_quotes (merge_jsonb); MARKET_SQL."),
    ("Market", "EconomicCalendar","economic_calendar.py → home.market_calendar; MARKET_SQL."),

    # Negocio / catálogos (writers SQL-native; readers SQL)
    ("CashFlow", "NegocioMovimientos", "jobs/negocio_movimientos.py SQL-native; pnl.py reads dead-path (pnl_sql vivo); _universo_portfolio SQL."),
    ("CashFlow", "TiposOperacion",     "fci_bilateral + operaciones_informes → operaciones.tipos_operacion; sync no-op."),
    ("Clientes", "ActividadMensual",   "jobs/actividad_mensual.py → clientes.actividad_mensual SQL-native; sync no-op."),
    ("News",     "Headlines",          "news_finnhub/ingesta SQL-native; readers (news.py/status/registry) SQL; NEWS_SQL."),

    # PyRofex (writer reconstruido scripts/discovery_pyrofex.py; readers SQL; ya poblado)
    ("Manager", "PyRofexInstruments", "discovery_pyrofex → manager.pyrofex_instruments; 5 readers SQL."),
    ("Manager", "PyRofexDiscovery",   "discovery_pyrofex → manager.pyrofex_discovery (singleton)."),

    # Manager / Auth (cutover SQL-only: writers SQL-native, readers flipeados, 5 syncs no-op).
    # SMOKE: panel Manager → USUARIOS / ROLES / GRUPOS carga; un cambio de rol persiste.
    ("Manager", "Users",      "roles.py SQL-only (upsert/delete/auto_register/touch); list_users SQL; AUTH_SQL/MANAGER_SQL."),
    ("Manager", "RoleMatrix", "set_role_modules SQL-only; lookup matrix SQL (fallback DEFAULT_MATRIX)."),
    ("Manager", "Grupos",     "grupos.py SQL-only (crear/actualizar/eliminar, uuid); listar_grupos SQL."),
    ("Manager", "RoleAudit",  "roles.py::_audit_insert SQL-native; list_audit_sql con MANAGER_SQL."),
    ("Manager", "JobRuns",    "core/job_runs.py JobRunLogger SQL-only; informe_salud/diagnostico leen SQL."),

    # Curvas (master RF) — SQL-native. GATE: correr scripts.compare_curvas_sql_vs_mongo
    # (paridad ticker_corto + flujos). Si ✅, descomentar y dropear.
    # ("Trading", "Curvas",     "ons/bonos_admin SQL-native; ~20 readers + loader SQL; sync_curvas no-op."),
    # ("Trading", "BondsMaster","consolidada en mercado.curvas; muerta."),

    # ── Gateadas por BACKFILL: correr `python -m scripts.backfill_gated_decomiso --apply`
    #    PRIMERO (poblar la historia congelada en Mongo), verificar, y recién ahí descomentar.
    #    Sin el backfill se pierde historia (z_temporal NULL ~20 ruedas / set ignorado de ONs).
    # ("Trading", "FitParams",          "fair_value.py SQL-native; historia → mercado.fit_params (backfill_gated_decomiso)."),
    # ("Trading", "FairValueResiduos",  "fair_value.py SQL-native; historia → mercado.fair_value_residuos (backfill_gated_decomiso)."),
    # ("Trading", "OnsIgnoradas",       "ons.py SQL-native; set → mercado.ons_ignoradas (backfill_gated_decomiso)."),

    # ── NO incluir todavía (otras gateadas) ──────────────────────────────────────────
    # Trading.PortfolioSnapshot — confirmar que pnl.py PortfolioSnapshot reads son dead-path (PNL_SQL).
    # CashFlow.{Accionistas,VolumenMercadoAgro} — sync backstop activo (carga manual aún a Mongo).
    # Operaciones.* (órdenes) — flipear ORDENES_SQL en rueda tras comparador de paridad PRIMERO.
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
