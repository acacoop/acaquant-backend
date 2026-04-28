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

# Timeout de espera del ER de la BUY. AL30 MARKET en mercado abierto se
# resuelve en milisegundos; 3s es generoso y bloquea la SELL si algo raro pasa.
BUY_WAIT_TIMEOUT_S = 3.0
BUY_WAIT_INTERVAL_S = 0.1


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


def _wait_buy_resolved(
    cl_ord_id: str,
    timeout_s: float = BUY_WAIT_TIMEOUT_S,
    interval_s: float = BUY_WAIT_INTERVAL_S,
) -> tuple[str | None, dict | None]:
    """Espera al motor_ordenes a que actualice OrdenesLive con un status ≠ PENDING_NEW.

    Devuelve (status, doc). status=None si timeout y nunca apareció el doc.
    El status final puede ser cualquier estado del broker (NEW/REJECTED/...);
    el caller decide qué hacer.
    """
    db = get_mongo_client()[DB_OPS]
    deadline = time.monotonic() + timeout_s
    last_doc: dict | None = None
    while time.monotonic() < deadline:
        last_doc = db[COL_ORDENES].find_one({"cl_ord_id": cl_ord_id})
        if last_doc:
            st = last_doc.get("status")
            if st and st != "PENDING_NEW":
                return st, last_doc
        time.sleep(interval_s)
    return ((last_doc or {}).get("status"), last_doc)


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

    buy_status, buy_doc = _wait_buy_resolved(buy_cl_ord_id)
    buy_reason = (buy_doc or {}).get("reject_reason")

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
        motivo = f"BUY no resuelta en {BUY_WAIT_TIMEOUT_S}s (status={buy_status})"
        logger.warning(motivo)
        return {
            "stage": "buy_er", "global_status": "STALE_BUY",
            "buy": {"cl_ord_id": buy_cl_ord_id, "status": buy_status, "ok": False},
            "sell": None, "error": motivo,
        }

    sell_resp = send_order(
        ticker=sell_ticker, side="SELL", size=nominales,
        order_type="MARKET", price=None, tif="DAY",
        account=account, actor_email=actor_email,
    )
    sell_cl_ord_id = sell_resp.get("cl_ord_id")
    sell_ok = bool(sell_resp.get("ok"))

    return {
        "stage": "all_ok" if sell_ok else "sell",
        "global_status": "OK" if sell_ok else "OK_PARCIAL",
        "buy": {"cl_ord_id": buy_cl_ord_id, "status": buy_status, "ok": True},
        "sell": {"cl_ord_id": sell_cl_ord_id, "status": "PENDING_NEW",
                 "ok": sell_ok,
                 "error": None if sell_ok else sell_resp.get("error")},
        "error": None if sell_ok else sell_resp.get("error"),
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

        # USD efectivo: cumQty * avgPx de la SELL (lo que se pagó en USD por el AL30D
        # vendido). MEP efectivo = monto_ars original / USD efectivo.
        usd_efectivo: float | None = None
        mep_efectivo: float | None = None
        if sell_ord:
            cum = float(sell_ord.get("cum_qty") or 0)
            avg = float(sell_ord.get("avg_px") or 0)
            if cum > 0 and avg > 0:
                usd_efectivo = round(cum * avg, 2)
                if usd_efectivo > 0:
                    mep_efectivo = round(op.get("monto_ars", 0) / usd_efectivo, 2)

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

    Default: últimas 24h. Devuelve [{ts, mep}] ordenado ascendente, con `ts`
    en ISO UTC real (con tz explícito) — el frontend lo localiza a ART.
    """
    if rueda not in RUEDAS_VALIDAS:
        raise ValueError(f"rueda inválida: {rueda!r}")
    # `desde` se compara contra ts naive ART (rotulado UTC en Mongo). Para
    # incluir las últimas 24h reales hay que mirar 24h atrás en ese mismo
    # espacio: now_utc - offset - 24h.
    if desde is None:
        desde = datetime.now(UTC).replace(tzinfo=None) - MOTOR_TS_OFFSET - timedelta(hours=24)

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
