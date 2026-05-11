"""Cliente OpenFIGI con caching en Mongo (Smart.CusipCatalog).

OpenFIGI (api.openfigi.com) es el servicio gratuito de Bloomberg que mapea
identificadores financieros entre sí: CUSIP ↔ ISIN ↔ FIGI ↔ ticker, con
metadata (name, exchange, security_type).

Lo usamos para 2 direcciones:
    - CUSIP → ticker  (al parsear 13Fs: cada holding viene con CUSIP, no ticker)
    - ticker → CUSIP  (al seedear CEDEARs: conocemos el ticker US, queremos CUSIP)

Caching: cada lookup se persiste en `Smart.CusipCatalog`. Si el CUSIP/ticker
ya está en cache no pegamos a OpenFIGI. Lookups fallidos (no match) también
se cachean con ticker=null para no reintentar.

Rate limit (real, medido por OpenFIGI):
    - Sin API key: 25 req **por 6 segundos** (ventana deslizante) + max 10 items/req
    - Con API key: 250 req por 6s + max 100 items/req

Throttle interno: usa la misma ventana de 6s, con headroom (22/240) para
absorber pequeños desfases de reloj sin disparar 429.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from collections.abc import Iterable
from datetime import UTC, datetime

import requests
from pymongo import UpdateOne

from core.mongo import get_mongo_client

logger = logging.getLogger(__name__)

API_KEY = os.getenv("OPENFIGI_API_KEY", "")
BASE_URL = "https://api.openfigi.com/v3/mapping"

# Misma ventana que mide OpenFIGI server-side. Headroom: 22/25 sin key,
# 240/250 con key. Evita 429 ante bursts iniciales.
RATE_WINDOW_SECONDS = 6
MAX_REQ_PER_WINDOW = 240 if API_KEY else 22
MAX_BATCH_SIZE = 100 if API_KEY else 10

_rate_lock = threading.Lock()
_calls_ts: list[float] = []

# Exchanges US que preferimos cuando una respuesta OpenFIGI tiene múltiples
# matches (ej. mismo CUSIP cotiza en composite + Nasdaq + NYSE Arca).
US_EXCHANGE_PREFS = ("UN", "UR", "UA", "UV", "UW", "UQ", "US")


class OpenFIGIError(RuntimeError):
    """Error del cliente (red, rate limit, respuesta inválida)."""


def _wait_rate_limit() -> None:
    """Sliding window de RATE_WINDOW_SECONDS con MAX_REQ_PER_WINDOW. Thread-safe."""
    with _rate_lock:
        now = time.time()
        _calls_ts[:] = [t for t in _calls_ts if now - t < RATE_WINDOW_SECONDS]
        if len(_calls_ts) >= MAX_REQ_PER_WINDOW:
            sleep_s = RATE_WINDOW_SECONDS - (now - _calls_ts[0]) + 0.3
            if sleep_s > 0:
                time.sleep(sleep_s)
                now = time.time()
                _calls_ts[:] = [t for t in _calls_ts if now - t < RATE_WINDOW_SECONDS]
        _calls_ts.append(now)


def _post_mapping(items: list[dict]) -> list[dict]:
    """POST raw batch a OpenFIGI /v3/mapping. Devuelve el array de resultados.

    Cada item del input es `{"idType": "...", "idValue": "...", ...}`.
    Cada item del output es `{"data": [...]}` (success) o `{"warning": "..."}` (no match).
    """
    if not items:
        return []
    if len(items) > MAX_BATCH_SIZE:
        raise OpenFIGIError(f"batch demasiado grande: {len(items)} > {MAX_BATCH_SIZE}")
    _wait_rate_limit()
    headers = {"Content-Type": "application/json"}
    if API_KEY:
        headers["X-OPENFIGI-APIKEY"] = API_KEY
    try:
        r = requests.post(BASE_URL, json=items, headers=headers, timeout=20)
    except requests.RequestException as e:
        raise OpenFIGIError(f"red: {e}") from e
    if r.status_code == 429:
        raise OpenFIGIError("429 Rate Limit — backoff y reintentar")
    if r.status_code != 200:
        raise OpenFIGIError(f"HTTP {r.status_code}: {r.text[:200]}")
    return r.json()


def _pick_best_match(matches: list[dict]) -> dict | None:
    """Cuando OpenFIGI devuelve múltiples matches, elegimos el mejor:
    1. Si hay alguno en US (compositeFIGI tipicamente), prefiere ese.
    2. Sino, el primero.
    """
    if not matches:
        return None
    # Preferencia: exchanges US típicos. UN=NYSE, UR=NYSE Arca, UA=NYSE American,
    # UV=Nasdaq Global Select, UW=Nasdaq Global, UQ=Nasdaq Capital, US=composite.
    for pref in US_EXCHANGE_PREFS:
        for m in matches:
            if m.get("exchCode") == pref:
                return m
    return matches[0]


def _normalize_doc(cusip: str | None, ticker: str | None, m: dict | None) -> dict:
    """Construye el doc para persistir en Smart.CusipCatalog."""
    return {
        "cusip":          cusip,
        "ticker":         (m or {}).get("ticker") if m else None,
        "name":           (m or {}).get("name") if m else None,
        "exchange":       (m or {}).get("exchCode") if m else None,
        "security_type":  (m or {}).get("securityType") if m else None,
        "figi":           (m or {}).get("figi") if m else None,
        # Si vinimos por ticker (no por CUSIP), guardamos el ticker query también.
        # cusip puede ser None si OpenFIGI no devolvió mapeo a CUSIP.
        "ticker_query":   ticker,
        "last_lookup_at": datetime.now(UTC),
    }


def _get_collection():
    return get_mongo_client()["Smart"]["CusipCatalog"]


# ─────────────────────────────────────────────────────────────────────────────
# Public lookups
# ─────────────────────────────────────────────────────────────────────────────


def lookup_cusip(cusip: str) -> dict | None:
    """Lookup CUSIP único. Devuelve doc del catalog o None si no hay match.

    Si está en cache, no toca OpenFIGI. Si no, hace 1 request.
    Lookups sin match se cachean con ticker=null para no reintentar.
    """
    cusip = (cusip or "").strip()
    if not cusip:
        return None
    col = _get_collection()
    cached = col.find_one({"cusip": cusip}, {"_id": 0})
    if cached:
        return cached if cached.get("ticker") else None
    res = _post_mapping([{"idType": "ID_CUSIP", "idValue": cusip}])
    item = res[0] if res else {}
    matches = item.get("data") or []
    best = _pick_best_match(matches)
    doc = _normalize_doc(cusip=cusip, ticker=None, m=best)
    col.update_one({"cusip": cusip}, {"$set": doc}, upsert=True)
    return doc if doc.get("ticker") else None


def lookup_cusips_batch(cusips: Iterable[str]) -> dict[str, dict | None]:
    """Bulk lookup. Devuelve dict {cusip: doc_or_None}.

    Eficiente: separa cached vs missing, batchea los missing en grupos de
    MAX_BATCH_SIZE, persiste todo en Mongo, devuelve el merge.
    """
    cusips_norm = list({(c or "").strip() for c in cusips if c and c.strip()})
    if not cusips_norm:
        return {}
    col = _get_collection()
    cached = list(col.find({"cusip": {"$in": cusips_norm}}, {"_id": 0}))
    cached_by_cusip = {d["cusip"]: d for d in cached}
    missing = [c for c in cusips_norm if c not in cached_by_cusip]
    logger.info(
        "openfigi batch: total=%d cached=%d to_fetch=%d",
        len(cusips_norm), len(cached_by_cusip), len(missing),
    )

    for i in range(0, len(missing), MAX_BATCH_SIZE):
        batch = missing[i:i + MAX_BATCH_SIZE]
        items = [{"idType": "ID_CUSIP", "idValue": c} for c in batch]
        res = _post_mapping(items)
        # res es una lista paralela a items.
        ops = []
        for cusip, r in zip(batch, res, strict=False):
            matches = r.get("data") or []
            best = _pick_best_match(matches)
            doc = _normalize_doc(cusip=cusip, ticker=None, m=best)
            cached_by_cusip[cusip] = doc
            ops.append(UpdateOne({"cusip": cusip}, {"$set": doc}, upsert=True))
        if ops:
            col.bulk_write(ops, ordered=False)

    # Devolvemos None para los que vinieron con ticker=null (no-match).
    return {
        c: (cached_by_cusip[c] if cached_by_cusip[c].get("ticker") else None)
        for c in cusips_norm
    }


def lookup_ticker(ticker: str, exch_code: str = "US") -> dict | None:
    """Lookup desde ticker (US). Útil para seedear CEDEARs.

    Args:
        ticker: ej. 'AAPL'.
        exch_code: filtro de exchange OpenFIGI. 'US' = composite US (NYSE/Nasdaq/etc).

    Returns:
        Doc del catalog con cusip + name + exchange. None si no hay match.
    """
    ticker = (ticker or "").strip().upper()
    if not ticker:
        return None
    col = _get_collection()
    # Cache por ticker_query (no por ticker, porque ticker viene de OpenFIGI
    # y a veces difiere en mayúsculas/sufijos según la fuente).
    cached = col.find_one(
        {"$or": [{"ticker_query": ticker}, {"ticker": ticker}]},
        {"_id": 0},
    )
    if cached and cached.get("cusip"):
        return cached
    res = _post_mapping([{
        "idType": "TICKER",
        "idValue": ticker,
        "exchCode": exch_code,
    }])
    item = res[0] if res else {}
    matches = item.get("data") or []
    best = _pick_best_match(matches)
    # Para ticker→CUSIP, el CUSIP viene en `cusip` o `compositeCusip` según provider.
    cusip = (best or {}).get("cusip") or None
    doc = _normalize_doc(cusip=cusip, ticker=ticker, m=best)
    if cusip:
        col.update_one({"cusip": cusip}, {"$set": doc}, upsert=True)
    return doc if doc.get("ticker") else None
