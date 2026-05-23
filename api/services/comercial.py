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
