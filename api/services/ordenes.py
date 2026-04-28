"""Servicio de órdenes — funciones puras invocables desde routers o scripts.

Sin FastAPI ni HTTP. Pensado así para que también lo pueda usar un script
de smoke o un job futuro. Las funciones que mutan persisten en Mongo
ANTES de tocar al broker, así si pyRofex tira o se cuelga el doc queda
trackeable y el motor (proceso aparte) lo va a ver vía recovery.

Diseño:
  - El proceso uvicorn levanta su propia sesión pyRofex liviana
    (`inicializar_para_envio`) — REST-only, sin WS.
  - El motor de órdenes (proceso aparte) tiene su propia sesión con WS
    suscripto a order_report. Es el único que escribe los ER en
    `Operaciones.OrdenesLive`.
  - Acá escribimos el doc inicial (PENDING_NEW) y el audit del request.
    Cuando llega el primer ER del broker el motor lo upsertea con el
    estado real (NEW / REJECTED / etc.).

Idempotencia:
  En V1 confiamos en el clOrdId que devuelve el broker. Si un cliente
  reintenta el mismo POST y el broker terminó aceptando ambos, vamos
  a tener 2 órdenes — explícito en la doc del endpoint. V2 puede sumar
  un `client_request_id` en el header y deduplicar acá.
"""
from __future__ import annotations

import logging
import threading
from datetime import UTC, datetime
from typing import Any

import pyRofex

from core.mongo import get_mongo_client
from core.rofex_orders_session import cuenta_default, inicializar_para_envio

logger = logging.getLogger("api.services.ordenes")

DB_NAME = "Operaciones"
COL_LIVE = "OrdenesLive"
COL_AUDIT = "OrdenesAudit"


# ─────────────────────────────────────────────────────────────────────────────
# Sesión pyRofex — lazy + lock (un init por proceso)
# ─────────────────────────────────────────────────────────────────────────────

_session_ready = False
_session_lock = threading.Lock()


def _ensure_session() -> str:
    """Inicializa pyRofex la primera vez. Devuelve la cuenta default."""
    global _session_ready
    if _session_ready:
        return cuenta_default()
    with _session_lock:
        if not _session_ready:
            inicializar_para_envio()
            _session_ready = True
    return cuenta_default()


# ─────────────────────────────────────────────────────────────────────────────
# Helpers de mapeo enums
# ─────────────────────────────────────────────────────────────────────────────


def _side_enum(side: str):
    s = (side or "").strip().upper()
    if s == "BUY":
        return pyRofex.Side.BUY
    if s == "SELL":
        return pyRofex.Side.SELL
    raise ValueError(f"side inválido: {side!r} (esperado BUY|SELL)")


def _order_type_enum(order_type: str):
    t = (order_type or "").strip().upper()
    if t == "MARKET":
        return pyRofex.OrderType.MARKET
    if t == "LIMIT":
        return pyRofex.OrderType.LIMIT
    raise ValueError(f"order_type inválido: {order_type!r} (esperado MARKET|LIMIT)")


_TIF_VALIDOS = {"DAY", "IOC", "FOK", "GTC"}


def _tif_enum(tif: str | None):
    """Resolve perezosamente — no todas las versiones de pyRofex exponen
    los 4 valores (vimos `TimeInForce.IOC` faltar). Si el broker no
    soporta el tif pedido, error explícito."""
    t = (tif or "DAY").strip().upper()
    if t not in _TIF_VALIDOS:
        raise ValueError(f"tif inválido: {tif!r} (esperado DAY|IOC|FOK|GTC)")
    enum_val = getattr(pyRofex.TimeInForce, t, None)
    if enum_val is None:
        raise ValueError(
            f"tif {t!r} no soportado por esta versión de pyRofex "
            f"(disponibles: {sorted(a for a in dir(pyRofex.TimeInForce) if not a.startswith('_'))})"
        )
    return enum_val


# ─────────────────────────────────────────────────────────────────────────────
# Persistencia
# ─────────────────────────────────────────────────────────────────────────────


def _audit(kind: str, *, cl_ord_id: str | None = None, account: str | None = None,
           actor_email: str | None = None, payload: dict | None = None) -> None:
    db = get_mongo_client()[DB_NAME]
    db[COL_AUDIT].insert_one({
        "ts": datetime.now(UTC),
        "kind": kind,
        "cl_ord_id": cl_ord_id,
        "ws_cl_ord_id": None,
        "account": account,
        "actor_email": actor_email,
        "payload": payload or {},
    })


def _seed_live_doc(*, account: str, ticker: str, side: str, order_type: str,
                   tif: str, size: int, price: float | None,
                   actor_email: str | None) -> None:
    """Crea el doc en OrdenesLive con status=PENDING_LOCAL antes del send.

    Cuando el broker responda con clOrdId, lo updateamos en el mismo doc
    (matcheamos por _id que vamos a generar acá). Si el broker rechaza,
    el doc queda con status=REJECTED_LOCAL.

    Nota: este doc NO usa cl_ord_id como clave primaria porque todavía
    no lo tenemos. Lo seteamos cuando llega la respuesta del broker.
    """
    # No insertamos hasta tener cl_ord_id. Si lo hicieramos sin clOrdId
    # tendríamos un doc huérfano que el motor no puede reconciliar. La
    # alternativa es mandar primero al broker y persistir con el clOrdId
    # de la respuesta — eso hacemos en `send_order`. Esta función queda
    # para consistencia futura (ej: cuando metamos client_request_id).
    return None


# ─────────────────────────────────────────────────────────────────────────────
# API pública
# ─────────────────────────────────────────────────────────────────────────────


def send_order(
    *,
    ticker: str,
    side: str,
    size: int,
    order_type: str = "LIMIT",
    price: float | None = None,
    tif: str | None = "DAY",
    account: str | None = None,
    actor_email: str | None = None,
) -> dict[str, Any]:
    """Envía una orden via REST y persiste el request + respuesta.

    Args:
        ticker: símbolo full ("MERV - XMEV - AL30 - 24hs", "DLR/MAR26", etc.)
        side: "BUY" | "SELL"
        size: nominales (int)
        order_type: "MARKET" | "LIMIT" (default LIMIT)
        price: requerido si LIMIT
        tif: "DAY" | "IOC" | "FOK" | "GTC" (default DAY)
        account: si None, usa la del .env
        actor_email: queda en el audit log

    Returns:
        {ok, cl_ord_id, status, error?, broker_response}
    """
    if order_type.upper() == "LIMIT" and price is None:
        raise ValueError("LIMIT requiere price")
    if size <= 0:
        raise ValueError("size debe ser > 0")

    acc = account or _ensure_session()

    request_payload = {
        "ticker": ticker, "side": side, "size": size,
        "order_type": order_type, "price": price, "tif": tif,
        "account": acc,
    }
    _audit("SEND_REQUEST", account=acc, actor_email=actor_email, payload=request_payload)

    try:
        resp = pyRofex.send_order(
            ticker=ticker,
            side=_side_enum(side),
            size=size,
            price=price if order_type.upper() == "LIMIT" else None,
            order_type=_order_type_enum(order_type),
            time_in_force=_tif_enum(tif),
            account=acc,
            cancel_previous=False,
        )
    except Exception as e:
        logger.error("send_order falló: %s", e, exc_info=True)
        _audit("SEND_ERROR", account=acc, actor_email=actor_email,
               payload={"request": request_payload, "exception": str(e)})
        return {"ok": False, "cl_ord_id": None, "status": "REJECTED_LOCAL", "error": str(e)}

    if not resp or resp.get("status") != "OK":
        _audit("SEND_ERROR", account=acc, actor_email=actor_email,
               payload={"request": request_payload, "response": resp})
        return {"ok": False, "cl_ord_id": None, "status": "REJECTED_BROKER",
                "error": (resp or {}).get("description", "broker rechazó la orden"),
                "broker_response": resp}

    cl_ord_id = (resp.get("order") or {}).get("clOrdId")

    # Insert inicial — el motor lo va a actualizar con cada ER. Si el motor
    # está caído, el doc queda con PENDING_NEW hasta que el motor arranque
    # y haga recovery (que justamente busca esto y lo sincroniza).
    now = datetime.now(UTC)
    db = get_mongo_client()[DB_NAME]
    db[COL_LIVE].update_one(
        {"cl_ord_id": cl_ord_id},
        {
            "$set": {
                "account": acc,
                "ticker": ticker,
                "side": side.upper(),
                "order_type": order_type.upper(),
                "tif": (tif or "DAY").upper(),
                "size": size,
                "price": price,
                "actor_email": actor_email,
                "updated_at": now,
            },
            "$setOnInsert": {
                "cl_ord_id": cl_ord_id,
                "status": "PENDING_NEW",
                "created_at": now,
                "cum_qty": 0,
                "leaves_qty": size,
            },
        },
        upsert=True,
    )

    _audit("SEND_OK", cl_ord_id=cl_ord_id, account=acc, actor_email=actor_email,
           payload={"request": request_payload, "response": resp})
    return {"ok": True, "cl_ord_id": cl_ord_id, "status": "PENDING_NEW",
            "broker_response": resp}


def cancel_order(cl_ord_id: str, *, actor_email: str | None = None) -> dict[str, Any]:
    """Cancela una orden por clOrdId. El estado real llega por order_report
    al motor — acá solo registramos el intento.
    """
    acc = _ensure_session()
    _audit("CANCEL_REQUEST", cl_ord_id=cl_ord_id, account=acc,
           actor_email=actor_email, payload={"cl_ord_id": cl_ord_id})

    try:
        resp = pyRofex.cancel_order(cl_ord_id)
    except Exception as e:
        logger.error("cancel_order falló: %s", e, exc_info=True)
        _audit("CANCEL_ERROR", cl_ord_id=cl_ord_id, account=acc,
               actor_email=actor_email, payload={"exception": str(e)})
        return {"ok": False, "error": str(e)}

    ok = bool(resp and resp.get("status") == "OK")
    _audit("CANCEL_OK" if ok else "CANCEL_ERROR", cl_ord_id=cl_ord_id,
           account=acc, actor_email=actor_email, payload={"response": resp})
    return {"ok": ok, "broker_response": resp}


def get_order_status(cl_ord_id: str) -> dict[str, Any] | None:
    """Lee el estado de una orden de Mongo (lo mantiene actualizado el motor)."""
    db = get_mongo_client()[DB_NAME]
    doc = db[COL_LIVE].find_one({"cl_ord_id": cl_ord_id}, {"_id": 0})
    return doc


def list_orders_dia(account: str | None = None, fecha: datetime | None = None) -> list[dict]:
    """Lista órdenes del día (UTC) para una cuenta."""
    acc = account or cuenta_default()
    if fecha is None:
        fecha = datetime.now(UTC)
    inicio = fecha.replace(hour=0, minute=0, second=0, microsecond=0)
    db = get_mongo_client()[DB_NAME]
    cursor = db[COL_LIVE].find(
        {"account": acc, "created_at": {"$gte": inicio}},
        {"_id": 0},
    ).sort("created_at", -1)
    return list(cursor)
