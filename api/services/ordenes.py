"""Servicio de órdenes (WRITE-SIDE) — funciones puras invocables desde routers o scripts.

Sin FastAPI ni HTTP. Pensado así para que también lo pueda usar un script
de smoke o un job futuro. Las funciones que mutan persisten en SQL
(`operaciones.ordenes_live` / `ordenes_audit`) ANTES de tocar al broker,
así si pyRofex tira o se cuelga el doc queda trackeable y el motor
(proceso aparte) lo va a ver vía recovery.

Diseño:
  - El proceso uvicorn levanta su propia sesión pyRofex liviana
    (`inicializar_para_envio`) — REST-only, sin WS.
  - El motor de órdenes (proceso aparte) tiene su propia sesión con WS
    suscripto a order_report. Es el único que escribe los ER en
    `operaciones.ordenes_live`.
  - Acá escribimos el doc inicial (PENDING_NEW) y el audit del request.
    Cuando llega el primer ER del broker el motor lo upsertea con el
    estado real (NEW / REJECTED / etc.).
  - Las LECTURAS (estado de una orden, listado del día con merge broker)
    viven en `api/services/ordenes_sql.py` — acá solo queda el builder puro
    `_broker_report_to_local` que ese módulo reusa.

Idempotencia:
  En V1 confiamos en el clOrdId que devuelve el broker. Si un cliente
  reintenta el mismo POST y el broker terminó aceptando ambos, vamos
  a tener 2 órdenes — explícito en la doc del endpoint. V2 puede sumar
  un `client_request_id` en el header y deduplicar acá.
"""
from __future__ import annotations

import logging
import time
from datetime import UTC, datetime
from typing import Any

import pyRofex

from core.postgres import get_pool
from core.rofex_orders_session import cuenta_default, ensure_session_envio, resolver_cuenta_rofex

logger = logging.getLogger("api.services.ordenes")


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
    # SQL-native (decomiso 2026-06-29): append a operaciones.ordenes_audit, sin Mongo.
    from core import pg_mirror
    pg_mirror.append_native("operaciones.ordenes_audit", [{
        "ts": datetime.now(UTC), "kind": kind, "cl_ord_id": cl_ord_id, "account": account,
        "actor_email": actor_email,
        "data": pg_mirror.doc_iso({"ws_cl_ord_id": None, "payload": payload or {}}),
    }])


def _upsert_live_sql(cl_ord_id: str, set_fields: dict, insert_only: dict | None = None) -> None:
    """Upsert SQL-native a operaciones.ordenes_live (read-modify-write, preserva el estado
    incremental). `set_fields` = siempre (campos no-null); `insert_only` = solo si la fila
    NO existe (equivale al $setOnInsert de Mongo: status PENDING_NEW, created_at, etc.).
    SQL-native (decomiso 2026-06-29): el envío de órdenes ya no escribe Mongo."""
    from psycopg.rows import dict_row
    from psycopg.types.json import Jsonb

    from core import pg_mirror
    from core.postgres import get_pool
    nuevos = {k: v for k, v in set_fields.items() if v is not None}
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT data FROM operaciones.ordenes_live WHERE cl_ord_id = %s", (cl_ord_id,))
        row = cur.fetchone()
        prev = (row["data"] if row else None) or {}
        merged = {**prev, **nuevos}
        if row is None and insert_only:
            merged = {**insert_only, **merged}  # insert_only no pisa set_fields/prev
        merged["cl_ord_id"] = cl_ord_id
        now = nuevos.get("updated_at") or datetime.now(UTC)
        cur.execute(
            "INSERT INTO operaciones.ordenes_live (cl_ord_id, account, ticker, estado, updated_at, data) "
            "VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (cl_ord_id) DO UPDATE SET "
            "account = EXCLUDED.account, ticker = EXCLUDED.ticker, estado = EXCLUDED.estado, "
            "updated_at = EXCLUDED.updated_at, data = EXCLUDED.data",
            (cl_ord_id, merged.get("account"), merged.get("ticker"), merged.get("status"),
             now, Jsonb(pg_mirror.doc_iso(merged))))
        conn.commit()


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
    client_order_id: str | None = None,
) -> dict[str, Any]:
    """Envía una orden con idempotencia opcional.

    Si `client_order_id` viene seteado, deduplica: un reenvío con la MISMA
    clave NO manda otra orden — devuelve el resultado del primer envío
    (anti doble-click / reintento). Una orden distinta lleva otra clave, así
    que nunca se bloquea una orden real. Sin `client_order_id` → llama al
    impl directo, comportamiento IDÉNTICO al de siempre (retrocompatible).
    """
    from api.services._idempotencia import ejecutar_idempotente

    return ejecutar_idempotente(
        client_order_id,
        lambda: _send_order_impl(
            ticker=ticker, side=side, size=size, order_type=order_type,
            price=price, tif=tif, account=account, actor_email=actor_email,
        ),
    )


def _send_order_impl(
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
    # El account que manda el front es el id_cuenta crudo de clientes.cuentas → lo
    # traducimos al nº que ROFEX acepta (medido + cacheado; algunas van crudas, otras con
    # cero a la izquierda, sin regla). Resolver ANTES de enviar la orden.
    acc = resolver_cuenta_rofex(account or cuenta_default())

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
    _upsert_live_sql(cl_ord_id, {
        "account": acc, "ticker": ticker, "side": side.upper(),
        "order_type": order_type.upper(), "tif": (tif or "DAY").upper(),
        "size": size, "price": price, "proprietary": proprietary,
        "actor_email": actor_email, "updated_at": now,
    }, insert_only={
        "status": "PENDING_NEW", "created_at": now.isoformat(),
        "cum_qty": 0, "leaves_qty": size,
    })

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
        from core.postgres import get_pool
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT data->>'proprietary' FROM operaciones.ordenes_live "
                        "WHERE cl_ord_id = %s", (cl_ord_id,))
            r = cur.fetchone()
        if r and r[0]:
            proprietary = r[0]

    if not proprietary:
        _audit("CANCEL_ERROR", cl_ord_id=cl_ord_id, account=acc,
               actor_email=actor_email,
               payload={"reason": "proprietary no encontrado (ni en query ni en SQL)"})
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


# ─────────────────────────────────────────────────────────────────────────────
# Catálogo de símbolos para autocomplete (PRUEBA → envío de órdenes raw)
# ─────────────────────────────────────────────────────────────────────────────


def search_symbols(q: str, limit: int = 20) -> list[dict[str, Any]]:
    """Busca instruments operables que matcheen `q` por substring de ticker
    o underlying. Lee manager.pyrofex_instruments (SQL) — la tabla que pobla
    scripts.discovery_pyrofex con todos los instruments del broker.

    Excluye FCI por CFI code (cuotapartes de fondos = cficode "CIO…"; ISO
    10962, 1ª letra C = Collective Investment Vehicles). La PK de la tabla ES
    el cficode, así que cortamos en la raíz: si el fondo no aparece acá no se
    puede pickear → no entra a AdhocSubscriptions → el motor nunca lo suscribe.
    Filtrar por nombre no servía: hay fondos sin "FCI" en el nombre (ej.
    "Toronto Trust Ahorro - Clase A").
    Devuelve top `limit` resultados con los campos mínimos del combobox.
    """
    if not q or len(q.strip()) < 2:
        return []
    # Escapar los metacaracteres de LIKE (\ % _) → match LITERAL como el
    # re.escape del path Mongo. ILIKE = substring case-insensitive.
    raw = q.strip()
    esc = raw.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    pattern = f"%{esc}%"
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT inst->>'ticker', inst->>'underlying', "
            "       inst->>'maturity', inst->>'currency', p.cficode "
            "FROM manager.pyrofex_instruments p, "
            "     jsonb_array_elements(p.instruments) AS inst "
            # Excluir fondos por cficode (raíz CIO…).
            "WHERE p.cficode NOT LIKE 'CIO%' "
            "  AND (inst->>'ticker' ILIKE %s OR inst->>'underlying' ILIKE %s) "
            "LIMIT %s",
            (pattern, pattern, limit),
        )
        rows = cur.fetchall()
    return [
        {"ticker": t, "underlying": u, "maturity": m, "currency": c, "cficode": cfi}
        for (t, u, m, c, cfi) in rows
    ]


# ─────────────────────────────────────────────────────────────────────────────
# FCI — suscripción / rescate
# ─────────────────────────────────────────────────────────────────────────────
# Universo y flujo SEPARADOS de los assets (send_order) para no romper nada.
# Los FCI (cficode "CIO…") se operan como LIMIT al precio de la cuota del día.
# Dato no obvio: el precio operable NO es el `last`, es la banda del día
# (lowLimitPrice == highLimitPrice == cuota oficial). El `last` es indicativo
# y suele diferir → mandar al last rebota fuera de banda. La orden va SIEMPRE
# por cantidad de cuotapartes (fraccionarias, según instrumentSizePrecision).

_FCI_CFI_PREFIX = "CIO"
_FCI_TTL_S = 300.0
# Cache process-wide del universo FCI. La cuota/banda cambia ~diario, así que
# 5 min es de sobra y evita traer los ~8600 instruments en cada request.
_fci_cache: dict[str, Any] = {"ts": 0.0, "by_ticker": {}}


def _fci_sym(inst: dict) -> str | None:
    sym = inst.get("symbol")
    if isinstance(sym, str) and sym:
        return sym
    iid = inst.get("instrumentId") or {}
    s = iid.get("symbol")
    return s if isinstance(s, str) and s else None


def _fci_universe() -> dict[str, dict[str, Any]]:
    """Universo de FCI (cficode CIO…) cacheado desde get_detailed_instruments.

    Fuente de verdad del precio operable: lowLimitPrice == highLimitPrice ==
    cuota del día. No depende de Mongo ni de discovery → siempre fresco.
    """
    now = time.time()
    cached = _fci_cache["by_ticker"]
    if cached and (now - _fci_cache["ts"]) < _FCI_TTL_S:
        return cached
    _ensure_session()
    res = pyRofex.get_detailed_instruments()
    by: dict[str, dict[str, Any]] = {}
    if res and res.get("status") == "OK":
        for inst in res.get("instruments") or []:
            if not (inst.get("cficode") or "").startswith(_FCI_CFI_PREFIX):
                continue
            sym = _fci_sym(inst)
            if not sym:
                continue
            by[sym] = {
                "ticker": sym,
                "underlying": inst.get("underlying"),
                "currency": inst.get("currency"),
                "size_precision": inst.get("instrumentSizePrecision"),
                "settl_type": inst.get("settlType"),
                "low_limit": inst.get("lowLimitPrice"),
                "high_limit": inst.get("highLimitPrice"),
                "min_trade_vol": inst.get("minTradeVol"),
            }
    if by:
        _fci_cache["by_ticker"] = by
        _fci_cache["ts"] = now
    return by


def _fci_cuota(ticker: str, meta: dict[str, Any]) -> float | None:
    """Precio operable del FCI = banda (low==high). Fallback a market data
    LAST/CLOSE si la banda viene vacía (raro)."""
    for k in ("low_limit", "high_limit"):
        v = meta.get(k)
        if v:
            return float(v)
    try:
        md = pyRofex.get_market_data(
            ticker,
            entries=[pyRofex.MarketDataEntry.LAST, pyRofex.MarketDataEntry.CLOSING_PRICE],
        )
        data = (md or {}).get("marketData", {}) or {}
        la = data.get("LA") or {}
        if la.get("price"):
            return float(la["price"])
        cl = data.get("CL")
        if isinstance(cl, dict) and cl.get("price"):
            return float(cl["price"])
        if isinstance(cl, (int, float)) and cl:
            return float(cl)
    except Exception as e:
        logger.warning("FCI cuota market-data fallback falló (%s): %s", ticker, e)
    return None


def _settl_label(settl_type) -> str:
    return {1: "T+0", 2: "T+1", 3: "T+2",
            "1": "T+0", "2": "T+1", "3": "T+2"}.get(settl_type, f"settl {settl_type}")


def search_fci(q: str, limit: int = 30) -> list[dict[str, Any]]:
    """Busca FCI (cficode CIO…) por substring de ticker o underlying.

    Universo OPUESTO a search_symbols (que excluye CIO). Lee el universo live
    cacheado — no toca el flujo de assets.
    """
    if not q or len(q.strip()) < 2:
        return []
    ql = q.strip().lower()
    out: list[dict[str, Any]] = []
    for meta in _fci_universe().values():
        tk = meta.get("ticker") or ""
        und = meta.get("underlying") or ""
        if ql in tk.lower() or ql in und.lower():
            out.append({
                "ticker": tk,
                "underlying": meta.get("underlying"),
                "currency": meta.get("currency"),
                "settl_type": meta.get("settl_type"),
                "plazo": _settl_label(meta.get("settl_type")),
            })
            if len(out) >= limit:
                break
    out.sort(key=lambda d: d["ticker"])
    return out


def get_fci_quote(ticker: str) -> dict[str, Any]:
    """Cuota + metadata operable de un FCI — alimenta la conversión
    importe ↔ cuotapartes en la UI."""
    meta = _fci_universe().get(ticker)
    if not meta:
        raise ValueError(f"FCI no encontrado: {ticker!r}")
    cuota = _fci_cuota(ticker, meta)
    return {
        "ticker": ticker,
        "underlying": meta.get("underlying"),
        "currency": meta.get("currency"),
        "cuota": cuota,
        "size_precision": meta.get("size_precision"),
        "min_trade_vol": meta.get("min_trade_vol"),
        "settl_type": meta.get("settl_type"),
        "plazo": _settl_label(meta.get("settl_type")),
    }


def send_fci_order(
    *,
    ticker: str,
    side: str,
    amount: float,
    amount_mode: str = "cuotapartes",
    account: str | None = None,
    actor_email: str | None = None,
    client_order_id: str | None = None,
) -> dict[str, Any]:
    """Suscripción/rescate de FCI con idempotencia opcional. Mismo patrón que
    send_order: clave repetida → no manda otra; sin clave → idéntico."""
    from api.services._idempotencia import ejecutar_idempotente

    return ejecutar_idempotente(
        client_order_id,
        lambda: _send_fci_order_impl(
            ticker=ticker, side=side, amount=amount, amount_mode=amount_mode,
            account=account, actor_email=actor_email,
        ),
    )


def _send_fci_order_impl(
    *,
    ticker: str,
    side: str,
    amount: float,
    amount_mode: str = "cuotapartes",
    account: str | None = None,
    actor_email: str | None = None,
) -> dict[str, Any]:
    """Suscripción (BUY) / rescate (SELL) de un FCI.

    La orden va SIEMPRE por cantidad de cuotapartes, como LIMIT al precio de
    la cuota del día (banda low==high). Si `amount_mode='importe'`, convierte
    importe → cuotapartes con la cuota live (autoritativa, server-side).
    """
    s = (side or "").strip().upper()
    if s not in ("BUY", "SELL"):
        raise ValueError("side debe ser BUY (suscripción) o SELL (rescate)")
    if amount is None or amount <= 0:
        raise ValueError("amount debe ser > 0")
    mode = (amount_mode or "cuotapartes").strip().lower()
    if mode not in ("cuotapartes", "importe"):
        raise ValueError("amount_mode debe ser 'cuotapartes' o 'importe'")

    meta = _fci_universe().get(ticker)
    if not meta:
        raise ValueError(f"FCI no encontrado: {ticker!r}")
    cuota = _fci_cuota(ticker, meta)
    if not cuota or cuota <= 0:
        raise ValueError(f"Sin cuota operable para {ticker} (¿mercado cerrado?)")

    sp = meta.get("size_precision")
    ndig = int(sp) if isinstance(sp, (int, float)) else 4
    cuotapartes = round(amount / cuota, ndig) if mode == "importe" else round(amount, ndig)
    if cuotapartes <= 0:
        raise ValueError("la cantidad de cuotapartes resultó 0 — subí el importe")

    _ensure_session()
    acc = account or cuenta_default()
    op = "SUSCRIPCION" if s == "BUY" else "RESCATE"
    request_payload = {
        "ticker": ticker, "side": s, "op": op, "kind": "FCI",
        "amount": amount, "amount_mode": mode,
        "cuota": cuota, "cuotapartes": cuotapartes, "account": acc,
    }
    _audit("FCI_SEND_REQUEST", account=acc, actor_email=actor_email, payload=request_payload)

    try:
        resp = pyRofex.send_order(
            ticker=ticker,
            side=_side_enum(s),
            size=cuotapartes,
            price=cuota,
            order_type=pyRofex.OrderType.LIMIT,
            time_in_force=_tif_enum("DAY"),
            account=acc,
            cancel_previous=False,
        )
    except Exception as e:
        logger.error("send_fci_order falló: %s", e, exc_info=True)
        _audit("FCI_SEND_ERROR", account=acc, actor_email=actor_email,
               payload={"request": request_payload, "exception": str(e)})
        return {"ok": False, "cl_ord_id": None, "status": "REJECTED_LOCAL", "error": str(e)}

    if not resp or resp.get("status") != "OK":
        _audit("FCI_SEND_ERROR", account=acc, actor_email=actor_email,
               payload={"request": request_payload, "response": resp})
        return {"ok": False, "cl_ord_id": None, "status": "REJECTED_BROKER",
                "error": (resp or {}).get("description", "broker rechazó la orden"),
                "broker_response": resp}

    order_blk = resp.get("order") or {}
    cl_ord_id = order_blk.get("clientId") or order_blk.get("clOrdId")
    proprietary = order_blk.get("proprietary")
    if not cl_ord_id:
        _audit("FCI_SEND_ERROR", account=acc, actor_email=actor_email,
               payload={"request": request_payload, "response": resp, "note": "OK sin clientId"})
        return {"ok": False, "cl_ord_id": None, "status": "REJECTED_BROKER",
                "error": "broker no devolvió clientId", "broker_response": resp}

    now = datetime.now(UTC)
    _upsert_live_sql(cl_ord_id, {
        "account": acc, "ticker": ticker, "side": s,
        "order_type": "LIMIT", "tif": "DAY", "size": cuotapartes, "price": cuota,
        "kind": "FCI", "op": op, "proprietary": proprietary,
        "actor_email": actor_email, "updated_at": now,
    }, insert_only={
        "status": "PENDING_NEW", "created_at": now.isoformat(),
        "cum_qty": 0, "leaves_qty": cuotapartes,
    })
    _audit("FCI_SEND_OK", cl_ord_id=cl_ord_id, account=acc, actor_email=actor_email,
           payload={"request": request_payload, "response": resp})
    return {"ok": True, "cl_ord_id": cl_ord_id, "status": "PENDING_NEW",
            "op": op, "cuotapartes": cuotapartes, "cuota": cuota,
            "proprietary": proprietary, "broker_response": resp}
