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

from pymongo.errors import PyMongoError

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


def _fmt_row(nombre: str, mongo, sql, nota: str = "") -> str:
    """Una fila alineada del reporte: nombre, Mongo, SQL y marca ⚠ dif si difieren.
    `mongo`/`sql` pueden ser None (None = no se pudo medir ese lado → 'n/d', sin dif)."""
    def _c(v):
        return f"{v:>10,}" if isinstance(v, int) else f"{'n/d':>10}"
    flag = "  ⚠ dif" if (isinstance(mongo, int) and isinstance(sql, int) and mongo != sql) else ""
    extra = f"   {nota}" if nota else ""
    return f"  {nombre:<26} Mongo={_c(mongo)}  SQL={_c(sql)}{flag}{extra}"


def _sql_count(cur, where_sql: str) -> int:
    """count(*) sobre una tabla SQL (where_sql ya calificado con el schema)."""
    cur.execute(f"SELECT count(*) FROM {where_sql}")
    return int(cur.fetchone()[0])


def _mg_count(coll, filt: dict):
    """count_documents AISLADO: si Mongo no responde, devuelve None (no rompe el recon)."""
    try:
        return coll.count_documents(filt)
    except PyMongoError:
        return None


def _mongo_vivo(mdb) -> bool:
    """Ping rápido a Mongo. Si Atlas no responde, lo decimos claro y no intentamos
    20 counts que timeoutean de a uno (cada uno tarda serverSelectionTimeoutMS)."""
    try:
        mdb.client.admin.command("ping")
        return True
    except PyMongoError as e:
        print(f"\n⚠ Mongo (Atlas) no responde: {type(e).__name__}: "
              f"{str(e).splitlines()[0][:160]}")
        print("  El lado SQL sí está accesible. Reintentá el recon cuando Atlas vuelva")
        print("  (suele ser un blip transitorio; los motores usan la misma conexión).")
        return False


def main() -> int:
    mdb = get_mongo_client_read()
    trading = mdb["Trading"]

    print("Reconciliación espejo MERCADO — Mongo (Trading.*) ↔ Postgres (mercado/macro.*)")
    print("READ-ONLY. ⚠ dif = los conteos difieren. Tolerar dif chicas en tablas live.\n")

    mongo_ok = _mongo_vivo(mdb)  # si Atlas no responde, lo medimos como n/d (no timeouts en cadena)

    with connect() as conn, conn.cursor() as cur:
        # ── Núcleo columnar (mercado.*) ──────────────────────────────────────
        print("\n── Núcleo de mercado (mercado.*) ──")

        # Curvas: solo docs con ticker_corto no vacío (PK del upsert; el sync saltea el resto).
        mg_curvas = _mg_count(trading["Curvas"], {"ticker_corto": {"$nin": [None, ""]}}) if mongo_ok else None
        print(_fmt_row("curvas (ticker_corto≠∅)", mg_curvas, _sql_count(cur, "mercado.curvas")))

        # BondsMaster: solo docs con asset (PK del upsert).
        mg_bm = _mg_count(trading["BondsMaster"], {"asset": {"$nin": [None, ""]}}) if mongo_ok else None
        print(_fmt_row("bonds_master (asset≠∅)", mg_bm, _sql_count(cur, "mercado.bonds_master")))

        # MarketSnapshot: 1 fila/ticker. LIVE → la dif por timing es esperable.
        mg_ms = _mg_count(trading["MarketSnapshot"], {"ticker": {"$nin": [None, ""]}}) if mongo_ok else None
        print(_fmt_row("market_snapshot (live)", mg_ms,
                       _sql_count(cur, "mercado.market_snapshot"), "live: dif chica OK"))

        # CanjeCierre: mismo grano ticker+fecha en ambos.
        mg_cj = _mg_count(trading["CanjeCierre"], {}) if mongo_ok else None
        print(_fmt_row("canje_cierre", mg_cj, _sql_count(cur, "mercado.canje_cierre")))

        # SnapshotsCierre → snapshots_cierre_hist. Grano (fecha, curva, ticker); en Mongo
        # la fecha vive en el campo ts_cierre (no `fecha`). El sync saltea docs sin alguno
        # de los tres → acá se exige que existan para igualar el criterio de conteo.
        mg_sh = _mg_count(trading["SnapshotsCierre"], {
            "ts_cierre": {"$nin": [None, ""]},
            "curva": {"$nin": [None, ""]},
            "ticker": {"$nin": [None, ""]},
        }) if mongo_ok else None
        print(_fmt_row("snapshots_cierre_hist", mg_sh, _sql_count(cur, "mercado.snapshots_cierre_hist")))

        # ── Series macro (macro.series_macro, tabla larga por `serie`) ────────
        print("\n── Series macro (macro.series_macro) ──")
        for serie in _SERIES_MACRO:
            mg = _mg_count(trading[serie], {}) if mongo_ok else None
            cur.execute("SELECT count(*) FROM macro.series_macro WHERE serie = %s", (serie,))
            print(_fmt_row(serie, mg, int(cur.fetchone()[0])))

        # ── REM (macro.rem) ──────────────────────────────────────────────────
        print("\n── REM (macro.rem) ──")
        mg_rem = _mg_count(trading["REM"], {}) if mongo_ok else None
        print(_fmt_row("rem", mg_rem, _sql_count(cur, "macro.rem")))

        # ── Históricos de mercado (mercado.mercado_hist por `coleccion`) ──────
        print("\n── Históricos de mercado (mercado.mercado_hist) ──")
        print("  Nota: el conteo Mongo puede NO ser 1:1 (grano SQL = coleccion+fecha+subclave).")
        for col in _MERCADO_HIST:
            mg = _mg_count(trading[col], {}) if mongo_ok else None
            cur.execute("SELECT count(*) FROM mercado.mercado_hist WHERE coleccion = %s", (col,))
            print(_fmt_row(col, mg, int(cur.fetchone()[0])))

        # ── Frescura: timestamp más reciente del market_snapshot en cada lado ─
        print("\n── Frescura market_snapshot (updated_at más reciente) ──")
        mg_ts = None
        if mongo_ok:
            try:
                doc = trading["MarketSnapshot"].find_one(
                    {"updated_at": {"$ne": None}}, sort=[("updated_at", -1)]
                )
                mg_ts = doc.get("updated_at") if doc else None
            except PyMongoError as e:  # diagnóstico: un error de frescura no debe abortar
                mg_ts = f"<err: {type(e).__name__}>"
        cur.execute("SELECT max(updated_at) FROM mercado.market_snapshot")
        sql_ts = cur.fetchone()[0]
        print(f"  Mongo MarketSnapshot.updated_at máx : {mg_ts if mongo_ok else 'n/d (Atlas no respondió)'}")
        print(f"  SQL   market_snapshot.updated_at máx: {sql_ts}")
        print("  (Con SNAPSHOT_SQL ON deberían quedar cerca en horario de mercado.)")

    if not mongo_ok:
        print("\n⚠ Mongo no respondió → la columna Mongo quedó en 'n/d'. Reintentá para comparar.")
    print("\nOK (read-only, nada escrito).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
