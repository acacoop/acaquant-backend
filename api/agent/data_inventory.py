"""Introspección automática de Mongo para saber qué data hay disponible.

No hardcodeamos fechas de arranque. En cada conversación el modelo recibe un
bloque con el rango real observado (primer doc / último doc) de cada colección
clave. Así, cuando cambie el feed, el modelo se entera solo.

Cache 5 minutos. Mongo frente a queries con índice sobre el campo de fecha
responde sub-100ms; igual cacheamos para no hacerlo en cada request.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any

from core.mongo import get_mongo_client_read

logger = logging.getLogger(__name__)

_CACHE_TTL = 300.0  # 5 minutos
_cache: dict[str, Any] = {"ts": 0.0, "text": ""}

# Colecciones a inspeccionar. (db, col, campo_fecha, etiqueta, descripción corta)
COLECCIONES = [
    ("Trading",     "TimeSales",            "timestamp",     "TimeSales (trades)",   "trades intradía enriquecidos (duration/TEA/TEM/paridad)"),
    ("Trading",     "MarketSnapshot",       "updated_at",    "MarketSnapshot",       "book + métricas en vivo (1s refresh)"),
    ("Trading",     "CER",                  "fecha",         "CER",                  "índice BCRA (serie diaria)"),
    ("Trading",     "BADLAR",               "fecha",         "BADLAR",               "tasa BADLAR privados (BCRA)"),
    ("Trading",     "DOLAR",                "fecha",         "Dólar A3500",          "dólar oficial BCRA"),
    ("Trading",     "ForwardsLive",         None,            "ForwardsLive",         "matriz forwards vigente por curva"),
    ("Trading",     "ForwardsHistorico",    "fecha",         "ForwardsHistorico",    "serie diaria de forwards"),
    ("Trading",     "BreakevensLive",       None,            "BreakevensLive",       "breakevens Lecap vs CER vigentes"),
    ("Trading",     "BreakevensHistorico",  "fecha",         "BreakevensHistorico",  "serie diaria de breakevens"),
    ("Trading",     "Curvas",               None,            "Curvas",               "definición estática de bonos (Lecap/Boncap/Boncer)"),
    ("Opciones",    "OptionsSnapshot",      "updated_at",    "OptionsSnapshot",      "opciones GGAL con greeks e IV"),
    ("Opciones",    "DataHistorica",        "fecha",         "Opciones.DataHistorica", "rollup diario opciones"),
    ("Valuaciones", "Dolar",                "timestamp",     "Dólar MEP",            "snapshot intradía MEP"),
]

# Datos que el usuario podría pedir y NO están disponibles. Se muestran al modelo.
NO_DISPONIBLE = [
    "Riesgo país (EMBI+)",
    "Datos de licitaciones del Tesoro (rollover, bid-to-cover, monto colocado)",
    "Stock de pases / REPO BCRA intradía",
    "TAMAR mayorista (solo implícita vía duales)",
    "Volumen intradiario histórico en tiempo real (solo cierre acumulado)",
    "Carteras / AuM / operaciones de clientes (fuera de alcance del asistente)",
]

# Campos que son CALCULADOS, no vienen directo del feed. El modelo debe saberlo
# para no sobrevalorar la precisión.
CAMPOS_CALCULADOS = [
    "paridad CER (precio / (VN × CER_trade/CER_emision))",
    "TEA / TEM / duration (calculados por engines.curvas, lag ~5s sobre el trade)",
    "breakevens (calculados por engines.breakevens cada 30s)",
    "forwards (calculados por engines.forwards cada 30s)",
    "Greeks e IV (calculados por engines.options con Black-Scholes)",
]


def _fmt_date(v: Any) -> str:
    if not v:
        return "—"
    if isinstance(v, datetime):
        return v.strftime("%Y-%m-%d")
    if isinstance(v, str):
        return v[:10]
    return str(v)[:10]


def _inspect(client, db: str, col: str, campo: str | None) -> str:
    """Devuelve '2024-01-15 → 2026-04-18 (1.234 docs)' o similar."""
    try:
        collection = client[db][col]
        count = collection.estimated_document_count()
    except Exception as e:
        logger.debug("no pude contar %s.%s: %s", db, col, e)
        return "sin datos"

    if count == 0:
        return "sin datos"

    count_fmt = f"{count:,}".replace(",", ".")

    if campo is None:
        return f"{count_fmt} docs"

    try:
        first = collection.find({}, {campo: 1, "_id": 0}).sort(campo, 1).limit(1)
        first_doc = next(iter(first), None)
        last = collection.find({}, {campo: 1, "_id": 0}).sort(campo, -1).limit(1)
        last_doc = next(iter(last), None)
    except Exception as e:
        logger.debug("no pude rango %s.%s: %s", db, col, e)
        return f"{count_fmt} docs (rango desconocido)"

    desde = _fmt_date(first_doc.get(campo) if first_doc else None)
    hasta = _fmt_date(last_doc.get(campo) if last_doc else None)
    return f"{desde} → {hasta} ({count_fmt} docs)"


def build_data_inventory() -> str:
    """Arma el bloque 'DATA DISPONIBLE' listo para concatenar al system prompt."""
    now = time.time()
    if _cache["text"] and (now - _cache["ts"]) < _CACHE_TTL:
        return _cache["text"]

    try:
        client = get_mongo_client_read()
    except Exception:
        logger.exception("no pude conectar a Mongo para inventory")
        return _cache["text"]  # devuelvo el último bueno si hay

    lineas = [
        f"DATA DISPONIBLE EN BASE (auto, {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}):"
    ]
    for db, col, campo, label, desc in COLECCIONES:
        rango = _inspect(client, db, col, campo)
        lineas.append(f"- {label}: {rango} — {desc}")

    lineas.append("")
    lineas.append("DATOS QUE NO TENEMOS (no inventar):")
    for x in NO_DISPONIBLE:
        lineas.append(f"- {x}")

    lineas.append("")
    lineas.append("CAMPOS CALCULADOS (no vienen del mercado directo):")
    for x in CAMPOS_CALCULADOS:
        lineas.append(f"- {x}")

    text = "\n".join(lineas)
    _cache["ts"] = now
    _cache["text"] = text
    return text
