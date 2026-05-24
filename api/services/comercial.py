"""api/services/comercial.py — Tablero Comercial (lente por operador).

Cruza, todo por `id_cuenta`:
  - QUIÉN   → `Clientes.Comitentes` (operador asignado, segmentación nivel_1).
  - ACTIVIDAD → `CashFlow.NegocioMovimientos` (última operación).
  - TAMAÑO  → `Valuaciones.AuM` (último snapshot).
  - operador ↔ usuario → `Manager.Users` (para detectar cuentas huérfanas).

Devuelve el resumen por operador para la vista COMERCIAL (v1, solo manager).
On-the-fly cacheado (TTL); si pesa, mover a precompute `Clientes.ComercialCache`.
Diseño completo: docs/TABLERO_COMERCIAL.md [5].
"""
from __future__ import annotations

import re
from datetime import UTC, date, datetime, timedelta
from typing import Any

from api.cache import cached
from api.db import (
    get_db_cashflow,
    get_db_clientes,
    get_db_manager,
    get_db_valuaciones,
)

# `cuenta` en NegocioMovimientos viene "[805] NOMBRE" → extraer el id.
_RE_ID_BRACKET = re.compile(r"^\[(\d+)\]")

_BUCKET = {
    "ACTIVA": "n_activas",
    "ENFRIANDOSE": "n_enfriandose",
    "DORMIDA": "n_dormidas",
    "NUEVA": "n_nuevas",
}


def estado_comercial(
    dias_desde_ult_op: int | None,
    opero_alguna_vez: bool,
    dias_activa: int,
    dias_dormida: int,
) -> str:
    """Estado COMERCIAL (≠ legal), derivado de la última operación.

    `dias_desde_ult_op`: días desde la última op SI está dentro de la ventana
    reciente (`dias_dormida`), si no → None. `opero_alguna_vez`: si la cuenta
    aparece alguna vez en NegocioMovimientos.

    NUEVA (nunca operó) · ACTIVA (≤ dias_activa) · ENFRIANDOSE (dias_activa..
    dias_dormida) · DORMIDA (operó alguna vez pero hace > dias_dormida).
    """
    if dias_desde_ult_op is not None:
        return "ACTIVA" if dias_desde_ult_op <= dias_activa else "ENFRIANDOSE"
    return "DORMIDA" if opero_alguna_vez else "NUEVA"


def _nuevo_operador(email: str | None, nombre: str | None) -> dict[str, Any]:
    return {
        "operador_email": email,
        "operador_nombre": nombre,
        "n_cuentas": 0,
        "n_activas": 0,
        "n_enfriandose": 0,
        "n_dormidas": 0,
        "n_nuevas": 0,
        "n_sin_segmentar": 0,
        "aum_total": 0.0,
        "huerfana": False,
    }


# ── Vista COMERCIAL en OPERACIONES (lente por operador, estilo NEGOCIO) ──────
# "Volumen operado" = mismo criterio que NEGOCIO: sum(abs(importe)) sobre estas
# categorías de boleto. AuM = Valuaciones.AuM (ARS). Todo por cuenta del operador.

_CATS_VOLUMEN = (
    "compra", "venta",
    "suscripcion_fci", "solicitud_suscripcion_fci",
    "caucion_tom_ap", "caucion_col_ap",
)


def _hoy_art() -> date:
    """Fecha de hoy en horario Argentina (UTC-3) para MTD/YTD calendario."""
    return (datetime.now(UTC) - timedelta(hours=3)).date()


def _cuentas_de_operador(operador_email: str) -> tuple[str, ...]:
    """ids de cuenta (Comitentes activas) asignadas a un operador."""
    docs = get_db_clientes()["Comitentes"].find(
        {"operador_email": operador_email, "estado": "Activa"},
        {"_id": 0, "id_cuenta": 1},
    )
    return tuple(sorted(str(d["id_cuenta"]) for d in docs if d.get("id_cuenta")))


def _aum_por_cuenta(ids: tuple[str, ...]) -> dict[str, float]:
    """AuM (último snapshot) por id_cuenta, restringido a `ids`."""
    if not ids:
        return {}
    col = get_db_valuaciones()["AuM"]
    snap = col.find_one({}, {"_id": 0, "fecha_snapshot": 1}, sort=[("fecha_snapshot", -1)])
    if not snap:
        return {}
    out: dict[str, float] = {}
    for d in col.aggregate([
        {"$match": {"fecha_snapshot": snap["fecha_snapshot"], "id_cuenta": {"$in": list(ids)}}},
        {"$group": {"_id": "$id_cuenta", "aum": {"$sum": "$valuacion"}}},
    ]):
        out[str(d["_id"])] = float(d.get("aum") or 0.0)
    return out


def _match_volumen(ids: tuple[str, ...], moneda: str, fecha_desde: str | None) -> dict:
    """$match de NegocioMovimientos por cuentas del operador + categorías + moneda."""
    from api.services._grupos_scope import scope_cuenta_match
    m: dict[str, Any] = {"moneda": moneda, "categoria": {"$in": list(_CATS_VOLUMEN)}}
    sub = scope_cuenta_match(ids)  # {"cuenta": {"$regex": "^\\[(id|...)\\]"}} (o $in:[] si vacío)
    if sub:
        m.update(sub)
    if fecha_desde:
        m["fecha"] = {"$gte": fecha_desde}
    return m


def _volumen_total(ids: tuple[str, ...], moneda: str, fecha_desde: str | None) -> float:
    if not ids:
        return 0.0
    coll = get_db_cashflow()["NegocioMovimientos"]
    res = list(coll.aggregate([
        {"$match": _match_volumen(ids, moneda, fecha_desde)},
        {"$group": {"_id": None, "v": {"$sum": {"$abs": "$importe"}}}},
    ]))
    return round(float(res[0]["v"]), 2) if res else 0.0


@cached(ttl=300)
def listar_operadores_comercial() -> list[dict[str, Any]]:
    """Operadores para el selector: email, nombre, # cuentas activas."""
    pipeline = [
        {"$match": {"estado": "Activa", "operador_email": {"$ne": None}}},
        {"$group": {
            "_id": "$operador_email",
            "nombre": {"$first": "$operador_nombre"},
            "n_cuentas": {"$sum": 1},
        }},
        {"$sort": {"n_cuentas": -1}},
    ]
    return [
        {"operador_email": d["_id"], "operador_nombre": d.get("nombre"), "n_cuentas": d["n_cuentas"]}
        for d in get_db_clientes()["Comitentes"].aggregate(pipeline)
    ]


@cached(ttl=300)
def resumen_comercial(*, operador: str, moneda: str = "ARS") -> dict[str, Any]:
    """KPIs del operador: AuM gestionado, # clientes, Volumen MTD, Volumen YTD."""
    ids = _cuentas_de_operador(operador)
    hoy = _hoy_art()
    aum = _aum_por_cuenta(ids)
    return {
        "operador": operador,
        "moneda": moneda,
        "aum_gestionado": round(sum(aum.values()), 2),
        "n_clientes": len(ids),
        "volumen_mtd": _volumen_total(ids, moneda, hoy.replace(day=1).isoformat()),
        "volumen_ytd": _volumen_total(ids, moneda, hoy.replace(month=1, day=1).isoformat()),
    }


@cached(ttl=300)
def clientes_comercial(*, operador: str, moneda: str = "ARS") -> list[dict[str, Any]]:
    """Tabla de clientes del operador: cuenta+nombre, AuM, Volumen YTD."""
    ids = _cuentas_de_operador(operador)
    if not ids:
        return []
    aum = _aum_por_cuenta(ids)
    nombres = {
        str(d["id_cuenta"]): d.get("denominacion")
        for d in get_db_clientes()["Comitentes"].find(
            {"operador_email": operador, "estado": "Activa"},
            {"_id": 0, "id_cuenta": 1, "denominacion": 1},
        )
    }
    # Volumen YTD por cuenta (un solo group, mapeo por id bracketed).
    ytd_desde = _hoy_art().replace(month=1, day=1).isoformat()
    vol: dict[str, float] = {}
    for d in get_db_cashflow()["NegocioMovimientos"].aggregate([
        {"$match": _match_volumen(ids, moneda, ytd_desde)},
        {"$group": {"_id": "$cuenta", "v": {"$sum": {"$abs": "$importe"}}}},
    ]):
        m = _RE_ID_BRACKET.match(d.get("_id") or "")
        if m:
            vol[m.group(1)] = round(float(d.get("v") or 0.0), 2)
    filas = [
        {
            "id_cuenta": idc,
            "denominacion": nombres.get(idc) or "—",
            "aum": round(aum.get(idc, 0.0), 2),
            "volumen_ytd": vol.get(idc, 0.0),
        }
        for idc in ids
    ]
    filas.sort(key=lambda x: x["aum"], reverse=True)
    return filas


@cached(ttl=300)
def serie_comercial(*, operador: str, metric: str = "volumen", moneda: str = "ARS") -> dict[str, Any]:
    """Serie temporal del operador para el gráfico de líneas.

    metric='volumen' → sum(abs(importe)) diario (NegocioMovimientos).
    metric='aum'     → AuM por fecha_snapshot (Valuaciones.AuM, ARS)."""
    ids = _cuentas_de_operador(operador)
    if not ids:
        return {"operador": operador, "metric": metric, "serie": []}

    if metric == "aum":
        serie = [
            {"fecha": d["_id"], "valor": round(float(d.get("v") or 0.0), 2)}
            for d in get_db_valuaciones()["AuM"].aggregate([
                {"$match": {"id_cuenta": {"$in": list(ids)}}},
                {"$group": {"_id": "$fecha_snapshot", "v": {"$sum": "$valuacion"}}},
                {"$sort": {"_id": 1}},
            ])
        ]
    else:
        serie = [
            {"fecha": d["_id"], "valor": round(float(d.get("v") or 0.0), 2)}
            for d in get_db_cashflow()["NegocioMovimientos"].aggregate([
                {"$match": _match_volumen(ids, moneda, None)},
                {"$group": {"_id": "$fecha", "v": {"$sum": {"$abs": "$importe"}}}},
                {"$sort": {"_id": 1}},
            ])
        ]
    return {"operador": operador, "metric": metric, "moneda": moneda, "serie": serie}


@cached(ttl=300)
def resumen_por_operador(*, dias_activa: int = 30, dias_dormida: int = 90) -> dict[str, Any]:
    """Resumen comercial agrupado por operador. Ver módulo."""
    hoy = datetime.now(UTC).date()

    # 1) Master: cuentas comitentes activas (legal) con operador + segmento.
    cuentas = list(
        get_db_clientes()["Comitentes"].find(
            {"estado": "Activa"},
            {"_id": 0, "id_cuenta": 1, "operador_email": 1,
             "operador_nombre": 1, "nivel_1": 1},
        )
    )

    # 2) AuM por id_cuenta (último snapshot).
    aum_col = get_db_valuaciones()["AuM"]
    ult_snap = aum_col.find_one({}, {"_id": 0, "fecha_snapshot": 1}, sort=[("fecha_snapshot", -1)])
    aum_por_cuenta: dict[str, float] = {}
    if ult_snap:
        for d in aum_col.aggregate([
            {"$match": {"fecha_snapshot": ult_snap["fecha_snapshot"]}},
            {"$group": {"_id": "$id_cuenta", "aum": {"$sum": "$valuacion"}}},
        ]):
            aum_por_cuenta[str(d["_id"])] = float(d.get("aum") or 0.0)

    # 3) Actividad: última op por cuenta dentro de la ventana + set "operó alguna vez".
    mov = get_db_cashflow()["NegocioMovimientos"]
    desde = (hoy - timedelta(days=dias_dormida)).isoformat()
    dias_ult_op: dict[str, int] = {}
    for d in mov.aggregate([
        {"$match": {"fecha": {"$gte": desde}}},
        {"$group": {"_id": "$cuenta", "ult": {"$max": "$fecha"}}},
    ]):
        m = _RE_ID_BRACKET.match(d.get("_id") or "")
        if not m or not d.get("ult"):
            continue
        try:
            dias_ult_op[m.group(1)] = (hoy - date.fromisoformat(d["ult"][:10])).days
        except ValueError:
            continue
    opero_alguna_vez: set[str] = set()
    for c in mov.distinct("cuenta"):
        m = _RE_ID_BRACKET.match(c or "")
        if m:
            opero_alguna_vez.add(m.group(1))

    # 4) Emails de usuarios reales (para flag de cuentas huérfanas).
    emails_users = {
        str(e).lower().strip()
        for e in get_db_manager()["Users"].distinct("email")
        if e
    }

    # 5) Agregar por operador.
    ops: dict[str, dict] = {}
    for c in cuentas:
        idc = str(c.get("id_cuenta"))
        email = (c.get("operador_email") or "").strip()
        key = email.lower() or "(sin operador)"
        o = ops.get(key)
        if o is None:
            o = ops[key] = _nuevo_operador(email or None, c.get("operador_nombre"))
        o["n_cuentas"] += 1
        o["aum_total"] += aum_por_cuenta.get(idc, 0.0)
        if not c.get("nivel_1"):
            o["n_sin_segmentar"] += 1
        est = estado_comercial(
            dias_ult_op.get(idc), idc in opero_alguna_vez, dias_activa, dias_dormida,
        )
        o[_BUCKET[est]] += 1

    for o in ops.values():
        em = (o["operador_email"] or "").lower()
        o["huerfana"] = bool(em) and em not in emails_users

    operadores = sorted(ops.values(), key=lambda x: x["aum_total"], reverse=True)
    return {
        "operadores": operadores,
        "dias_activa": dias_activa,
        "dias_dormida": dias_dormida,
        "snapshot_aum": (ult_snap or {}).get("fecha_snapshot"),
        "total_cuentas": sum(o["n_cuentas"] for o in operadores),
        "total_aum": sum(o["aum_total"] for o in operadores),
    }
