"""api/services/macro_sql.py — Series macro 100% SQL (decomiso Mongo).

Implementación única (ya no hay gemelo Mongo; `macro.py` delega acá):
- 7 series de `macro.series_macro` (CER, DOLAR, BADLAR, TAMAR, RiesgoPais,
  InflacionMensual, InflacionInteranual).
- mep / ccl / canje  → `valuaciones.dolar` (vía core.dolar_sql).
- caucion_ars/usd    → cierre histórico en `mercado.mercado_hist` (mercado_hist_sql).
- variables bloqueadas / desconocidas → stub `sin_datos`.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from psycopg.rows import dict_row

from api.cache import cached
from core.postgres import get_pool
from quant.stats import cambio_pct, compute_stats

# variable (lower) → `serie` en macro.series_macro. Derivado de macro._MACROS:
# solo las entradas Trading.<col> cuya col colapsa en series_macro (sin extra_filter).
_VAR_TO_SERIE: dict[str, str] = {
    "tamar":           "TAMAR",
    "cer":             "CER",
    "dolar":           "DOLAR",
    "badlar":          "BADLAR",
    "riesgo_pais":     "RiesgoPais",
    "ipc":             "InflacionMensual",
    "ipc_interanual":  "InflacionInteranual",
    "dolar_oficial":   "DOLAR",
    "dolar_mayorista": "DOLAR",
}


def _q(sql: str, params: tuple) -> list[dict]:
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def _serie_cruda(serie: str, desde: str | None, hasta: str | None) -> list[dict]:
    """[{fecha:'YYYY-MM-DD', valor:float}] asc — espejo de macro._query_serie.
    `fecha` sale como texto (to_char) para igualar el string que guarda Mongo."""
    where = ["serie = %s", "valor IS NOT NULL"]
    params: list = [serie]
    if desde:
        where.append("fecha >= %s::date")
        params.append(desde)
    if hasta:
        where.append("fecha <= %s::date")
        params.append(hasta)
    rows = _q(
        "SELECT to_char(fecha, 'YYYY-MM-DD') AS fecha, valor FROM macro.series_macro "
        f"WHERE {' AND '.join(where)} ORDER BY fecha ASC",
        tuple(params),
    )
    return [{"fecha": r["fecha"], "valor": float(r["valor"])} for r in rows]


@cached(ttl=3600)
def get_badlar(desde: str | None = None, hasta: str | None = None) -> list:
    return _serie_cruda("BADLAR", desde, hasta)


@cached(ttl=3600)
def get_cer(desde: str | None = None, hasta: str | None = None) -> list:
    return _serie_cruda("CER", desde, hasta)


@cached(ttl=3600)
def get_dolar(desde: str | None = None, hasta: str | None = None) -> list:
    return _serie_cruda("DOLAR", desde, hasta)


def _fetch_serie_macro_sql(serie: str, ventana_dias: int) -> list[dict]:
    """Serie [{fecha, valor}] de los últimos `ventana_dias` — espejo de
    macro._fetch_serie_macro para las series ts_tipo='string' (corte por fecha
    string >= corte; comparación lexicográfica == fecha date >= corte.date())."""
    corte = (datetime.now(UTC) - timedelta(days=ventana_dias)).strftime("%Y-%m-%d")
    return _serie_cruda(serie, corte, None)


# Variables del feed dólar (valuaciones.dolar vía dolar_sql) y caución (mercado_hist).
_DOLAR_FEED_VARS = {"mep", "ccl", "canje"}
_CAUCION_VARS = {"caucion_ars": "ARS", "caucion_usd": "USD"}

# Conocidas pero sin fuente de datos (mismo stub que daba el path Mongo).
_BLOQUEADAS: dict[str, str] = {
    "ipim":          "IPIM INDEC no cargado — falta job jobs/inflacion.py",
    "repo":          "stock REPO BCRA no cargado — falta extensión de jobs/bcra.py",
    "rem_inflacion": "REM BCRA no cargado — falta job jobs/rem.py",
    "blue":          "dólar blue sin fuente (dolarapi.com apagado 2026-05-04)",
}


def _stub(variable: str) -> dict:
    return {
        "variable": variable, "actual": None, "clasificacion": "sin_datos",
        "serie": [], "hint": _BLOQUEADAS.get(variable, "data no disponible"),
    }


def _armar(variable: str, serie: list[dict], ventana_dias: int) -> dict:
    """{actual, serie, cambios, stats} desde [{fecha, valor}] asc. Stub si vacío."""
    if not serie:
        return _stub(variable)
    actual = serie[-1]["valor"]
    values = [p["valor"] for p in serie]
    return {
        "variable": variable,
        "actual": actual,
        "fecha_actual": serie[-1]["fecha"],
        "ventana_dias": ventana_dias,
        "serie": serie,
        "cambio_dia_pct":    cambio_pct(values, actual, 1),
        "cambio_semana_pct": cambio_pct(values, actual, 5),
        "cambio_mes_pct":    cambio_pct(values, actual, 21),
        **compute_stats(values, actual),
    }


@cached(ttl=120)
def obtener_serie_macro(variable: str, ventana_dias: int = 90) -> dict:
    """actual + serie + stats — SQL-native (decomiso Mongo).

    7 series de macro.series_macro; mep/ccl/canje de valuaciones.dolar; caución del
    cierre histórico (mercado.mercado_hist); el resto → stub sin_datos."""
    var = (variable or "").strip().lower() if variable else ""

    if var in _VAR_TO_SERIE:
        return _armar(variable, _fetch_serie_macro_sql(_VAR_TO_SERIE[var], ventana_dias), ventana_dias)

    if var in _DOLAR_FEED_VARS:
        from core import dolar_sql
        hasta = datetime.now(UTC)
        desde = hasta - timedelta(days=ventana_dias)
        d = dolar_sql.por_dia(var, desde, hasta)
        serie = [{"fecha": f, "valor": float(v)} for f, v in sorted(d.items()) if v is not None]
        return _armar(variable, serie, ventana_dias)

    if var in _CAUCION_VARS:
        from api.services import mercado_hist_sql
        corte = (datetime.now(UTC) - timedelta(days=ventana_dias)).strftime("%Y-%m-%d")
        serie = []
        for h in mercado_hist_sql.get_historico_caucion(moneda=_CAUCION_VARS[var]):
            v = h.get("tna_cierre") if isinstance(h, dict) else None
            f = str(h.get("fecha"))[:10] if isinstance(h, dict) and h.get("fecha") else None
            if v is not None and f and f >= corte:
                serie.append({"fecha": f, "valor": float(v)})
        serie.sort(key=lambda x: x["fecha"])
        return _armar(variable, serie, ventana_dias)

    return _stub(variable)


def clasificar_nivel(variable: str, ventana_dias: int = 90) -> dict:
    """Wrapper compacto — misma forma que macro.clasificar_nivel pero sobre la
    serie SQL (reusa la lógica de texto del path Mongo vía obtener_serie_macro)."""
    full = obtener_serie_macro(variable=variable, ventana_dias=ventana_dias)
    pct = full.get("percentil_actual")
    z = full.get("zscore_actual")
    contexto_parts: list[str] = []
    if pct is not None:
        contexto_parts.append(f"percentil {pct:.0f}")
    if z is not None:
        contexto_parts.append(f"z-score {z:.2f}")
    contexto_parts.append(f"vs {ventana_dias}d")
    return {
        "variable": variable,
        "actual": full.get("actual"),
        "clasificacion": full.get("clasificacion", "sin_datos"),
        "percentil_actual": pct,
        "zscore_actual": z,
        "ventana_dias": ventana_dias,
        "contexto": "; ".join(contexto_parts),
        "hint": full.get("hint"),
    }
