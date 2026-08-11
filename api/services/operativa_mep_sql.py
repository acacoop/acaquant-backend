"""api/services/operativa_mep_sql.py — READ-SIDE de la operativa Dólar MEP (única implementación).

Servicio PURO (sin FastAPI). Acá viven las LECTURAS de la operativa MEP:
`listar_operativas_dia` (listado del día con join a órdenes + métricas) y
`obtener_detalle_operativa` (drilldown con timeline de audit).

⚠️ Esto NO toca el write-side ni el envío al broker. La CREACIÓN de operativas
(`crear_operativa`/`operativa_venta_mep`/…), el polling REST contra pyRofex y la
persistencia viven en `operativa_mep.py`, que también aporta los helpers PUROS
reusados acá (`_enrich_pata`, `_serializar_doc`, constantes) para no duplicar la
lógica de cálculo (usd/mep efectivo, slippage, duración) — que es lo único delicado.

Contrato de las tablas (ver sql/schema.sql):
  operaciones.operativas_mep: id (PK=operativa_id) · account · rueda · ts · data jsonb
  operaciones.ordenes_live:   cl_ord_id (PK) · account · ticker · estado · updated_at · data
  operaciones.ordenes_audit:  id · ts · kind · cl_ord_id · account · actor_email · data
`data` = doc completo con datetimes→ISO (doc_iso).
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from api.services._sql import _q

logger = logging.getLogger("api.services.operativa_mep_sql")


def _orden_doc(cl_ord_id: str | None) -> dict | None:
    """Doc LOCAL de una orden (shape OrdenesLive) desde SQL `operaciones.ordenes_live`.
    El `data` jsonb trae el doc completo (status/cum_qty/avg_px/leaves_qty/ticker/…),
    que es exactamente lo que consumen `_enrich_pata` y el cálculo de usd/mep."""
    if not cl_ord_id:
        return None
    rows = _q("SELECT data FROM operaciones.ordenes_live WHERE cl_ord_id = %s", (cl_ord_id,))
    if not rows:
        return None
    return dict(rows[0].get("data") or {})


# ─────────────────────────────────────────────────────────────────────────────
# Listado del día con enrich (espejo de operativa_mep.listar_operativas_dia)
# ─────────────────────────────────────────────────────────────────────────────


def listar_operativas_dia(account: str | None = None) -> list[dict]:
    """Operativas MEP del día UTC con join a órdenes + métricas calculadas.

    Filtra por `data->>'created_at'` (ISO estable en el jsonb), NO por la columna
    `ts` (ambigua entre el baseline sync = created_at y el write live =
    updated_at). Mismo criterio que `ordenes_sql._orders_locales_dia`."""
    from api.services.operativa_mep import (
        ESTADOS_FINALES_ORDEN,
        _enrich_pata,
        calcular_efectivos,
    )

    inicio = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    inicio_iso = inicio.isoformat()

    where = "WHERE (data->>'created_at') >= %s"
    params: list[Any] = [inicio_iso]
    if account:
        where += " AND account = %s"
        params.append(account)

    rows = _q(
        f"SELECT data FROM operaciones.operativas_mep {where} "
        f"ORDER BY (data->>'created_at') DESC",
        tuple(params),
    )
    operativas = [dict(r.get("data") or {}) for r in rows]
    if not operativas:
        return []

    # Traer las OrdenesLive de las patas de un saque.
    cl_ord_ids: list[str] = []
    for op in operativas:
        for pata in ("buy", "sell"):
            cid = (op.get(pata) or {}).get("cl_ord_id")
            if cid:
                cl_ord_ids.append(cid)
    ordenes_by_id: dict[str, dict] = {}
    if cl_ord_ids:
        live_rows = _q(
            "SELECT cl_ord_id, data FROM operaciones.ordenes_live "
            "WHERE cl_ord_id = ANY(%s)",
            (cl_ord_ids,),
        )
        for r in live_rows:
            d = dict(r.get("data") or {})
            ordenes_by_id[r["cl_ord_id"]] = d

    out: list[dict] = []
    for op in operativas:
        buy_cid = (op.get("buy") or {}).get("cl_ord_id")
        sell_cid = (op.get("sell") or {}).get("cl_ord_id")
        buy_ord = ordenes_by_id.get(buy_cid) if buy_cid else None
        sell_ord = ordenes_by_id.get(sell_cid) if sell_cid else None

        # USD/ARS efectivamente movidos y MEP efectivo de MERCADO
        # (= ARS_operados / USD_obtenidos). Se usan los ARS realmente movidos
        # por la pata en pesos, NO `monto_ars` bruto, que incluye la comisión e
        # inflaría el slippage vs MEP_ini. Qué pata está en dólares lo decide el
        # `tipo` (compra/venta) — ver `calcular_efectivos`.
        efectivos = calcular_efectivos(op.get("tipo"), buy_ord, sell_ord)
        usd_efectivo = efectivos["usd_efectivo"]
        mep_efectivo = efectivos["mep_efectivo"]

        # Status compuesto basado en las 2 patas.
        st_buy = (buy_ord or {}).get("status")
        st_sell = (sell_ord or {}).get("status")
        if st_buy == "FILLED" and st_sell == "FILLED":
            estado = "FILLED"
        elif st_buy in ESTADOS_FINALES_ORDEN and st_sell in ESTADOS_FINALES_ORDEN:
            estado = "TERMINADA"
        else:
            estado = op.get("status", "PENDING")

        # created_at en el jsonb ya es ISO (doc_iso) → se pasa tal cual.
        ts_created = op.get("created_at")
        out.append({
            "operativa_id":      op.get("operativa_id"),
            "tipo":              op.get("tipo", "compra"),
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
# Detalle de una operativa (drilldown) — espejo de obtener_detalle_operativa
# ─────────────────────────────────────────────────────────────────────────────


def obtener_detalle_operativa(operativa_id: str) -> dict[str, Any] | None:
    """Detalle completo de una operativa MEP para el drilldown: doc completo +
    2 patas con sus órdenes live y timeline de audit + métricas derivadas
    (slippage, duración). Devuelve None si la operativa no existe."""
    from api.services.operativa_mep import _serializar_doc, calcular_efectivos

    rows = _q("SELECT data FROM operaciones.operativas_mep WHERE id = %s", (operativa_id,))
    if not rows:
        return None
    op = dict(rows[0].get("data") or {})

    buy_cid = (op.get("buy") or {}).get("cl_ord_id")
    sell_cid = (op.get("sell") or {}).get("cl_ord_id")

    def _pata(cid: str | None) -> dict[str, Any]:
        if not cid:
            return {"live": None, "audit": []}
        live = _orden_doc(cid)
        audit_rows = _q(
            "SELECT ts, kind, data FROM operaciones.ordenes_audit "
            "WHERE cl_ord_id = %s ORDER BY ts ASC",
            (cid,),
        )
        audit = []
        for a in audit_rows:
            ts = a.get("ts")
            # `payload` vive en data->'payload' (mismo shape que escriben el motor,
            # ordenes._audit y operativa_mep._persistir_orden_live).
            data = a.get("data") or {}
            audit.append({
                "ts":      ts.isoformat() if isinstance(ts, datetime) else ts,
                "kind":    a.get("kind"),
                "payload": data.get("payload"),
            })
        return {"live": _serializar_doc(live), "audit": audit}

    buy = _pata(buy_cid)
    sell = _pata(sell_cid)

    # Métricas derivadas (ver racional del slippage en listar_operativas_dia).
    # MISMA función que el listado → el drilldown no puede contradecir a la tabla.
    metricas: dict[str, Any] = {}
    efectivos = calcular_efectivos(op.get("tipo"), buy.get("live"), sell.get("live"))
    usd_efectivo = efectivos["usd_efectivo"]
    if usd_efectivo:
        metricas["usd_efectivo"] = usd_efectivo
        # Los precios se rotulan por INSTRUMENTO (AL30 en ARS, AL30D en USD),
        # no por side: en la venta el AL30 se vende y el AL30D se compra.
        if efectivos["precio_al30"] is not None:
            metricas["precio_compra_al30"] = efectivos["precio_al30"]
        if efectivos["precio_al30d"] is not None:
            metricas["precio_venta_al30d"] = efectivos["precio_al30d"]
        if efectivos["ars_operados"] is not None:
            metricas["ars_operados"] = efectivos["ars_operados"]
        mep_ef = efectivos["mep_efectivo"]
        if mep_ef:
            metricas["mep_efectivo"] = mep_ef
            mep_ini = op.get("mep_inicial")
            if mep_ini:
                metricas["slippage_pct"] = round((mep_ef / mep_ini - 1) * 100, 3)
            if op.get("monto_ars"):
                metricas["mep_costo_cliente"] = round(op["monto_ars"] / usd_efectivo, 2)

    # Duración: del primer audit al último (across both patas).
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
