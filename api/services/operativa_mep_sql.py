"""api/services/operativa_mep_sql.py — READ-SIDE de la operativa Dólar MEP leyendo Postgres.

Servicio PURO (sin FastAPI). Espejo SQL de las LECTURAS de
`api/services/operativa_mep.py` que hoy leen Mongo (`Operaciones.{OperativasMep,
OrdenesLive, OrdenesAudit}`): `listar_operativas_dia` + `obtener_detalle_operativa`.

Dual-run: el selector del router elige SQL o Mongo según el flag `ORDENES_SQL`
(el MISMO que gobierna `ordenes_sql.py` — el read-side de órdenes es un solo
dominio). El path Mongo queda INTACTO → rollback = sacar el flag.

⚠️ Esto NO toca el write-side ni el envío al broker. La CREACIÓN de operativas
(`crear_operativa`/`operativa_venta_mep`/…), el polling REST contra pyRofex y la
persistencia (Mongo + dual-write best-effort a SQL bajo `ORDENES_SQL_WRITE`)
siguen IGUAL en `operativa_mep.py`. Acá SOLO cambia de DÓNDE salen los docs para
el listado del día y el drilldown.

Contrato de las tablas (ver sql/schema.sql):
  operaciones.operativas_mep: id (PK=operativa_id) · account · rueda · ts · data jsonb
  operaciones.ordenes_live:   cl_ord_id (PK) · account · ticker · estado · updated_at · data
  operaciones.ordenes_audit:  id · ts · kind · cl_ord_id · account · actor_email · data
`data` = doc Mongo completo con datetimes→ISO (doc_iso). El read reconstruye el
MISMO shape que devolvía Mongo, reusando los helpers PUROS del módulo Mongo
(`_enrich_pata`, `_serializar_doc`, constantes) para no duplicar la lógica de
cálculo (usd/mep efectivo, slippage, duración) — que es lo único delicado.
"""
from __future__ import annotations

import logging
import os
from datetime import UTC, datetime
from typing import Any

from api.services._sql import _q

logger = logging.getLogger("api.services.operativa_mep_sql")


def operativas_sql_on() -> bool:
    """Lectura del read-side de la operativa MEP desde SQL (default Mongo).
    Comparte flag con el read-side de órdenes (mismo dominio TRANSACCIONAL)."""
    return os.getenv("ORDENES_SQL") == "1"


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
    """Operativas MEP del día UTC con join a OrdenesLive + métricas — MISMO shape
    que `operativa_mep.listar_operativas_dia`, pero los docs salen de SQL.

    Filtra por `data->>'created_at'` (ISO estable en el jsonb), NO por la columna
    `ts` (ambigua entre el baseline sync = created_at y el dual-write live =
    updated_at). Es el mismo criterio que usa `ordenes_sql._orders_locales_dia`
    y replica el filtro Mongo `created_at >= inicio`."""
    from api.services.operativa_mep import (
        ESTADOS_FINALES_ORDEN,
        PRICE_FACTOR_BONOS,
        _enrich_pata,
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

        # USD/MEP efectivo de MERCADO — lógica IDÉNTICA al path Mongo (ver
        # operativa_mep.listar_operativas_dia para el racional del slippage).
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

        # Status compuesto basado en las 2 patas.
        st_buy = (buy_ord or {}).get("status")
        st_sell = (sell_ord or {}).get("status")
        if st_buy == "FILLED" and st_sell == "FILLED":
            estado = "FILLED"
        elif st_buy in ESTADOS_FINALES_ORDEN and st_sell in ESTADOS_FINALES_ORDEN:
            estado = "TERMINADA"
        else:
            estado = op.get("status", "PENDING")

        # created_at en el jsonb ya es ISO (doc_iso) → se pasa tal cual, idéntico
        # al .isoformat() del datetime que devuelve el path Mongo.
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
    """Detalle completo de una operativa MEP — MISMO shape que el path Mongo,
    pero el doc + las patas (OrdenesLive) + el timeline (OrdenesAudit) salen de SQL.

    Devuelve None si la operativa no existe."""
    from api.services.operativa_mep import PRICE_FACTOR_BONOS, _serializar_doc

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

    # Métricas derivadas — código IDÉNTICO al path Mongo.
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
