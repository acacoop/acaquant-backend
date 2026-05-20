"""Operativa Dólar MEP — wrapper de 2 órdenes MARKET (BUY AL30 + SELL AL30D).

Concepto: el cliente entra ARS, sale USD MEP. Lo logramos con 2 patas:
  - BUY  AL30  MARKET (en ARS)
  - SELL AL30D MARKET (en USD)
Misma especie subyacente (AL30), distintas ruedas según el ticker (CI o 24hs).
La operativa NO es atómica — entre la BUY y la SELL los precios pueden moverse,
pero para AL30/AL30D ultra-líquidos el slippage es chico.

Persistencia:
  Operaciones.OperativasMep:
    {
      _id, fecha, account, actor_email,
      monto_ars, comision_pct,
      rueda,                # "CI" | "24hs"
      precio_al30_inicial, precio_al30d_inicial, mep_inicial, nominales,
      buy:  { cl_ord_id, ticker },
      sell: { cl_ord_id, ticker },
      status,               # PENDING | OK_PARCIAL | OK | FAIL
      created_at, updated_at
    }
  Las dos patas (cl_ord_id) viven en Operaciones.OrdenesLive — no duplicamos
  estado de ER, joineamos al leer.

Cotización live: lee Trading.TimeSales del último trade del ticker (lo
mantiene motor_rofex en tiempo real).
"""
from __future__ import annotations

import logging
import math
import time
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pyRofex

from api.services.ordenes import send_order
from core.mongo import get_mongo_client, get_mongo_client_read

logger = logging.getLogger("api.services.operativa_mep")

DB_OPS = "Operaciones"
COL_OPERATIVAS = "OperativasMep"
COL_ORDENES = "OrdenesLive"

DB_TRADING = "Trading"
COL_TIMESALES = "TimeSales"

# motor_rofex (engines/valores.py) escribe `timestamp` como naive ART
# (utcnow() llevado a ART y replace(tzinfo=None)). Mongo lo guarda como UTC,
# entonces los ts quedan -3h vs UTC real. Para devolver ts honestos en
# /timesales hay que sumar 3h y rotular UTC. Constante duplicada de
# triggers_mep.py — convención del proyecto: NO tocar el motor.
MOTOR_TS_OFFSET = timedelta(hours=3)

# Tickers según rueda. El frontend solo manda "CI" o "24hs" — los símbolos
# full quedan acá, no exponemos detalles del broker a la UI.
TICKERS_POR_RUEDA: dict[str, dict[str, str]] = {
    "CI": {
        "al30":  "MERV - XMEV - AL30 - CI",
        "al30d": "MERV - XMEV - AL30D - CI",
    },
    "24hs": {
        "al30":  "MERV - XMEV - AL30 - 24hs",
        "al30d": "MERV - XMEV - AL30D - 24hs",
    },
}

RUEDAS_VALIDAS = set(TICKERS_POR_RUEDA.keys())

ESTADOS_FINALES_ORDEN = {"FILLED", "CANCELLED", "REJECTED", "EXPIRED"}

# Convención BYMA: los bonos cotizan precio por cada 100 VN. Para pasar a
# precio por 1 VN (que es la unidad de `size` en la orden) hay que multiplicar
# por 0.01. En get_detailed_position aparece como `priceConversionFactor`.
PRICE_FACTOR_BONOS = 0.01

# Estados de la BUY que IMPIDEN mandar la SELL — la BUY no se va a llenar,
# entonces vender AL30D dispararía short si la cuenta tiene tenencia previa.
BUY_BLOQUEA_SELL = {"REJECTED", "CANCELLED", "EXPIRED", "UNKNOWN_LOCAL"}

# Estados de la BUY que CONFIRMAN que llegó al book — recién ahí mandamos SELL.
BUY_HABILITA_SELL = {"NEW", "PARTIALLY_FILLED", "FILLED"}

# Timeouts del polling REST contra el broker. AL30 MARKET en mercado abierto
# fillea en milisegundos. 5s es generoso para tolerar lag de red sin bloquear
# al user demasiado. Se chequea cada 200ms — 25 fetches max por orden.
ORDER_POLL_TIMEOUT_S = 5.0
ORDER_POLL_INTERVAL_S = 0.2


# ─────────────────────────────────────────────────────────────────────────────
# Cotización live
# ─────────────────────────────────────────────────────────────────────────────


def _last_trade(ticker: str) -> dict[str, Any] | None:
    """Último trade de un ticker desde Trading.TimeSales. Devuelve {price, ts} o None."""
    db = get_mongo_client_read()[DB_TRADING]
    doc = db[COL_TIMESALES].find_one(
        {"ticker": ticker},
        sort=[("timestamp", -1)],
        projection={"_id": 0, "price": 1, "timestamp": 1},
    )
    if not doc:
        return None
    ts = doc.get("timestamp")
    return {
        "price": doc.get("price"),
        "ts": ts.isoformat() if isinstance(ts, datetime) else ts,
    }


def get_cotizaciones(rueda: str) -> dict[str, Any]:
    """Devuelve last AL30 + AL30D + MEP implícito para una rueda."""
    if rueda not in RUEDAS_VALIDAS:
        raise ValueError(f"rueda inválida: {rueda!r} (esperado: {sorted(RUEDAS_VALIDAS)})")
    tk = TICKERS_POR_RUEDA[rueda]
    al30 = _last_trade(tk["al30"])
    al30d = _last_trade(tk["al30d"])
    mep = None
    if al30 and al30d:
        try:
            p_a, p_d = float(al30["price"]), float(al30d["price"])
            if p_d > 0:
                mep = round(p_a / p_d, 2)
        except (TypeError, ValueError):
            pass
    return {
        "rueda": rueda,
        "al30": al30,    # {price, ts} o None
        "al30d": al30d,
        "mep_implicito": mep,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Crear operativa
# ─────────────────────────────────────────────────────────────────────────────


def _wait_order_filled_rest(
    cl_ord_id: str,
    proprietary: str | None,
    timeout_s: float = ORDER_POLL_TIMEOUT_S,
    interval_s: float = ORDER_POLL_INTERVAL_S,
) -> tuple[str | None, dict | None]:
    """Pollea pyRofex.get_order_status (REST) hasta status final o timeout.

    Va directo al broker — no depende del motor_ordenes ni del WS ni de
    Mongo. Si el broker dice FILLED, la orden está llena. La sesión
    pyRofex del API (que llama esto) está autenticada con la cuenta
    master, y `get_order_status` resuelve por clOrdId — la subcuenta
    de la orden no afecta la consulta.

    Devuelve (status, order_dict). status=None si nunca pudimos parsear
    una respuesta válida del broker.
    """
    deadline = time.monotonic() + timeout_s
    last_order: dict | None = None
    while time.monotonic() < deadline:
        try:
            resp = pyRofex.get_order_status(cl_ord_id, proprietary)
        except Exception as e:
            logger.warning("get_order_status falló (cl_ord_id=%s): %s", cl_ord_id, e)
            time.sleep(interval_s)
            continue
        if resp and resp.get("status") == "OK":
            order = resp.get("order") or {}
            last_order = order
            st = order.get("status")
            if st in ESTADOS_FINALES_ORDEN or st in {"NEW", "PARTIALLY_FILLED"}:
                return st, order
        time.sleep(interval_s)
    return (last_order or {}).get("status"), last_order


def _persistir_orden_live(
    cl_ord_id: str,
    *,
    account: str | None,
    ticker: str,
    side: str,
    order: dict | None,
) -> None:
    """Snapshot REST del estado de una orden en Operaciones.OrdenesLive +
    audit en OrdenesAudit.

    Reemplaza la dependencia del motor_ordenes para esta operativa: el
    flujo de operativa_mep escribe el estado final directamente desde
    el resultado del polling REST, así `listar_operativas_dia` lo lee
    sin esperar que el WS del motor llegue. El motor sigue corriendo
    para audit general, pero no es bloqueante.

    También deja un `kind=REST_SNAPSHOT` en OrdenesAudit por cada llamada
    para que el drilldown del frontend tenga al menos un timeline aunque
    el motor no haya recibido el ER por WS (caso típico: subcuenta no
    suscripta).
    """
    if not order:
        return
    db = get_mongo_client()[DB_OPS]
    now = datetime.now(UTC)
    db[COL_ORDENES].update_one(
        {"cl_ord_id": cl_ord_id},
        {"$set": {
            "ticker":     ticker,
            "side":       side.upper(),
            "account":    account,
            "status":     order.get("status"),
            "cum_qty":    order.get("cumQty"),
            "leaves_qty": order.get("leavesQty"),
            "avg_px":     order.get("avgPx"),
            "last_px":    order.get("lastPx"),
            "last_qty":   order.get("lastQty"),
            "updated_at": now,
            "source":     "rest_poll",
        }},
        upsert=True,
    )
    db["OrdenesAudit"].insert_one({
        "ts":         now,
        "kind":       "REST_SNAPSHOT",
        "cl_ord_id":  cl_ord_id,
        "account":    account,
        "payload":    order,
    })


def _ejecutar_buy_then_sell(
    *,
    buy_ticker: str,
    sell_ticker: str,
    nominales: int,
    account: str | None,
    actor_email: str | None,
) -> dict[str, Any]:
    """Manda BUY MARKET, espera ER, y si confirma manda SELL MARKET.

    Lógica común entre operativa de compra MEP (BUY AL30 + SELL AL30D) y
    venta MEP (BUY AL30D + SELL AL30). Si la BUY rechaza/expira/timeouts,
    NO se manda SELL para evitar shorts involuntarios contra tenencia previa.

    Returns:
        {
          "stage":          "buy_ack" | "buy_er" | "sell" | "all_ok",
          "global_status":  "OK" | "OK_PARCIAL" | "FAIL" | "STALE_BUY",
          "buy":            {cl_ord_id, status, ok, reason?},
          "sell":           {cl_ord_id, status, ok, error?} | None,
          "error":          str | None,
        }
    """
    # 1. Mandar BUY al broker (REST, sincrónico).
    buy_resp = send_order(
        ticker=buy_ticker, side="BUY", size=nominales,
        order_type="MARKET", price=None, tif="DAY",
        account=account, actor_email=actor_email,
    )
    if not buy_resp.get("ok"):
        return {
            "stage": "buy_ack", "global_status": "FAIL",
            "buy": {"cl_ord_id": None, "status": "REJECTED_LOCAL",
                    "ok": False, "reason": buy_resp.get("error")},
            "sell": None, "error": buy_resp.get("error"),
        }
    buy_cl_ord_id = buy_resp["cl_ord_id"]
    buy_proprietary = buy_resp.get("proprietary")

    # 2. Esperar resultado de la BUY pegando al broker REST. No dependemos
    # del WS ni del motor_ordenes — `get_order_status` resuelve por clOrdId.
    buy_status, buy_order = _wait_order_filled_rest(buy_cl_ord_id, buy_proprietary)
    _persistir_orden_live(
        buy_cl_ord_id, account=account, ticker=buy_ticker, side="BUY", order=buy_order,
    )
    buy_reason = (buy_order or {}).get("text")

    if buy_status in BUY_BLOQUEA_SELL:
        logger.warning(
            "BUY %s rechazada/cancelada (status=%s, reason=%s) — NO mandamos SELL",
            buy_cl_ord_id, buy_status, buy_reason,
        )
        return {
            "stage": "buy_er", "global_status": "FAIL",
            "buy": {"cl_ord_id": buy_cl_ord_id, "status": buy_status,
                    "ok": False, "reason": buy_reason},
            "sell": None, "error": buy_reason or buy_status,
        }

    if buy_status not in BUY_HABILITA_SELL:
        motivo = f"BUY no resuelta en {ORDER_POLL_TIMEOUT_S}s (status={buy_status})"
        logger.warning(motivo)
        return {
            "stage": "buy_er", "global_status": "STALE_BUY",
            "buy": {"cl_ord_id": buy_cl_ord_id, "status": buy_status, "ok": False},
            "sell": None, "error": motivo,
        }

    # 3. Mandar SELL al broker.
    sell_resp = send_order(
        ticker=sell_ticker, side="SELL", size=nominales,
        order_type="MARKET", price=None, tif="DAY",
        account=account, actor_email=actor_email,
    )
    if not sell_resp.get("ok"):
        return {
            "stage": "sell", "global_status": "OK_PARCIAL",
            "buy": {"cl_ord_id": buy_cl_ord_id, "status": buy_status, "ok": True},
            "sell": {"cl_ord_id": None, "status": "REJECTED_LOCAL",
                     "ok": False, "error": sell_resp.get("error")},
            "error": sell_resp.get("error"),
        }
    sell_cl_ord_id = sell_resp["cl_ord_id"]
    sell_proprietary = sell_resp.get("proprietary")

    # 4. Esperar resultado de la SELL — devolvemos al frontend el estado
    # confirmado por el broker, no un PENDING_NEW que después tiene que
    # actualizar el motor.
    sell_status, sell_order = _wait_order_filled_rest(sell_cl_ord_id, sell_proprietary)
    _persistir_orden_live(
        sell_cl_ord_id, account=account, ticker=sell_ticker, side="SELL", order=sell_order,
    )
    sell_filled_ok = sell_status in {"FILLED", "PARTIALLY_FILLED"}

    return {
        "stage": "all_ok" if sell_filled_ok else "sell",
        "global_status": "OK" if sell_filled_ok else "OK_PARCIAL",
        "buy": {"cl_ord_id": buy_cl_ord_id, "status": buy_status, "ok": True},
        "sell": {"cl_ord_id": sell_cl_ord_id, "status": sell_status or "UNKNOWN",
                 "ok": sell_filled_ok,
                 "error": None if sell_filled_ok else f"SELL en estado {sell_status}"},
        "error": None if sell_filled_ok else f"SELL en estado {sell_status}",
    }


def crear_operativa(
    *,
    monto_ars: float,
    comision_pct: float = 0.62,
    rueda: str = "CI",
    account: str | None = None,
    actor_email: str | None = None,
) -> dict[str, Any]:
    """Calcula nominales, manda BUY AL30 + SELL AL30D, persiste el wrapper.

    Si la BUY rechaza, NO mandamos la SELL (no tendríamos qué vender).
    Si la BUY OK pero la SELL rechaza, marca status=OK_PARCIAL — el user va
    a tener que liquidar la posición de AL30 manualmente.
    """
    if rueda not in RUEDAS_VALIDAS:
        raise ValueError(f"rueda inválida: {rueda!r} (esperado: {sorted(RUEDAS_VALIDAS)})")
    if monto_ars <= 0:
        raise ValueError("monto_ars debe ser > 0")
    if comision_pct < 0 or comision_pct > 5:
        raise ValueError("comision_pct fuera de rango (esperado [0, 5])")

    tk = TICKERS_POR_RUEDA[rueda]
    cot = get_cotizaciones(rueda)
    if not cot["al30"] or not cot["al30d"]:
        raise ValueError(
            "Sin cotización live para AL30/AL30D en rueda "
            f"{rueda} — el motor de market data no tiene trades recientes."
        )
    precio_al30 = float(cot["al30"]["price"])
    precio_al30d = float(cot["al30d"]["price"])
    if precio_al30 <= 0:
        raise ValueError("precio AL30 <= 0, no se puede operar")

    # Precio por VN (no por 100 VN como cotiza pantalla).
    precio_al30_vn = precio_al30 * PRICE_FACTOR_BONOS
    ars_neto = monto_ars * (1.0 - comision_pct / 100.0)
    nominales = math.floor(ars_neto / precio_al30_vn) if precio_al30_vn > 0 else 0

    # Persistimos el doc SIEMPRE — incluso cuando nominales=0 — para que el
    # user vea todos los intentos en la tabla. Solo si nominales>0 vamos
    # al broker; sino marcamos FAIL_VALIDACION y devolvemos.
    operativa_id = str(uuid4())
    now = datetime.now(UTC)
    db_ops = get_mongo_client()[DB_OPS]
    doc = {
        "operativa_id": operativa_id,
        "tipo": "compra",
        "fecha": now.strftime("%Y-%m-%d"),
        "account": account,
        "actor_email": actor_email,
        "monto_ars": monto_ars,
        "comision_pct": comision_pct,
        "rueda": rueda,
        "precio_al30_inicial": precio_al30,
        "precio_al30d_inicial": precio_al30d,
        "mep_inicial": cot["mep_implicito"],
        "nominales": nominales,
        "buy": {"cl_ord_id": None, "ticker": tk["al30"]},
        "sell": {"cl_ord_id": None, "ticker": tk["al30d"]},
        "status": "PENDING",
        "created_at": now,
        "updated_at": now,
    }
    db_ops[COL_OPERATIVAS].insert_one(doc)

    if nominales <= 0:
        motivo = (
            f"nominales=0 (ars_neto=${ars_neto:.2f} / precio_VN=${precio_al30_vn:.2f}). "
            "Subí el monto o bajá la comisión."
        )
        db_ops[COL_OPERATIVAS].update_one(
            {"operativa_id": operativa_id},
            {"$set": {
                "status": "FAIL_VALIDACION",
                "buy_error": motivo,
                "updated_at": datetime.now(UTC),
            }},
        )
        return {
            "ok": False,
            "operativa_id": operativa_id,
            "status": "FAIL_VALIDACION",
            "stage": "validacion",
            "error": motivo,
        }

    res = _ejecutar_buy_then_sell(
        buy_ticker=tk["al30"],
        sell_ticker=tk["al30d"],
        nominales=nominales,
        account=account,
        actor_email=actor_email,
    )
    return _persistir_resultado_operativa(operativa_id, nominales, res, db_ops)


def crear_operativa_venta(
    *,
    nominales: int,
    rueda: str = "CI",
    account: str | None = None,
    actor_email: str | None = None,
    parent_trigger_id: str | None = None,
) -> dict[str, Any]:
    """Cierre de posición MEP: USD → ARS. Vende los nominales que vinieron de
    una operativa de compra previa (o que tenga la cuenta).

    Mecánica: BUY AL30D MARKET (recompra los AL30D que se vendieron en la
    entry, cancela el short) + SELL AL30 MARKET (vende los AL30 que se
    compraron en la entry). Mismo guard de BUY antes que SELL.

    `parent_trigger_id` queda en el doc para auditoría — el scanner lo
    setea cuando dispara la salida de un trigger.
    """
    if rueda not in RUEDAS_VALIDAS:
        raise ValueError(f"rueda inválida: {rueda!r} (esperado: {sorted(RUEDAS_VALIDAS)})")
    if nominales <= 0:
        raise ValueError("nominales debe ser > 0")

    tk = TICKERS_POR_RUEDA[rueda]
    cot = get_cotizaciones(rueda)
    precio_al30 = float(cot["al30"]["price"]) if cot["al30"] else None
    precio_al30d = float(cot["al30d"]["price"]) if cot["al30d"] else None

    operativa_id = str(uuid4())
    now = datetime.now(UTC)
    db_ops = get_mongo_client()[DB_OPS]
    doc = {
        "operativa_id": operativa_id,
        "tipo": "venta",
        "parent_trigger_id": parent_trigger_id,
        "fecha": now.strftime("%Y-%m-%d"),
        "account": account,
        "actor_email": actor_email,
        "rueda": rueda,
        "precio_al30_inicial": precio_al30,
        "precio_al30d_inicial": precio_al30d,
        "mep_inicial": cot["mep_implicito"],
        "nominales": nominales,
        # Patas invertidas vs. compra: comprás AL30D (cancelás short) y
        # vendés AL30 (cerrás long).
        "buy":  {"cl_ord_id": None, "ticker": tk["al30d"]},
        "sell": {"cl_ord_id": None, "ticker": tk["al30"]},
        "status": "PENDING",
        "created_at": now,
        "updated_at": now,
    }
    db_ops[COL_OPERATIVAS].insert_one(doc)

    res = _ejecutar_buy_then_sell(
        buy_ticker=tk["al30d"],
        sell_ticker=tk["al30"],
        nominales=nominales,
        account=account,
        actor_email=actor_email,
    )
    return _persistir_resultado_operativa(operativa_id, nominales, res, db_ops)


def crear_operativa_venta(
    *,
    monto_usd: float,
    comision_pct: float = 0.62,
    rueda: str = "CI",
    account: str | None = None,
    actor_email: str | None = None,
) -> dict[str, Any]:
    """Wrapper "venta MEP" análogo a `crear_operativa` (compra).

    Recibe `monto_usd` que el user quiere convertir a pesos, calcula los
    nominales de AL30D a operar (= ese monto neto / precio AL30D por VN)
    y delega en `operativa_venta_mep`. La operativa inversa:
        BUY AL30D (recompra los AL30D, paga en USD)
        SELL AL30 (vende AL30, recibe ARS)

    Si nominales=0 (monto < precio mínimo), persiste FAIL_VALIDACION.
    """
    if rueda not in RUEDAS_VALIDAS:
        raise ValueError(f"rueda inválida: {rueda!r} (esperado: {sorted(RUEDAS_VALIDAS)})")
    if monto_usd <= 0:
        raise ValueError("monto_usd debe ser > 0")
    if comision_pct < 0 or comision_pct > 5:
        raise ValueError("comision_pct fuera de rango (esperado [0, 5])")

    cot = get_cotizaciones(rueda)
    if not cot["al30"] or not cot["al30d"]:
        raise ValueError(
            "Sin cotización live para AL30/AL30D en rueda "
            f"{rueda} — el motor de market data no tiene trades recientes."
        )
    precio_al30d = float(cot["al30d"]["price"])
    if precio_al30d <= 0:
        raise ValueError("precio AL30D <= 0, no se puede operar")

    # Precio por VN (no por 100 VN como cotiza pantalla).
    precio_al30d_vn = precio_al30d * PRICE_FACTOR_BONOS
    usd_neto = monto_usd * (1.0 - comision_pct / 100.0)
    nominales = math.floor(usd_neto / precio_al30d_vn) if precio_al30d_vn > 0 else 0

    if nominales <= 0:
        motivo = (
            f"nominales=0 (usd_neto=US${usd_neto:.2f} / precio_VN=US${precio_al30d_vn:.4f}). "
            "Subí el monto o bajá la comisión."
        )
        return {
            "ok": False,
            "status": "FAIL_VALIDACION",
            "stage": "validacion",
            "error": motivo,
        }

    return operativa_venta_mep(
        nominales=nominales,
        rueda=rueda,
        account=account,
        actor_email=actor_email,
    )


def _persistir_resultado_operativa(
    operativa_id: str,
    nominales: int,
    res: dict[str, Any],
    db_ops,
) -> dict[str, Any]:
    """Toma el resultado de _ejecutar_buy_then_sell y persiste el update final
    en OperativasMep. Devuelve el dict que sube al router."""
    buy = res.get("buy") or {}
    sell = res.get("sell")
    global_status = res["global_status"]

    update_set = {
        "buy.cl_ord_id": buy.get("cl_ord_id"),
        "status": global_status,
        "updated_at": datetime.now(UTC),
    }
    if buy.get("reason") or res.get("error"):
        update_set["buy_error"] = buy.get("reason") or res.get("error")
    if sell:
        update_set["sell.cl_ord_id"] = sell.get("cl_ord_id")
        if sell.get("error"):
            update_set["sell_error"] = sell.get("error")

    db_ops[COL_OPERATIVAS].update_one(
        {"operativa_id": operativa_id},
        {"$set": update_set},
    )

    return {
        "ok":            global_status in {"OK", "OK_PARCIAL"},
        "operativa_id":  operativa_id,
        "status":        global_status,
        "stage":         res.get("stage"),
        "error":         res.get("error"),
        "buy":           buy,
        "sell":          sell,
        "nominales":     nominales,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Listado del día con enrich
# ─────────────────────────────────────────────────────────────────────────────


def _enrich_pata(orden: dict | None) -> dict[str, Any] | None:
    """Reduce el doc de OrdenesLive a lo que necesita la tabla del front."""
    if not orden:
        return None
    return {
        "cl_ord_id":     orden.get("cl_ord_id"),
        "ticker":        orden.get("ticker"),
        "status":        orden.get("status"),
        "cum_qty":       orden.get("cum_qty"),
        "leaves_qty":    orden.get("leaves_qty"),
        "avg_px":        orden.get("avg_px"),
        "reject_reason": orden.get("reject_reason"),
    }


def listar_operativas_dia(account: str | None = None) -> list[dict]:
    """Lista operativas del día UTC con join a OrdenesLive y métricas calculadas."""
    db_ops = get_mongo_client_read()[DB_OPS]
    inicio = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    filtro: dict[str, Any] = {"created_at": {"$gte": inicio}}
    if account:
        filtro["account"] = account

    operativas = list(
        db_ops[COL_OPERATIVAS]
        .find(filtro, {"_id": 0})
        .sort("created_at", -1)
    )
    if not operativas:
        return []

    # Traer las OrdenesLive de las patas de un saque
    cl_ord_ids: list[str] = []
    for op in operativas:
        for pata in ("buy", "sell"):
            cid = (op.get(pata) or {}).get("cl_ord_id")
            if cid:
                cl_ord_ids.append(cid)
    ordenes_by_id: dict[str, dict] = {}
    if cl_ord_ids:
        for d in db_ops[COL_ORDENES].find(
            {"cl_ord_id": {"$in": cl_ord_ids}}, {"_id": 0},
        ):
            ordenes_by_id[d["cl_ord_id"]] = d

    out: list[dict] = []
    for op in operativas:
        buy_cid = (op.get("buy") or {}).get("cl_ord_id")
        sell_cid = (op.get("sell") or {}).get("cl_ord_id")
        buy_ord = ordenes_by_id.get(buy_cid) if buy_cid else None
        sell_ord = ordenes_by_id.get(sell_cid) if sell_cid else None

        # USD efectivo: cum_sell * avg_sell * 0.01 (los bonos cotizan por 100 VN).
        # MEP efectivo de MERCADO: ARS_operados / USD_obtenidos. Antes usábamos
        # `monto_ars` bruto en el numerador — pero ese monto incluye la comisión
        # que se descuenta antes de calcular nominales, así que el "slippage"
        # daba inflado por la comisión, no por el book. Ahora usamos los ARS
        # realmente movidos por la BUY (cum_buy * avg_buy * 0.01) → el slippage
        # vs MEP_ini refleja solo movimiento de precios.
        usd_efectivo: float | None = None
        mep_efectivo: float | None = None
        if sell_ord and buy_ord:
            cum_sell = float(sell_ord.get("cum_qty") or 0)
            avg_sell = float(sell_ord.get("avg_px") or 0)
            cum_buy = float(buy_ord.get("cum_qty") or 0)
            avg_buy = float(buy_ord.get("avg_px") or 0)
            if cum_sell > 0 and avg_sell > 0:
                usd_efectivo = round(cum_sell * avg_sell * PRICE_FACTOR_BONOS, 2)
            if cum_buy > 0 and avg_buy > 0 and usd_efectivo and usd_efectivo > 0:
                ars_operados = cum_buy * avg_buy * PRICE_FACTOR_BONOS
                mep_efectivo = round(ars_operados / usd_efectivo, 2)

        # Status compuesto basado en las 2 patas
        st_buy = (buy_ord or {}).get("status")
        st_sell = (sell_ord or {}).get("status")
        if st_buy == "FILLED" and st_sell == "FILLED":
            estado = "FILLED"
        elif st_buy in ESTADOS_FINALES_ORDEN and st_sell in ESTADOS_FINALES_ORDEN:
            estado = "TERMINADA"
        else:
            estado = op.get("status", "PENDING")

        ts_created = op.get("created_at")
        out.append({
            "operativa_id":      op.get("operativa_id"),
            "tipo":              op.get("tipo", "compra"),  # legacy docs sin tipo = compras
            "parent_trigger_id": op.get("parent_trigger_id"),
            "created_at":        ts_created.isoformat() if isinstance(ts_created, datetime) else ts_created,
            "rueda":             op.get("rueda"),
            "account":           op.get("account"),
            "actor_email":       op.get("actor_email"),
            "monto_ars":         op.get("monto_ars"),
            "comision_pct":      op.get("comision_pct"),
            "nominales":         op.get("nominales"),
            "mep_inicial":       op.get("mep_inicial"),
            "buy":  _enrich_pata(buy_ord),
            "sell": _enrich_pata(sell_ord),
            "usd_efectivo":  usd_efectivo,
            "mep_efectivo":  mep_efectivo,
            "estado":        estado,
            "wrapper_status": op.get("status"),
        })
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Detalle de una operativa (drilldown del frontend)
# ─────────────────────────────────────────────────────────────────────────────


def _serializar_doc(doc: dict | None) -> dict | None:
    """Convierte ts a ISO + filtra _id para enviar al frontend."""
    if not doc:
        return None
    out = {}
    for k, v in doc.items():
        if k == "_id":
            continue
        if isinstance(v, datetime):
            out[k] = v.isoformat()
        else:
            out[k] = v
    return out


def obtener_detalle_operativa(operativa_id: str) -> dict[str, Any] | None:
    """Devuelve el detalle completo de una operativa MEP para el drilldown.

    Incluye:
      - El doc completo de OperativasMep.
      - Por cada pata (buy/sell): el doc de OrdenesLive + timeline de
        OrdenesAudit (ER del motor + REST_SNAPSHOTs del flujo lineal).
      - Métricas derivadas (slippage, duración).

    Devuelve None si la operativa no existe.
    """
    db = get_mongo_client_read()[DB_OPS]
    op = db[COL_OPERATIVAS].find_one({"operativa_id": operativa_id})
    if not op:
        return None

    buy_cid = (op.get("buy") or {}).get("cl_ord_id")
    sell_cid = (op.get("sell") or {}).get("cl_ord_id")

    def _pata(cid: str | None) -> dict[str, Any]:
        if not cid:
            return {"live": None, "audit": []}
        live = db[COL_ORDENES].find_one({"cl_ord_id": cid})
        audit_cursor = (
            db["OrdenesAudit"]
            .find({"cl_ord_id": cid}, {"_id": 0})
            .sort("ts", 1)
        )
        audit = []
        for a in audit_cursor:
            ts = a.get("ts")
            audit.append({
                "ts":      ts.isoformat() if isinstance(ts, datetime) else ts,
                "kind":    a.get("kind"),
                "payload": a.get("payload"),
            })
        return {"live": _serializar_doc(live), "audit": audit}

    buy = _pata(buy_cid)
    sell = _pata(sell_cid)

    # Métricas derivadas — MEP efectivo de MERCADO = ARS_operados / USD obtenidos.
    # Ver listar_operativas_dia para el racional completo (no usar monto_ars
    # bruto porque la comisión infla el slippage).
    metricas: dict[str, Any] = {}
    sell_live = sell.get("live") or {}
    buy_live = buy.get("live") or {}
    cum_sell = float(sell_live.get("cum_qty") or 0)
    avg_sell = float(sell_live.get("avg_px") or 0)
    cum_buy = float(buy_live.get("cum_qty") or 0)
    avg_buy = float(buy_live.get("avg_px") or 0)

    if cum_sell > 0 and avg_sell > 0:
        usd_efectivo = round(cum_sell * avg_sell * PRICE_FACTOR_BONOS, 2)
        metricas["usd_efectivo"] = usd_efectivo
        if cum_buy > 0 and avg_buy > 0:
            ars_operados = round(cum_buy * avg_buy * PRICE_FACTOR_BONOS, 2)
            metricas["ars_operados"] = ars_operados
            metricas["precio_compra_al30"] = avg_buy
            metricas["precio_venta_al30d"] = avg_sell
            if usd_efectivo > 0:
                mep_ef = round(ars_operados / usd_efectivo, 2)
                metricas["mep_efectivo"] = mep_ef
                mep_ini = op.get("mep_inicial")
                if mep_ini:
                    metricas["slippage_pct"] = round((mep_ef / mep_ini - 1) * 100, 3)
                # Costo cliente = monto bruto / USD (incluye comisión).
                if op.get("monto_ars"):
                    metricas["mep_costo_cliente"] = round(op["monto_ars"] / usd_efectivo, 2)

    # Duración: del primer audit al último (across both patas)
    timestamps = []
    for pata in (buy, sell):
        for a in pata["audit"]:
            timestamps.append(a["ts"])
    if len(timestamps) >= 2:
        timestamps.sort()
        try:
            dt0 = datetime.fromisoformat(timestamps[0])
            dt1 = datetime.fromisoformat(timestamps[-1])
            metricas["duracion_ms"] = int((dt1 - dt0).total_seconds() * 1000)
        except (ValueError, TypeError):
            pass

    return {
        "operativa": _serializar_doc(op),
        "buy": buy,
        "sell": sell,
        "metricas": metricas,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Serie MEP por minuto (chart TRADING)
# ─────────────────────────────────────────────────────────────────────────────


def serie_mep_minuto(
    rueda: str = "CI",
    desde: datetime | None = None,
) -> list[dict[str, Any]]:
    """Serie del MEP por minuto. Para cada minuto donde hay trade de AL30 Y
    AL30D, calcula MEP = last_price_AL30 / last_price_AL30D usando
    aggregation pipelines de Mongo ($dateTrunc + $last) — la base hace el
    heavy lifting, el proceso solo hace el merge.

    Default: desde el inicio del día (ART) de hoy. La mesa pidió no
    arrastrar datos del día anterior — el chart de TRADING tiene que
    mostrar solo la rueda actual. Devuelve [{ts, mep}] ordenado
    ascendente, con `ts` en ISO UTC real (con tz explícito) — el
    frontend lo localiza a ART.
    """
    if rueda not in RUEDAS_VALIDAS:
        raise ValueError(f"rueda inválida: {rueda!r}")
    # `desde` se compara contra ts naive ART (rotulado UTC en Mongo).
    # Para "hoy" de la mesa = inicio del día ART de hoy en ese espacio.
    if desde is None:
        now_naive_art = datetime.now(UTC).replace(tzinfo=None) - MOTOR_TS_OFFSET
        desde = now_naive_art.replace(hour=0, minute=0, second=0, microsecond=0)

    tk = TICKERS_POR_RUEDA[rueda]
    db = get_mongo_client_read()[DB_TRADING]

    def _serie_ticker(ticker: str) -> dict[datetime, float]:
        cursor = db[COL_TIMESALES].aggregate([
            {"$match": {"ticker": ticker, "timestamp": {"$gte": desde}}},
            {"$group": {
                "_id": {"$dateTrunc": {"date": "$timestamp", "unit": "minute"}},
                "last_price": {"$last": "$price"},
            }},
            {"$sort": {"_id": 1}},
        ])
        out: dict[datetime, float] = {}
        for row in cursor:
            px = row.get("last_price")
            if px is None:
                continue
            try:
                out[row["_id"]] = float(px)
            except (TypeError, ValueError):
                continue
        return out

    al30 = _serie_ticker(tk["al30"])
    al30d = _serie_ticker(tk["al30d"])

    minutos = sorted(set(al30) & set(al30d))
    out: list[dict[str, Any]] = []
    for m in minutos:
        if al30d[m] <= 0:
            continue
        # m es naive ART rotulado UTC → sumamos offset y forzamos tz UTC real.
        ts_utc = (m.replace(tzinfo=None) + MOTOR_TS_OFFSET).replace(tzinfo=UTC)
        out.append({"ts": ts_utc.isoformat(), "mep": round(al30[m] / al30d[m], 2)})
    return out
