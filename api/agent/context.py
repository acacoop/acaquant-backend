"""Contexto dinámico inyectado en cada conversación.

El modelo arranca con una 'foto del mercado': fecha actual, MEP, CER, top bonos,
próximos vencimientos, breakevens. Eso le da situational awareness para
interpretar preguntas ambiguas ('el 26', 'resumen', 'qué tal hoy').

Cacheamos 60s para que ráfagas de mensajes no re-consulten Mongo cada vez.
Ante errores individuales seguimos igual (resto del contexto se arma).
"""
from __future__ import annotations

import logging
import time
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

from core.mongo import get_mongo_client_read

logger = logging.getLogger(__name__)

TZ_AR = timezone(timedelta(hours=-3))  # Argentina no tiene DST
_CACHE_TTL = 60  # segundos
_cache: dict[str, Any] = {"ts": 0.0, "text": ""}


def _fmt_money(v: float | int | None) -> str:
    if v is None:
        return "—"
    try:
        return f"{float(v):,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")
    except (TypeError, ValueError):
        return "—"


def _fmt_pct(v: float | None) -> str:
    if v is None:
        return "—"
    return f"{v * 100:.2f}%" if abs(v) < 1 else f"{v:.2f}%"


def _fmt_date(v: Any) -> str:
    if not v:
        return "—"
    if isinstance(v, datetime):
        return v.astimezone(TZ_AR).strftime("%d/%m/%Y")
    if isinstance(v, str):
        try:
            return datetime.fromisoformat(v.replace("Z", "+00:00")).astimezone(TZ_AR).strftime("%d/%m/%Y")
        except ValueError:
            return v
    return str(v)


def _safe(fn, default: str = "—") -> str:
    try:
        return fn() or default
    except Exception as e:
        logger.warning("contexto parcial falló: %s", e)
        return default


def _mep_line(client) -> str:
    doc = client["Valuaciones"]["Dolar"].find_one({}, sort=[("timestamp", -1)])
    if not doc:
        return "MEP: sin datos"
    mep = doc.get("mep")
    ts = doc.get("timestamp")
    return f"MEP: ${_fmt_money(mep)} (snapshot {_fmt_date(ts)})"


def _cer_line(client) -> str:
    doc = client["Trading"]["CER"].find_one({}, sort=[("fecha", -1)])
    if not doc:
        return "CER: sin datos"
    return f"CER: {_fmt_money(doc.get('valor'))} (al {_fmt_date(doc.get('fecha'))})"


def _a3500_line(client) -> str:
    doc = client["Trading"]["DOLAR"].find_one({}, sort=[("fecha", -1)])
    if not doc:
        return "A3500: sin datos"
    return f"A3500: ${_fmt_money(doc.get('valor'))} (al {_fmt_date(doc.get('fecha'))})"


def _top_volumen_line(client) -> str:
    """Top 5 tickers por volumen en los últimos 3 días, enriquecidos con curva/tipo."""
    corte = datetime.now(UTC) - timedelta(days=3)
    pipeline = [
        {"$match": {"timestamp": {"$gte": corte}, "money": {"$gt": 0}}},
        {"$group": {"_id": "$ticker", "money": {"$sum": "$money"}}},
        {"$sort": {"money": -1}},
        {"$limit": 5},
    ]
    rows = list(client["Trading"]["TimeSales"].aggregate(pipeline))
    if not rows:
        return "Top volumen (últimos 3d): sin datos"

    # Buscar curva/tipo en Trading.Curvas por ticker para clasificar.
    fulls = [r["_id"] for r in rows]
    curvas_docs = list(
        client["Trading"]["Curvas"].find(
            {"ticker": {"$in": fulls}},
            {"_id": 0, "ticker": 1, "ticker_corto": 1, "curva": 1, "tipo": 1},
        )
    )
    by_full = {d["ticker"]: d for d in curvas_docs}

    items = []
    for r in rows:
        full = r["_id"]
        info = by_full.get(full, {})
        corto = info.get("ticker_corto") or _short_ticker(full)
        curva = info.get("curva") or ""
        items.append(f"{corto}{' (' + curva + ')' if curva else ''}")
    return "Top volumen (últimos 3d): " + ", ".join(items)


def _ultima_licitacion_line(client) -> str:
    """Último instrumento emitido en Trading.Curvas (proxy de 'última licitación').

    Si encontramos un doc con fecha_emision dentro de los últimos 10 días, lo
    marcamos como recién emitido (datos provisorios primeros 3-5 días).
    """
    hoy = datetime.now(TZ_AR).date()
    corte = (hoy - timedelta(days=10)).isoformat()
    cur = (
        client["Trading"]["Curvas"]
        .find(
            {"fecha_emision": {"$gte": corte}},
            {"_id": 0, "ticker_corto": 1, "ticker": 1, "fecha_emision": 1, "curva": 1},
        )
        .sort("fecha_emision", -1)
        .limit(3)
    )
    items = []
    for d in cur:
        corto = d.get("ticker_corto") or _short_ticker(d.get("ticker", ""))
        fecha = d.get("fecha_emision", "")
        curva = d.get("curva", "")
        items.append(f"{corto} ({curva}, emitido {_fmt_date(fecha)})")
    if not items:
        return "Últimas emisiones (10d): sin datos"
    return "Últimas emisiones (10d) — primeros 3-5 días los datos son provisorios: " + " · ".join(items)


def _short_ticker(full: str) -> str:
    """'MERV - XMEV - TX26 - 24hs' → 'TX26'."""
    if not full or " - " not in full:
        return full or ""
    parts = [p.strip() for p in full.split(" - ")]
    return parts[2] if len(parts) >= 3 else full


def _proximos_vencimientos_line(client, curva: str, label: str) -> str:
    hoy_iso = datetime.now(TZ_AR).strftime("%Y-%m-%d")
    cur = (
        client["Trading"]["Curvas"]
        .find(
            {"curva": curva, "fecha_vencimiento": {"$gte": hoy_iso}},
            {"_id": 0, "ticker_corto": 1, "ticker": 1, "fecha_vencimiento": 1},
        )
        .sort("fecha_vencimiento", 1)
        .limit(4)
    )
    items = []
    for d in cur:
        tkr = d.get("ticker_corto") or _short_ticker(d.get("ticker", ""))
        items.append(f"{tkr} ({_fmt_date(d.get('fecha_vencimiento'))})")
    if not items:
        return f"Próx. venc. {label}: sin datos"
    return f"Próx. venc. {label}: " + ", ".join(items)


def _breakevens_line(client) -> str:
    docs = list(
        client["Trading"]["BreakevensLive"]
        .find({}, {"_id": 0, "lecap": 1, "cer": 1, "breakeven": 1})
        .limit(3)
    )
    if not docs:
        return "Breakevens: sin datos"
    items = []
    for d in docs:
        lecap = _short_ticker(d.get("lecap", "")) or "?"
        cer = _short_ticker(d.get("cer", "")) or "?"
        be = d.get("breakeven")
        items.append(f"{lecap}↔{cer} {_fmt_pct(be)}")
    return "Breakevens: " + " · ".join(items)


def build_market_context() -> str:
    """Retorna el bloque de contexto listo para concatenar al system prompt.

    Cachea 60s para evitar hammering a Mongo en conversaciones activas.
    """
    now = time.time()
    if _cache["text"] and (now - _cache["ts"]) < _CACHE_TTL:
        return _cache["text"]

    ahora = datetime.now(TZ_AR)
    dia_sem = ahora.weekday()  # 0=lunes
    es_fds = dia_sem >= 5
    hora_h = ahora.hour
    en_rueda = not es_fds and 11 <= hora_h < 17
    estado = "abierto" if en_rueda else ("cerrado (fin de semana)" if es_fds else "cerrado")

    client = get_mongo_client_read()

    lineas = [
        f"Fecha/hora AR: {ahora.strftime('%d/%m/%Y %H:%M')}",
        f"Mercado: {estado}",
        f"{_safe(lambda: _mep_line(client))}",
        f"{_safe(lambda: _a3500_line(client))}",
        f"{_safe(lambda: _cer_line(client))}",
        f"{_safe(lambda: _top_volumen_line(client))}",
        f"{_safe(lambda: _proximos_vencimientos_line(client, 'tasa_fija', 'Lecap'))}",
        f"{_safe(lambda: _proximos_vencimientos_line(client, 'cer', 'CER'))}",
        f"{_safe(lambda: _breakevens_line(client))}",
        f"{_safe(lambda: _ultima_licitacion_line(client))}",
    ]
    text = "\n".join(f"- {l}" for l in lineas)

    _cache["ts"] = now
    _cache["text"] = text
    return text
