"""scripts/healthcheck_sql_tablas.py — census de las tablas SQL del decomiso.

Segundo check rápido: por cada tabla que reemplazó una colección Mongo, cuenta
filas + frescura (max del campo de timestamp si aplica) y marca 🟢/🟡/🔴.
Read-only. No reemplaza al healthcheck que ejecuta los readers reales
(`scripts.healthcheck_sql`), lo complementa: acá se ve de un vistazo si alguna
tabla quedó VACÍA cuando no debería.

    python -m scripts.healthcheck_sql_tablas
"""
from __future__ import annotations

import sys

from core.postgres import get_pool

# (schema.tabla, ts_expr|None, esperado: 'datos'|'puede_vacio', nota)
TABLAS: list[tuple[str, str | None, str, str]] = [
    # Mercado — RF / derivados
    ("mercado.curvas",                "data->>'updated_at'", "datos", "master RF (~57)"),
    ("mercado.market_snapshot",       "updated_at",          "datos", "estado live (rueda)"),
    ("mercado.mercado_hist",          "fecha",               "datos", "forwards/breakevens/caución/futuros hist"),
    ("mercado.snapshots_cierre_hist", "fecha",               "datos", "cierre histórico por bono"),
    ("mercado.timesales",             "ts",                  "puede_vacio", "tape intradía (vacía off-rueda)"),
    ("mercado.caucion_snapshot",      "data->>'updated_at'", "puede_vacio", "live (rueda)"),
    ("mercado.futuros_dlr_snapshot",  "data->>'updated_at'", "puede_vacio", "live (rueda)"),
    ("mercado.forwards_zscore",       "data->>'updated_at'", "datos", "coef z-score por curva"),
    ("mercado.fit_params",            "ts_cierre",           "puede_vacio", "fair_value (puebla lunes)"),
    ("mercado.fair_value_residuos",   "ts_cierre",           "puede_vacio", "fair_value (puebla lunes)"),
    ("mercado.ons_ignoradas",         None,                  "puede_vacio", "conciliación ONs"),
    # Mercado — RV / agro / opciones
    ("mercado.cedears_snapshot",      "data->>'updated_at'", "puede_vacio", "scanner CEDEARs (rueda)"),
    ("mercado.adr_snapshot",          None,                  "puede_vacio", "ADR USD"),
    ("mercado.precios_acciones",      None,                  "datos", "velas EOD subyacente"),
    ("mercado.cedears_time_sales",    "ts",                  "puede_vacio", "tape RV (vacía off-rueda)"),
    ("mercado.snapshots_sinteticos",  None,                  "datos", "sintéticos histórico"),
    ("mercado.agro_snapshot",         "data->>'updated_at'", "puede_vacio", "agro (rueda agro)"),
    ("mercado.agro_opciones_snapshot","data->>'updated_at'", "puede_vacio", "agro opciones"),
    ("mercado.agro_pizarra",          None,                  "datos", "pizarra agro (manual)"),
    ("mercado.camara_cereales",       None,                  "datos", "cámara (manual)"),
    ("mercado.options_data",          "ts",                  "puede_vacio", "tick opciones (rueda)"),
    ("mercado.options_snapshot",      "updated_at",          "puede_vacio", "grid opciones (rueda)"),
    ("mercado.options_metadata",      None,                  "datos", "config tasa + expiries + vr"),
    ("mercado.options_vr",            None,                  "datos", "VR-GGal serie"),
    ("mercado.volumen_mercado_agro",  None,                  "datos", "denominador share agro"),
    # Macro
    ("macro.series_macro",            "fecha",               "datos", "CER/DOLAR/BADLAR/TAMAR/RP/Inflación"),
    ("macro.rem",                     None,                  "datos", "REM consenso IPC"),
    ("macro.uva",                     "fecha",               "datos", "UVA"),
    # Valuaciones
    ("valuaciones.dolar",             "timestamp",           "datos", "MEP histórico"),
    ("valuaciones.dolar_snapshot",    "ts",                  "puede_vacio", "MEP/CCL live (rueda)"),
    ("valuaciones.dolar_oficial_live","updated_at",          "puede_vacio", "MAE oficial (PC oficina)"),
    ("valuaciones.portfolio_snapshot","updated_at",          "puede_vacio", "precio live tenencia (rueda)"),
    ("valuaciones.consolidado",       None,                  "datos", "cache consolidado por cuenta"),
    ("valuaciones.pnl_totales_cache", None,                  "datos", "cache PnL todas las cuentas"),
    # Operaciones / negocio
    ("operaciones.operaciones",       "concertacion",        "datos", "movimientos (volumen/arancel)"),
    ("operaciones.negocio_movimientos","fecha",              "datos", "boletos (cost-basis PnL)"),
    ("operaciones.acreencias",        None,                  "datos", "proyección de cobros"),
    ("operaciones.movimientos",       None,                  "datos", "flujos depósitos/extracciones"),
    ("operaciones.tipos_operacion",   None,                  "datos", "catálogo"),
    # Clientes / portafolio
    ("clientes.comitentes",           None,                  "datos", "segmentación/operador"),
    ("clientes.contrapartes",         None,                  "datos", "FCI/sociedades gerentes"),
    ("clientes.accionistas",          None,                  "datos", "set accionistas"),
    ("clientes.actividad_mensual",    None,                  "datos", "snapshot operador/segmento"),
    ("portafolio.tenencia",           None,                  "datos", "tenencias (AuM)"),
    ("portafolio.assets",             None,                  "datos", "catálogo de títulos"),
    # Home / manager
    ("home.market_quotes",            None,                  "datos", "watchlist HOME"),
    ("home.market_calendar",          None,                  "datos", "calendario económico"),
    ("home.news_headlines",           "fecha_publicacion",   "datos", "noticias"),
    ("manager.manager_users",         None,                  "datos", "usuarios RBAC"),
    ("manager.role_matrix",           None,                  "datos", "matriz de roles"),
    ("manager.grupos",                None,                  "puede_vacio", "grupos de scope"),
    ("manager.role_audit",            None,                  "puede_vacio", "audit de roles"),
    ("manager.job_runs",              "finished_at",         "datos", "historial de jobs"),
    ("manager.pyrofex_instruments",   None,                  "datos", "instrumentos operables"),
    ("manager.health_reports",        "ts",                  "puede_vacio", "informe de salud"),
    ("manager.watchdog_alertas",      None,                  "puede_vacio", "cooldown watchdog"),
]


def main() -> int:
    rojos = 0
    print(f"{'TABLA':<36} {'FILAS':>9}  {'FRESCURA':<22} ESTADO  NOTA")
    print("─" * 110)
    with get_pool().connection() as conn:
        for tabla, ts_expr, esperado, nota in TABLAS:
            try:
                with conn.cursor() as cur:
                    cur.execute(f"SELECT count(*) FROM {tabla}")
                    n = cur.fetchone()[0]
                    fresc = ""
                    if ts_expr:
                        cur.execute(f"SELECT max(({ts_expr})::timestamptz) FROM {tabla}")
                        r = cur.fetchone()
                        fresc = str(r[0])[:19] if r and r[0] else "—"
            except Exception as e:
                conn.rollback()
                print(f"{tabla:<36} {'ERROR':>9}  {'':<22} 🔴      {str(e).splitlines()[0][:40]}")
                rojos += 1
                continue
            if n == 0 and esperado == "datos":
                estado = "🔴"
                rojos += 1
            elif n == 0:
                estado = "🟡"
            else:
                estado = "🟢"
            print(f"{tabla:<36} {n:>9,}  {fresc:<22} {estado}      {nota}")

    print("─" * 110)
    if rojos:
        print(f"🔴 {rojos} tabla(s) VACÍA(s) o con ERROR que deberían tener datos — revisar ARRIBA.")
    else:
        print("🟢 Todas las tablas que deberían tener datos los tienen.")
    print("(🟡 = vacía esperable off-rueda/feature lunes. No reemplaza a scripts.healthcheck_sql,"
          " que ejecuta los readers reales.)")
    return 1 if rojos else 0


if __name__ == "__main__":
    sys.exit(main())
