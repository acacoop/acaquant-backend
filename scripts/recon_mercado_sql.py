"""scripts/recon_mercado_sql.py — reconciliación del espejo de MERCADO Mongo ↔ Postgres.

Valida la paridad del espejo de mercado entre Mongo (Trading.*) y la capa SQL
(schemas mercado.* / macro.* tras la reorg por dominios). 100% READ-ONLY: cuenta
filas/docs por par y un chequeo de frescura donde aplica; NO escribe NADA en
ninguna DB. Sirve para confirmar el dual-write ANTES y DESPUÉS de prender los
flags SNAPSHOT_SQL (motores → market_snapshot live) y MERCADO_SQL_WRITE (jobs →
series_macro, rem, cierres).

El criterio de conteo replica el de jobs/sync_postgres.py (qué doc cuenta para
qué tabla): ej. curvas exige ticker_corto no vacío, bonds_master exige asset.
Tolerancia: market_snapshot y demás tablas live pueden diferir un poco por timing
(el dual-write va por motor, el sync horario es un baseline) → se marca ⚠ dif
pero NO se falla. Exit code 0 SIEMPRE (es diagnóstico).

Uso: python -m scripts.recon_mercado_sql
"""
from __future__ import annotations

from core.mongo import get_mongo_client_read
from core.postgres import connect

# ── series macro: colección Mongo (Trading.<serie>) ↔ macro.series_macro WHERE serie=<>
# Mismo conjunto que jobs/sync_postgres._SERIES_MACRO (no se compara InflacionMensual/
# Interanual si el sync no las sincroniza — pero acá sí entran las 7 del enunciado).
_SERIES_MACRO = (
    "CER", "DOLAR", "BADLAR", "TAMAR",
    "RiesgoPais", "InflacionMensual", "InflacionInteranual",
)

# ── históricos de mercado: colección Mongo ↔ mercado.mercado_hist WHERE coleccion=<>
# (mismo set que jobs/sync_postgres._MERCADO_HIST). El conteo Mongo puede NO ser 1:1
# con el SQL: el grano SQL es (coleccion, fecha, subclave) y un doc Mongo puede traer
# varias subclaves → se muestran ambos y se marca dif SIN fallar (esperable).
_MERCADO_HIST = (
    "BreakevensHistorico", "ForwardsHistorico", "FuturosDLR",
    "Caucion", "FitParams", "FairValueResiduos",
)


def _fmt_row(nombre: str, mongo: int, sql: int, nota: str = "") -> str:
    """Una fila alineada del reporte: nombre, Mongo, SQL y marca ⚠ dif si difieren."""
    flag = "  ⚠ dif" if mongo != sql else ""
    extra = f"   {nota}" if nota else ""
    return f"  {nombre:<26} Mongo={mongo:>10,}  SQL={sql:>10,}{flag}{extra}"


def _sql_count(cur, where_sql: str) -> int:
    """count(*) sobre una tabla SQL (where_sql ya calificado con el schema)."""
    cur.execute(f"SELECT count(*) FROM {where_sql}")
    return int(cur.fetchone()[0])


def main() -> int:
    mdb = get_mongo_client_read()
    trading = mdb["Trading"]

    print("Reconciliación espejo MERCADO — Mongo (Trading.*) ↔ Postgres (mercado/macro.*)")
    print("READ-ONLY. ⚠ dif = los conteos difieren. Tolerar dif chicas en tablas live.\n")

    with connect() as conn, conn.cursor() as cur:
        # ── Núcleo columnar (mercado.*) ──────────────────────────────────────
        print("── Núcleo de mercado (mercado.*) ──")

        # Curvas: solo docs con ticker_corto no vacío (PK del upsert; el sync saltea el resto).
        mg_curvas = trading["Curvas"].count_documents(
            {"ticker_corto": {"$nin": [None, ""]}}
        )
        sql_curvas = _sql_count(cur, "mercado.curvas")
        print(_fmt_row("curvas (ticker_corto≠∅)", mg_curvas, sql_curvas))

        # BondsMaster: solo docs con asset (PK del upsert).
        mg_bm = trading["BondsMaster"].count_documents({"asset": {"$nin": [None, ""]}})
        sql_bm = _sql_count(cur, "mercado.bonds_master")
        print(_fmt_row("bonds_master (asset≠∅)", mg_bm, sql_bm))

        # MarketSnapshot: 1 fila/ticker. LIVE → la dif por timing es esperable.
        mg_ms = trading["MarketSnapshot"].count_documents({"ticker": {"$nin": [None, ""]}})
        sql_ms = _sql_count(cur, "mercado.market_snapshot")
        print(_fmt_row("market_snapshot (live)", mg_ms, sql_ms, "live: dif chica OK"))

        # CanjeCierre: mismo grano ticker+fecha en ambos.
        mg_cj = trading["CanjeCierre"].count_documents({})
        sql_cj = _sql_count(cur, "mercado.canje_cierre")
        print(_fmt_row("canje_cierre", mg_cj, sql_cj))

        # SnapshotsCierre → snapshots_cierre_hist. Grano (fecha, curva, ticker); en Mongo
        # la fecha vive en el campo ts_cierre (no `fecha`). El sync saltea docs sin alguno
        # de los tres → acá se exige que existan para igualar el criterio de conteo.
        mg_sh = trading["SnapshotsCierre"].count_documents({
            "ts_cierre": {"$nin": [None, ""]},
            "curva": {"$nin": [None, ""]},
            "ticker": {"$nin": [None, ""]},
        })
        sql_sh = _sql_count(cur, "mercado.snapshots_cierre_hist")
        print(_fmt_row("snapshots_cierre_hist", mg_sh, sql_sh))

        # ── Series macro (macro.series_macro, tabla larga por `serie`) ────────
        print("\n── Series macro (macro.series_macro) ──")
        for serie in _SERIES_MACRO:
            mg = trading[serie].count_documents({})
            cur.execute("SELECT count(*) FROM macro.series_macro WHERE serie = %s", (serie,))
            sql = int(cur.fetchone()[0])
            print(_fmt_row(serie, mg, sql))

        # ── REM (macro.rem) ──────────────────────────────────────────────────
        print("\n── REM (macro.rem) ──")
        mg_rem = trading["REM"].count_documents({})
        sql_rem = _sql_count(cur, "macro.rem")
        print(_fmt_row("rem", mg_rem, sql_rem))

        # ── Históricos de mercado (mercado.mercado_hist por `coleccion`) ──────
        print("\n── Históricos de mercado (mercado.mercado_hist) ──")
        print("  Nota: el conteo Mongo puede NO ser 1:1 (grano SQL = coleccion+fecha+subclave).")
        for col in _MERCADO_HIST:
            mg = trading[col].count_documents({})
            cur.execute(
                "SELECT count(*) FROM mercado.mercado_hist WHERE coleccion = %s", (col,)
            )
            sql = int(cur.fetchone()[0])
            print(_fmt_row(col, mg, sql))

        # ── Frescura: timestamp más reciente del market_snapshot en cada lado ─
        print("\n── Frescura market_snapshot (updated_at más reciente) ──")
        try:
            doc = trading["MarketSnapshot"].find_one(
                {"updated_at": {"$ne": None}}, sort=[("updated_at", -1)]
            )
            mg_ts = doc.get("updated_at") if doc else None
        except Exception as e:  # diagnóstico: un error de frescura no debe abortar
            mg_ts = f"<err: {type(e).__name__}>"
        cur.execute("SELECT max(updated_at) FROM mercado.market_snapshot")
        sql_ts = cur.fetchone()[0]
        print(f"  Mongo MarketSnapshot.updated_at máx : {mg_ts}")
        print(f"  SQL   market_snapshot.updated_at máx: {sql_ts}")
        print("  (Con SNAPSHOT_SQL ON deberían quedar cerca en horario de mercado.)")

    print("\nOK (read-only, nada escrito).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
