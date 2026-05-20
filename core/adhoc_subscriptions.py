"""Helpers para Trading.AdhocSubscriptions — suscripciones live efímeras.

Aísla los tickers que el user pidió desde el Dashboard de Operar pero que
NO están en Trading.Curvas ni en config.TICKERS_EXTRA_PRECIOS. El motor
los suscribe vía pyRofex en runtime (cada 5s polleando esta colección)
y los persiste en MarketSnapshot como cualquier otro.

Diseño:
- `_id` = ticker full ROFEX. Único.
- TTL index sobre `expires_at` → Mongo borra solo cuando vence.
- Cap de TTL_DAYS desde `last_used_at`: cada llamada a `subscribe()` o
  `bump_last_used()` refresca expires_at = now + TTL_DAYS. Si nadie lo
  usa en 7 días, vence y el motor lo unsuscribe.
- Cap CAP global: si hay >= CAP docs activos al pedir uno nuevo, el
  endpoint rechaza con 429.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from pymongo import ASCENDING

from core.mongo import get_mongo_client, get_mongo_client_read

DB = "Trading"
COLL = "AdhocSubscriptions"

TTL_DAYS = 7
CAP = 50


def _coll_rw():
    return get_mongo_client()[DB][COLL]


def _coll_ro():
    return get_mongo_client_read()[DB][COLL]


def ensure_indexes() -> None:
    """Crea TTL index sobre expires_at si no existe. Idempotente.

    Llamar al startup del motor o de la API. Mongo respeta el TTL con
    granularidad de ~60s, suficiente para nuestro uso.
    """
    col = _coll_rw()
    existing = {ix["name"] for ix in col.list_indexes()}
    if "expires_at_ttl" not in existing:
        col.create_index(
            [("expires_at", ASCENDING)],
            name="expires_at_ttl",
            expireAfterSeconds=0,
        )
    if "ticker_1" not in existing:
        col.create_index([("ticker", ASCENDING)], name="ticker_1", unique=True)


def subscribe(ticker: str) -> dict:
    """Upsert: agrega o refresca un ticker adhoc.

    Si ya existe, refresca last_used_at y expires_at (rolling TTL).
    Si es nuevo y se alcanzó el cap, devuelve {"ok": False, "reason": "cap"}.

    Devuelve dict con estado: {ok, ticker, created, expires_at, active_count}.
    """
    ticker = (ticker or "").strip()
    if not ticker:
        return {"ok": False, "reason": "ticker vacío"}

    col = _coll_rw()
    now = datetime.now(UTC)
    expires_at = now + timedelta(days=TTL_DAYS)

    existing = col.find_one({"_id": ticker})
    if existing:
        col.update_one(
            {"_id": ticker},
            {"$set": {"last_used_at": now, "expires_at": expires_at}},
        )
        return {
            "ok":           True,
            "ticker":       ticker,
            "created":      False,
            "expires_at":   expires_at,
            "active_count": col.count_documents({}),
        }

    active = col.count_documents({})
    if active >= CAP:
        return {
            "ok":           False,
            "reason":       "cap",
            "ticker":       ticker,
            "active_count": active,
            "cap":          CAP,
        }

    col.insert_one({
        "_id":          ticker,
        "ticker":       ticker,
        "created_at":   now,
        "last_used_at": now,
        "expires_at":   expires_at,
    })
    return {
        "ok":           True,
        "ticker":       ticker,
        "created":      True,
        "expires_at":   expires_at,
        "active_count": active + 1,
    }


def bump_last_used(ticker: str) -> bool:
    """Extiende el TTL si el doc existe. No crea nada nuevo.

    Útil cuando el front sigue polleando un ticker que ya está suscrito
    — cada poll refresca el TTL así no expira mientras se está usando.
    """
    res = _coll_rw().update_one(
        {"_id": ticker},
        {"$set": {
            "last_used_at": datetime.now(UTC),
            "expires_at":   datetime.now(UTC) + timedelta(days=TTL_DAYS),
        }},
    )
    return res.matched_count > 0


def list_active_tickers() -> list[str]:
    """Tickers vivos (sin filtrar por expires_at — Mongo ya los borró)."""
    return [
        d["ticker"]
        for d in _coll_ro().find({}, {"_id": 0, "ticker": 1})
        if d.get("ticker")
    ]


def count_active() -> int:
    return _coll_ro().count_documents({})


def remove(ticker: str) -> bool:
    """Borrado manual (no esperar al TTL). Útil para limpieza explícita
    desde manager o pruebas."""
    res = _coll_rw().delete_one({"_id": ticker})
    return res.deleted_count > 0
