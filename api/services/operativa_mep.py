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
from datetime import UTC, datetime
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

    # ── BUY AL30 MARKET ──
    buy_resp = send_order(
        ticker=tk["al30"],
        side="BUY",
        size=nominales,
        order_type="MARKET",
        price=None,
        tif="DAY",
        account=account,
        actor_email=actor_email,
    )
    if not buy_resp.get("ok"):
        db_ops[COL_OPERATIVAS].update_one(
            {"operativa_id": operativa_id},
            {"$set": {
                "status": "FAIL",
                "buy_error": buy_resp.get("error"),
                "updated_at": datetime.now(UTC),
            }},
        )
        return {
            "ok": False,
            "operativa_id": operativa_id,
            "status": "FAIL",
            "stage": "buy",
            "error": buy_resp.get("error"),
        }
    buy_cl_ord_id = buy_resp["cl_ord_id"]

    # ── SELL AL30D MARKET ──
    sell_resp = send_order(
        ticker=tk["al30d"],
        side="SELL",
        size=nominales,
        order_type="MARKET",
        price=None,
        tif="DAY",
        account=account,
        actor_email=actor_email,
    )
    sell_cl_ord_id = sell_resp.get("cl_ord_id")
    sell_ok = bool(sell_resp.get("ok"))

    db_ops[COL_OPERATIVAS].update_one(
        {"operativa_id": operativa_id},
        {"$set": {
            "buy.cl_ord_id": buy_cl_ord_id,
            "sell.cl_ord_id": sell_cl_ord_id,
            "status": "OK" if sell_ok else "OK_PARCIAL",
            "sell_error": None if sell_ok else sell_resp.get("error"),
            "updated_at": datetime.now(UTC),
        }},
    )
    return {
        "ok": True,
        "operativa_id": operativa_id,
        "status": "OK" if sell_ok else "OK_PARCIAL",
        "buy": {"cl_ord_id": buy_cl_ord_id, "ok": True},
        "sell": {"cl_ord_id": sell_cl_ord_id, "ok": sell_ok,
                 "error": None if sell_ok else sell_resp.get("error")},
        "nominales": nominales,
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
            "operativa_id":  op.get("operativa_id"),
            "created_at":    ts_created.isoformat() if isinstance(ts_created, datetime) else ts_created,
            "rueda":         op.get("rueda"),
            "account":       op.get("account"),
            "actor_email":   op.get("actor_email"),
            "monto_ars":     op.get("monto_ars"),
            "comision_pct":  op.get("comision_pct"),
            "nominales":     op.get("nominales"),
            "mep_inicial":   op.get("mep_inicial"),
            "buy":  _enrich_pata(buy_ord),
            "sell": _enrich_pata(sell_ord),
            "usd_efectivo":  usd_efectivo,
            "mep_efectivo":  mep_efectivo,
            "estado":        estado,
            "wrapper_status": op.get("status"),
        })
    return out
