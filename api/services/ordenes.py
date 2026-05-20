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
import re
from datetime import UTC, datetime
from typing import Any

import pyRofex

from core.mongo import get_mongo_client, get_mongo_client_read
from core.rofex_orders_session import cuenta_default, ensure_session_envio

logger = logging.getLogger("api.services.ordenes")

DB_NAME = "Operaciones"
COL_LIVE = "OrdenesLive"
COL_AUDIT = "OrdenesAudit"


# El singleton de inicialización pyRofex vive en core/rofex_orders_session.py
# (`ensure_session_envio`) — compartido con api/services/risk.py y otros.
_ensure_session = ensure_session_envio


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

    # ensure_session_envio es idempotente — la llamamos SIEMPRE para
    # garantizar que pyRofex tenga environment y default seteados, incluso
    # cuando el caller pasa `account` (scanner de triggers, etc.). Sin esto,
    # pyRofex.send_order tira ApiException("Environment not specify.") si
    # ningún endpoint del API tocó pyRofex antes en este proceso.
    _ensure_session()
    acc = account or cuenta_default()

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

    # pyRofex devuelve `order.clientId` y `order.proprietary`. El clientId
    # es el mismo identificador que después llega en los order_report como
    # `clOrdId` — lo usamos como cl_ord_id en Mongo. El proprietary hace
    # falta para cancelar (cancel_order pide ambos).
    order_blk = resp.get("order") or {}
    cl_ord_id = order_blk.get("clientId") or order_blk.get("clOrdId")
    proprietary = order_blk.get("proprietary")

    if not cl_ord_id:
        _audit("SEND_ERROR", account=acc, actor_email=actor_email,
               payload={"request": request_payload, "response": resp,
                        "note": "broker OK pero sin clientId"})
        return {"ok": False, "cl_ord_id": None, "status": "REJECTED_BROKER",
                "error": "broker no devolvió clientId", "broker_response": resp}

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
                "proprietary": proprietary,
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
            "proprietary": proprietary, "broker_response": resp}


def cancel_order(
    cl_ord_id: str,
    *,
    actor_email: str | None = None,
    proprietary: str | None = None,
) -> dict[str, Any]:
    """Cancela una orden por clOrdId. El estado real llega por order_report
    al motor — acá solo registramos el intento.

    pyRofex.cancel_order pide (client_order_id, proprietary). Resolución
    del proprietary, en orden:
      1. El que viene en `proprietary` (frontend lo envía cuando lo tiene
         del payload de /api/ordenes/dia — funciona para órdenes external).
      2. El persistido en OrdenesLive (lo guardamos al enviar desde acá).
    """
    acc = _ensure_session()
    _audit("CANCEL_REQUEST", cl_ord_id=cl_ord_id, account=acc,
           actor_email=actor_email,
           payload={"cl_ord_id": cl_ord_id, "proprietary_in": proprietary})

    if not proprietary:
        db = get_mongo_client()[DB_NAME]
        doc = db[COL_LIVE].find_one({"cl_ord_id": cl_ord_id}, {"proprietary": 1})
        if doc:
            proprietary = doc.get("proprietary")

    if not proprietary:
        _audit("CANCEL_ERROR", cl_ord_id=cl_ord_id, account=acc,
               actor_email=actor_email,
               payload={"reason": "proprietary no encontrado (ni en query ni en Mongo)"})
        return {"ok": False, "error": (
            "proprietary no disponible para cancelar. Si la orden vino de "
            "otra plataforma, abrila desde la web del broker para cancelar."
        )}

    try:
        resp = pyRofex.cancel_order(cl_ord_id, proprietary)
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


def _broker_report_to_local(rep: dict[str, Any]) -> dict[str, Any]:
    """Mapea un orderReport del broker al shape de OrdenesLive.

    No persiste — esto es para devolver al frontend órdenes que viven solo
    en el broker (operadas desde otra plataforma, ej. la web del broker).
    """
    instrument = rep.get("instrumentId") or {}
    ticker = instrument.get("symbol") or rep.get("symbol") or ""
    acc_field = rep.get("accountId")
    account = acc_field.get("id") if isinstance(acc_field, dict) else acc_field

    # Timestamps: pyRofex devuelve transactTime en formato propio
    # "20260520-15:11:26.794-0300" (FIX-like, no ISO 8601). Lo parseamos
    # custom para que el ordenamiento del front sea correcto.
    tt = rep.get("transactTime")
    created_at = None
    if isinstance(tt, (int, float)):
        try:
            created_at = datetime.fromtimestamp(tt / 1000, tz=UTC)
        except (OverflowError, ValueError):
            created_at = None
    elif isinstance(tt, str) and tt:
        # Caso 1: ISO 8601.
        try:
            created_at = datetime.fromisoformat(tt.replace("Z", "+00:00"))
        except ValueError:
            created_at = None
        # Caso 2: formato pyRofex "YYYYMMDD-HH:MM:SS.fff±ZZZZ".
        if created_at is None and "-" in tt and len(tt) >= 17:
            try:
                # Separar la parte de fecha-hora del offset final.
                # Buscamos el último '+' o '-' que sea offset (después del '.fff').
                base = tt[:18]  # "20260520-15:11:26."
                ms_and_tz = tt[18:]  # "794-0300"
                # Reformatear base a "YYYY-MM-DD HH:MM:SS."
                fmt_base = (
                    f"{base[0:4]}-{base[4:6]}-{base[6:8]} {base[9:17]}."
                )
                # ms (3 chars) + tz "+HHMM" o "-HHMM"
                ms = ms_and_tz[:3]
                tz_part = ms_and_tz[3:]  # "+0300" / "-0300"
                if len(tz_part) == 5 and tz_part[0] in ("+", "-"):
                    iso = f"{fmt_base}{ms}{tz_part[:3]}:{tz_part[3:]}"
                    created_at = datetime.fromisoformat(iso)
            except (ValueError, IndexError):
                created_at = None

    return {
        "cl_ord_id":     rep.get("clOrdId"),
        "ws_cl_ord_id":  rep.get("wsClOrdId"),
        "account":       account,
        "ticker":        ticker,
        "side":          rep.get("side"),
        "order_type":    rep.get("ordType"),
        "tif":           rep.get("timeInForce"),
        "size":          rep.get("orderQty"),
        "price":         rep.get("price"),
        "status":        rep.get("status"),
        "cum_qty":       rep.get("cumQty"),
        "leaves_qty":    rep.get("leavesQty"),
        "avg_px":        rep.get("avgPx"),
        "last_px":       rep.get("lastPx"),
        "last_qty":      rep.get("lastQty"),
        "reject_reason": rep.get("text"),
        "proprietary":   rep.get("proprietary"),
        "created_at":    created_at,
        "external":      True,  # marca: vino solo del broker, no de nuestra API
    }


def list_orders_dia(account: str | None = None, fecha: datetime | None = None) -> list[dict]:
    """Lista órdenes del día — merge live de Mongo + broker.

    Combina:
      1. `Operaciones.OrdenesLive` (lo que pasó por nuestra API, con
         actor_email + audit log + el clOrdId que motor_ordenes actualiza).
      2. `pyRofex.get_all_orders_status(account=X)` (lo que el broker ve
         hoy en esa cuenta, sin importar desde dónde se mandó — la web del
         broker, otra plataforma, etc).

    Join por clOrdId. Las que están en local + broker → broker pisa estado
    (más fresco). Las que están solo en broker → se devuelven marcadas como
    `external=true`. No persistimos nada nuevo — solo merge en memoria para
    la respuesta del endpoint. Cancelar una external requiere también
    `proprietary` que va en el payload.
    """
    acc = account or cuenta_default()
    if fecha is None:
        fecha = datetime.now(UTC)
    inicio = fecha.replace(hour=0, minute=0, second=0, microsecond=0)

    db = get_mongo_client()[DB_NAME]
    local = list(db[COL_LIVE].find(
        {"account": acc, "created_at": {"$gte": inicio}},
        {"_id": 0},
    ))
    by_cl_ord: dict[str, dict[str, Any]] = {
        o["cl_ord_id"]: o for o in local if o.get("cl_ord_id")
    }

    # Pegada al broker. Si falla, devolvemos solo lo local (degradación
    # graceful — no rompemos la vista si pyRofex está flaky).
    #
    # CLAVE: pyRofex devuelve UNA entry por cada cambio de estado. Cada
    # cancel request genera una entry NUEVA con su propio clOrdId que
    # apunta al original via `origClOrdId`. Si la orden original ya fue
    # cancelada/rejeada y alguien insiste con cancels, se acumulan N
    # entries PENDING_CANCEL (caso GD41D — 17 cancel requests sobre 1
    # sola orden REJECTED).
    #
    # Solución: resolver cadena origClOrdId → raíz, y por cada raíz
    # quedarnos con el ER de mayor transactTime. El frontend ve UNA fila
    # por orden con el estado actual.
    try:
        resp = pyRofex.get_all_orders_status(account=acc)
        if resp and resp.get("status") == "OK":
            reports = []
            for o in resp.get("orders", []) or []:
                rep = o.get("orderReport", o)
                if rep.get("clOrdId"):
                    reports.append(rep)

            # Mapa clOrdId → orig (si tiene). Para resolver raíz.
            parent_of = {
                r["clOrdId"]: r.get("origClOrdId")
                for r in reports
            }

            def _root(cid: str, depth: int = 0) -> str:
                """Sigue origClOrdId hasta que se acabe. Cap profundidad
                para evitar ciclos teóricos."""
                if depth > 20:
                    return cid
                parent = parent_of.get(cid)
                if not parent or parent == cid:
                    return cid
                return _root(parent, depth + 1)

            # Agrupar por raíz: ER con mayor transactTime gana.
            by_root: dict[str, tuple[str, dict]] = {}  # root → (transactTime, rep)
            for rep in reports:
                cid = rep["clOrdId"]
                root = _root(cid)
                tt = str(rep.get("transactTime") or "")
                if root in by_root and by_root[root][0] >= tt:
                    continue
                by_root[root] = (tt, rep)

            for root, (_tt, rep) in by_root.items():
                mapped = _broker_report_to_local(rep)
                # El cl_ord_id efectivo es el root (la orden original);
                # el cancel request se "absorbe" en su estado actual.
                mapped["cl_ord_id"] = root
                if root in by_cl_ord:
                    existing = by_cl_ord[root]
                    mapped["external"] = False
                    if existing.get("actor_email"):
                        mapped["actor_email"] = existing["actor_email"]
                    if existing.get("proprietary") and not mapped.get("proprietary"):
                        mapped["proprietary"] = existing["proprietary"]
                    if existing.get("created_at") and not mapped.get("created_at"):
                        mapped["created_at"] = existing["created_at"]
                    by_cl_ord[root] = {**existing, **mapped}
                else:
                    by_cl_ord[root] = mapped
    except Exception as e:
        logger.warning("list_orders_dia: merge broker falló (acc=%s): %s", acc, e)

    out = list(by_cl_ord.values())
    out.sort(key=lambda x: x.get("created_at") or datetime.min.replace(tzinfo=UTC), reverse=True)
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Catálogo de símbolos para autocomplete (PRUEBA → envío de órdenes raw)
# ─────────────────────────────────────────────────────────────────────────────


def search_symbols(q: str, limit: int = 20) -> list[dict[str, Any]]:
    """Busca instruments operables que matcheen `q` por substring de ticker
    o underlying. Lee Manager.PyRofexInstruments — la colección que pobla
    scripts.discovery_pyrofex con todos los instruments del broker.

    Excluye FCI (no son operables vía pyRofex; el dropdown del frontend
    no debe sugerirlos). Devuelve top `limit` resultados con los campos
    mínimos que el combobox necesita.
    """
    if not q or len(q.strip()) < 2:
        return []
    db = get_mongo_client_read()["Manager"]
    pattern = re.escape(q.strip())
    regex = {"$regex": pattern, "$options": "i"}
    fci_neg = {"$not": {"$regex": "FCI", "$options": "i"}}

    pipeline: list[dict[str, Any]] = [
        {"$unwind": "$instruments"},
        {"$match": {
            "$and": [
                {"$or": [
                    {"instruments.ticker":     regex},
                    {"instruments.underlying": regex},
                ]},
                {"instruments.ticker":     fci_neg},
                {"instruments.underlying": fci_neg},
            ],
        }},
        {"$limit": limit},
        {"$project": {
            "_id":        0,
            "ticker":     "$instruments.ticker",
            "underlying": "$instruments.underlying",
            "maturity":   "$instruments.maturity",
            "currency":   "$instruments.currency",
            "cficode":    "$_id",
        }},
    ]
    return list(db["PyRofexInstruments"].aggregate(pipeline))
