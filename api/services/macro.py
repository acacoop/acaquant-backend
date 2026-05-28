"""Capa de servicio — series macro y clasificación.

Expone dos tools al asistente:

- `obtener_serie_macro(variable, ventana_dias)` — devuelve actual + serie +
  stats + clasificación.
- `clasificar_nivel(variable, ventana_dias)` — wrapper con output compacto
  (solo clasificación + contexto textual), sin la serie entera.

Variables soportadas:
- Macros puras: tamar, cer, dolar, badlar, mep
- Series por ticker: `<TICKER>.<CAMPO>` — ej "TX26.TEM", "GD30.paridad"
  Campos: TEA, TEM, paridad, duration, price

Variables bloqueadas por data faltante (devuelven stub con hint):
- ccl, canje, ipc, ipim, riesgo_pais, repo, rem_inflacion

"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from api.cache import cached
from api.db import get_db_trading, get_db_valuaciones
from quant.stats import cambio_pct, compute_stats

# ─────────────────────────────────────────────────────────────────────────────
# Series raw de cotizaciones (BCRA + Dólar financiero)
#
# Wrappers simples a Mongo usados por los endpoints `/api/cotizaciones/*`.
# La tool analítica `obtener_serie_macro` (más abajo) hace lo mismo vía
# `_fetch_serie_macro` genérico + stats. Se mantienen separados porque los
# callers HTTP devuelven data cruda sin stats ni clasificación.
# ─────────────────────────────────────────────────────────────────────────────


def _query_serie(collection: str, desde: str | None, hasta: str | None) -> list:
    db = get_db_trading()
    filtro: dict = {}
    if desde or hasta:
        rango: dict = {}
        if desde:
            rango["$gte"] = desde
        if hasta:
            rango["$lte"] = hasta
        filtro["fecha"] = rango
    return list(
        db[collection]
        .find(filtro, {"_id": 0, "fecha": 1, "valor": 1})
        .sort("fecha", 1)
    )


@cached(ttl=3600)
def get_badlar(desde: str | None = None, hasta: str | None = None) -> list:
    return _query_serie("BADLAR", desde, hasta)


@cached(ttl=3600)
def get_cer(desde: str | None = None, hasta: str | None = None) -> list:
    return _query_serie("CER", desde, hasta)


@cached(ttl=3600)
def get_dolar(desde: str | None = None, hasta: str | None = None) -> list:
    return _query_serie("DOLAR", desde, hasta)


@cached(ttl=5)
def get_ultimo_mep() -> dict:
    """Último valor de dólar MEP/CCL/canje + oficial. Prefiere snapshot live
    (5s desde engines/dolares.py vía WS) y cae al último doc del cron de cierre
    (Valuaciones.Dolar) si el snapshot live no existe.

    Devuelve {mep, ccl, canje, oficial, timestamp, source}. Los 4 campos
    numéricos pueden ser None si los inputs WS no están disponibles.

    El `oficial` viene de Valuaciones.DolarOficialLive (MAE UST$T mayorista
    plazo 000) — fuente única del dólar oficial en toda la app. Es
    independiente de la fuente del MEP/CCL: el snapshot WS no lo escribe."""
    from core.dolar_oficial import mid_oficial_live

    db = get_db_valuaciones()

    snap = db["DolarSnapshot"].find_one(
        {"_id": "current"},
        {"_id": 0, "mep": 1, "ccl": 1, "canje": 1, "timestamp": 1, "source": 1},
    )
    base = snap if (snap and snap.get("mep") is not None) else None
    if base is None:
        doc = db["Dolar"].find_one(
            {}, {"_id": 0, "mep": 1, "ccl": 1, "canje": 1, "timestamp": 1},
            sort=[("timestamp", -1)],
        )
        if doc:
            doc["source"] = "cron_close"
        base = doc or {}

    base["oficial"] = mid_oficial_live("oficial").get("value")
    return base


@cached(ttl=300)
def get_ultimo_uva() -> float | None:
    """Último UVA cargado MANUALMENTE en `Trading.UVA`.

    Cada doc tiene `{valor_uva: <float>}` (más lo que sea — el resto se ignora).
    Toma el más reciente por `_id` (ObjectId time-based). Cuando UVA cambia,
    se inserta un doc nuevo desde Compass o `scripts/insertar_uva.py` — el
    cron diario / motor lo levanta solo (vía cache TTL 5min).
    """
    doc = get_db_trading()["UVA"].find_one(sort=[("_id", -1)])
    if not doc:
        return None
    v = doc.get("valor_uva")
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


@cached(ttl=300)
def get_historico_mep(desde: str | None = None, hasta: str | None = None) -> list:
    """Serie histórica del dólar MEP (Valuaciones.Dolar)."""
    db = get_db_valuaciones()
    filtro: dict = {}
    if desde or hasta:
        rango: dict = {}
        if desde:
            rango["$gte"] = datetime.fromisoformat(desde)
        if hasta:
            rango["$lte"] = datetime.fromisoformat(hasta + "T23:59:59")
        filtro["timestamp"] = rango
    return list(db["Dolar"].find(filtro, {"_id": 0, "mep": 1, "timestamp": 1}))


@cached(ttl=60)
def get_historico_dolares(
    desde: str | None = None,
    hasta: str | None = None,
    ventana_dias: int = 30,
) -> dict:
    """3 series de dólar DAILY-CLOSE para el chart de la watchlist ARGY.

    Devuelve `{mep: [...], ccl: [...], oficial: [...]}` con cada item
    `{ts: "YYYY-MM-DD", valor: float}` — un punto por día. Garantiza
    continuidad visual (sin cortes intraday, sin huecos los fines de
    semana cuando se conectan los puntos en el frontend).

    Fuentes:
      - MEP / CCL → último tick del día en Valuaciones.Dolar (WS engines/dolares.py).
      - Oficial   → Trading.DOLAR (A3500 BCRA fixing diario, escrito por
                    jobs/bcra.py 22 UTC L-V). 1 valor por día garantizado.
                    Antes usábamos dolarapi.com pero a veces no popula y
                    BCRA es la fuente oficial canónica.

    Default ventana: últimos `ventana_dias` (30).
    """
    db_val = get_db_valuaciones()
    db_tr = get_db_trading()

    # Resolución de ventana
    if hasta:
        hasta_dt = datetime.fromisoformat(hasta + "T23:59:59").replace(tzinfo=UTC)
    else:
        hasta_dt = datetime.now(UTC)
    if desde:
        desde_dt = datetime.fromisoformat(desde).replace(tzinfo=UTC)
    else:
        desde_dt = hasta_dt - timedelta(days=ventana_dias)

    # MEP + CCL: aggregate por día, último tick del día (`$last`).
    pipeline = [
        {"$match": {"timestamp": {"$gte": desde_dt, "$lte": hasta_dt}}},
        {"$sort": {"timestamp": 1}},
        {"$group": {
            "_id": {"$dateToString": {"format": "%Y-%m-%d", "date": "$timestamp"}},
            "mep": {"$last": "$mep"},
            "ccl": {"$last": "$ccl"},
        }},
        {"$sort": {"_id": 1}},
    ]
    mep_ccl_docs = list(db_val["Dolar"].aggregate(pipeline))
    mep_series = [
        {"ts": d["_id"], "valor": float(d["mep"])}
        for d in mep_ccl_docs if d.get("mep") is not None
    ]
    ccl_series = [
        {"ts": d["_id"], "valor": float(d["ccl"])}
        for d in mep_ccl_docs if d.get("ccl") is not None
    ]

    # Oficial = Trading.DOLAR (A3500 BCRA fixing diario). El campo `fecha`
    # ya es string YYYY-MM-DD y hay 1 doc por día — no hace falta reducir.
    desde_str = desde_dt.strftime("%Y-%m-%d")
    hasta_str = hasta_dt.strftime("%Y-%m-%d")
    docs_of = list(
        db_tr["DOLAR"].find(
            {"fecha": {"$gte": desde_str, "$lte": hasta_str},
             "valor": {"$gt": 0}},
            {"_id": 0, "fecha": 1, "valor": 1},
        ).sort("fecha", 1)
    )
    oficial_series = [
        {"ts": d["fecha"], "valor": float(d["valor"])} for d in docs_of
    ]

    return {"mep": mep_series, "ccl": ccl_series, "oficial": oficial_series}

# ─────────────────────────────────────────────────────────────────────────────
# Config: mapping variable macro → (db, coleccion, campo_fecha, campo_valor)
# ─────────────────────────────────────────────────────────────────────────────

# Cada entrada describe cómo traer la serie. fecha_tipo = "string" (ISO YYYY-MM-DD)
# o "datetime" (objeto Mongo).
_MACROS: dict[str, dict[str, Any]] = {
    "tamar":  {"db": "Trading",     "col": "TAMAR",  "ts": "fecha",     "val": "valor", "ts_tipo": "string"},
    "cer":    {"db": "Trading",     "col": "CER",    "ts": "fecha",     "val": "valor", "ts_tipo": "string"},
    "dolar":  {"db": "Trading",     "col": "DOLAR",  "ts": "fecha",     "val": "valor", "ts_tipo": "string"},
    "badlar": {"db": "Trading",     "col": "BADLAR", "ts": "fecha",     "val": "valor", "ts_tipo": "string"},
    "mep":    {"db": "Valuaciones", "col": "Dolar",  "ts": "timestamp", "val": "mep",   "ts_tipo": "datetime"},
    "ccl":    {"db": "Valuaciones", "col": "Dolar",  "ts": "timestamp", "val": "ccl",   "ts_tipo": "datetime"},
    "canje":  {"db": "Valuaciones", "col": "Dolar",  "ts": "timestamp", "val": "canje", "ts_tipo": "datetime"},
    # Caución: serie de cierre histórico, escrita por engines/caucion.py al apagado.
    # Filtra por moneda en el fetcher; la tool delega a obtener_serie_macro genérico.
    "caucion_ars": {"db": "Trading", "col": "Caucion", "ts": "fecha", "val": "tna_cierre", "ts_tipo": "string", "extra_filter": {"moneda": "ARS"}},
    "caucion_usd": {"db": "Trading", "col": "Caucion", "ts": "fecha", "val": "tna_cierre", "ts_tipo": "string", "extra_filter": {"moneda": "USD"}},

    # Series argentinadatos.com (vía jobs/argentina_datos.py, cron 1x/día).
    "riesgo_pais":       {"db": "Trading", "col": "RiesgoPais",          "ts": "fecha", "val": "valor", "ts_tipo": "string"},
    "ipc":               {"db": "Trading", "col": "InflacionMensual",    "ts": "fecha", "val": "valor", "ts_tipo": "string"},
    "ipc_interanual":    {"db": "Trading", "col": "InflacionInteranual", "ts": "fecha", "val": "valor", "ts_tipo": "string"},
    # Dólares agregados — antes venían de dolarapi.com (jobs/dolar_api.py),
    # apagado el 2026-05-04. "oficial" y "mayorista" redirigen a la serie
    # A3500 BCRA (que ES el oficial mayorista canónico). "blue" no tiene
    # reemplazo y queda bloqueada.
    "dolar_oficial":   {"db": "Trading", "col": "DOLAR", "ts": "fecha", "val": "valor", "ts_tipo": "string"},
    "dolar_mayorista": {"db": "Trading", "col": "DOLAR", "ts": "fecha", "val": "valor", "ts_tipo": "string"},
}

# Variables conocidas pero bloqueadas por falta de data en Mongo.
_BLOQUEADAS: dict[str, str] = {
    "ipim":         "IPIM INDEC no cargado — falta job jobs/inflacion.py",
    "repo":         "stock REPO BCRA no cargado — falta extensión de jobs/bcra.py",
    "rem_inflacion": "REM BCRA no cargado — falta job jobs/rem.py (tiene múltiples indicadores, requiere schema específico)",
}

_CAMPOS_TICKER_VALIDOS = ("TEA", "TEM", "paridad", "duration", "price")


# ─────────────────────────────────────────────────────────────────────────────
# Fetchers internos
# ─────────────────────────────────────────────────────────────────────────────


def _get_db(db_name: str):
    return {"Trading": get_db_trading, "Valuaciones": get_db_valuaciones}[db_name]()


def _fetch_serie_macro(variable: str, ventana_dias: int) -> list[dict]:
    """Trae la serie [{fecha, valor}] ordenada cronológicamente."""
    cfg = _MACROS[variable]
    db = _get_db(cfg["db"])
    corte = datetime.now(UTC) - timedelta(days=ventana_dias)

    if cfg["ts_tipo"] == "string":
        filtro = {cfg["ts"]: {"$gte": corte.strftime("%Y-%m-%d")}}
    else:
        filtro = {cfg["ts"]: {"$gte": corte}}

    # Mappings de colecciones compartidas (ej: Trading.Caucion tiene moneda) usan
    # extra_filter para discriminar la serie correcta.
    extra = cfg.get("extra_filter")
    if extra:
        filtro.update(extra)

    docs = list(
        db[cfg["col"]]
        .find(filtro, {"_id": 0, cfg["ts"]: 1, cfg["val"]: 1})
        .sort(cfg["ts"], 1)
    )

    out: list[dict] = []
    for d in docs:
        fecha = d.get(cfg["ts"])
        valor = d.get(cfg["val"])
        if valor is None:
            continue
        if isinstance(fecha, datetime):
            fecha = fecha.strftime("%Y-%m-%d")
        out.append({"fecha": str(fecha)[:10], "valor": float(valor)})
    return out


def _fetch_serie_ticker(variable: str, ventana_dias: int) -> list[dict]:
    """Serie por ticker: variable con forma '<TICKER>.<CAMPO>'.

    Devuelve un punto por día (último valor del día para ese ticker/campo).
    """
    # Import lazy para evitar ciclo al importar macro desde tools.py
    from api.services.renta_fija import resolver_ticker_exacto

    parts = variable.split(".", 1)
    if len(parts) != 2:
        return []
    ticker_raw, campo = parts[0].strip().upper(), parts[1].strip()
    if campo not in _CAMPOS_TICKER_VALIDOS:
        return []

    ticker_exact = resolver_ticker_exacto(ticker_raw)
    if not ticker_exact:
        return []

    db = get_db_trading()
    corte = datetime.now(UTC) - timedelta(days=ventana_dias)

    pipeline = [
        {"$match": {
            "ticker": ticker_exact,
            "timestamp": {"$gte": corte},
            campo: {"$exists": True, "$ne": None},
        }},
        {"$sort": {"timestamp": 1}},
        {"$group": {
            "_id": {"$dateToString": {"format": "%Y-%m-%d", "date": "$timestamp"}},
            "valor": {"$last": f"${campo}"},
        }},
        {"$sort": {"_id": 1}},
    ]
    return [
        {"fecha": r["_id"], "valor": float(r["valor"])}
        for r in db["TimeSales"].aggregate(pipeline)
        if r.get("valor") is not None
    ]


def _stub_bloqueada(variable: str) -> dict:
    """Response estándar para variables que no tienen data cargada."""
    return {
        "variable": variable,
        "actual": None,
        "clasificacion": "sin_datos",
        "serie": [],
        "hint": _BLOQUEADAS.get(variable, "data no disponible"),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Tools expuestas al asistente
# ─────────────────────────────────────────────────────────────────────────────


@cached(ttl=120)
def obtener_serie_macro(variable: str, ventana_dias: int = 90) -> dict:
    """Devuelve actual + serie + stats completos para una variable."""
    var = (variable or "").strip().lower() if variable else ""
    if not var:
        return {"variable": "", "error": "variable vacía", "clasificacion": "sin_datos"}

    # Variables bloqueadas: stub
    if var in _BLOQUEADAS:
        return _stub_bloqueada(var)

    # Serie por ticker: contiene "."
    if "." in var:
        # Mantener el case original del ticker para resolver_ticker_exacto
        serie = _fetch_serie_ticker(variable.strip(), ventana_dias)
    elif var in _MACROS:
        serie = _fetch_serie_macro(var, ventana_dias)
    else:
        return {
            "variable": variable,
            "error": f"variable desconocida: {variable}",
            "clasificacion": "sin_datos",
            "hint": f"valores soportados: {sorted(_MACROS.keys())} o '<TICKER>.<CAMPO>'",
        }

    if not serie:
        return {
            "variable": variable,
            "actual": None,
            "serie": [],
            "clasificacion": "sin_datos",
            "hint": f"sin observaciones en los últimos {ventana_dias} días",
        }

    actual = serie[-1]["valor"]
    fecha_actual = serie[-1]["fecha"]
    values = [p["valor"] for p in serie]
    stats = compute_stats(values, actual)

    return {
        "variable": variable,
        "actual": actual,
        "fecha_actual": fecha_actual,
        "ventana_dias": ventana_dias,
        "serie": serie,
        "cambio_dia_pct":    cambio_pct(values, actual, 1),
        "cambio_semana_pct": cambio_pct(values, actual, 5),   # ~5 ruedas
        "cambio_mes_pct":    cambio_pct(values, actual, 21),  # ~21 ruedas
        **stats,
    }


def clasificar_nivel(variable: str, ventana_dias: int = 90) -> dict:
    """Wrapper compacto de obtener_serie_macro — devuelve solo clasificación + contexto."""
    full = obtener_serie_macro(variable=variable, ventana_dias=ventana_dias)
    pct = full.get("percentil_actual")
    z = full.get("zscore_actual")
    clasif = full.get("clasificacion", "sin_datos")

    # Armar contexto textual corto para que el modelo lo incluya como disclaimer
    contexto_parts: list[str] = []
    if pct is not None:
        contexto_parts.append(f"percentil {pct:.0f}")
    if z is not None:
        contexto_parts.append(f"z-score {z:.2f}")
    contexto_parts.append(f"vs {ventana_dias}d")
    contexto = "; ".join(contexto_parts)

    return {
        "variable": variable,
        "actual": full.get("actual"),
        "clasificacion": clasif,
        "percentil_actual": pct,
        "zscore_actual": z,
        "ventana_dias": ventana_dias,
        "contexto": contexto,
        "hint": full.get("hint"),  # None si no aplica
    }
