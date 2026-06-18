"""api/services/macro_sql.py — Series macro leyendo Postgres (macro.series_macro).

Espejo SQL-native del subconjunto SQL-backed de `api/services/macro.py`. Las 7
series que viven en `macro.series_macro` (CER, DOLAR, BADLAR, TAMAR, RiesgoPais,
InflacionMensual, InflacionInteranual) se leen de SQL; **todo lo demás delega al
servicio Mongo** (`macro.py`) → byte-idéntico por construcción:

- mep / ccl / canje  → Valuaciones.Dolar (live, no es series_macro)
- series por ticker  → TimeSales (stream, no migrado)
- caucion_ars/usd    → Trading.Caucion (vive en mercado_hist, no en series_macro)
- variables bloqueadas / desconocidas → mismo stub que Mongo

Dual-run por flag `MACRO_SQL` (+ `?_engine` override). Gate de paridad:
`scripts/compare_macro_sql_vs_mongo.py`. Como el write-side está en paridad exacta
(recon 2026-06-18), SQL vacío ⟺ Mongo vacío → delegar en ese caso es seguro.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from psycopg.rows import dict_row

from api.cache import cached
from api.services import macro as _mongo
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


@cached(ttl=120)
def obtener_serie_macro(variable: str, ventana_dias: int = 90) -> dict:
    """actual + serie + stats. SQL para las 7 series de series_macro; el resto
    (mep/ccl/canje, ticker, caución, bloqueadas) delega al path Mongo."""
    var = (variable or "").strip().lower() if variable else ""
    if var in _VAR_TO_SERIE:
        serie = _fetch_serie_macro_sql(_VAR_TO_SERIE[var], ventana_dias)
        if serie:
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
        # SQL vacío ⟺ Mongo vacío (write-side en paridad) → Mongo arma el stub.
    return _mongo.obtener_serie_macro(variable=variable, ventana_dias=ventana_dias)


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
