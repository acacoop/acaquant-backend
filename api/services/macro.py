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

Ver docs/asistente/tools_spec.md §2.2 para el contrato completo.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from api.cache import cached
from api.db import get_db_trading, get_db_valuaciones
from quant.stats import cambio_pct, compute_stats

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
}

# Variables conocidas pero bloqueadas por falta de data en Mongo.
_BLOQUEADAS: dict[str, str] = {
    "ipc":          "IPC INDEC no cargado — falta job jobs/inflacion.py",
    "ipim":         "IPIM INDEC no cargado — falta job jobs/inflacion.py",
    "riesgo_pais":  "EMBI+ no cargado — falta job jobs/riesgo_pais.py",
    "repo":         "stock REPO BCRA no cargado — falta extensión de jobs/bcra.py",
    "rem_inflacion": "REM BCRA no cargado — falta job jobs/rem.py",
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
    from api.services.cotizaciones import resolver_ticker_exacto

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
    """Devuelve actual + serie + stats completos para una variable.

    Shape del return — ver docs/asistente/tools_spec.md §2.2.
    """
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
