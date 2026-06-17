"""Service Tenencia Valorizada (cartera HD, cuentas propias 100/255/256).

Lee la colección materializada `Valuaciones.TenenciaHD` (1 doc/día, la escribe
`jobs/tenencia_hd.py`). Lógica pura — sin FastAPI. Dos lecturas:
  - `tenencia_dias`      → tabla izquierda: 1 fila por día, AuM HD de cada cuenta.
  - `tenencia_posiciones`→ tabla derecha: posiciones HD por título de un día.
"""
from __future__ import annotations

from typing import Any

from api.cache import cached, invalidate
from api.db import get_db_valuaciones

CUENTAS = ["100", "255", "256"]


@cached(ttl=300)
def tenencia_dias() -> dict[str, Any]:
    """Serie diaria: 1 fila por fecha con el AuM HD de cada cuenta + total.
    No trae las posiciones (van por `tenencia_posiciones`, on-demand por día)."""
    col = get_db_valuaciones()["TenenciaHD"]
    dias = []
    for d in col.find({}, {"_id": 0, "posiciones": 0}).sort("fecha_snapshot", 1):
        aum = d.get("aum", {})
        fila = {"fecha": d["fecha_snapshot"], "tc": d.get("tc"), "total": d.get("total", 0.0)}
        fila.update({c: aum.get(c, 0.0) for c in CUENTAS})
        dias.append(fila)
    return {
        "cuentas": CUENTAS,
        "dias": dias,
        "ultima_fecha": dias[-1]["fecha"] if dias else None,
    }


def tenencia_posiciones(*, fecha: str) -> dict[str, Any]:
    """Posiciones HD (por título, con desglose por cuenta) de un día."""
    col = get_db_valuaciones()["TenenciaHD"]
    d = col.find_one({"fecha_snapshot": fecha},
                     {"_id": 0, "fecha_snapshot": 1, "posiciones": 1, "total": 1, "tc": 1})
    return {
        "fecha": fecha,
        "cuentas": CUENTAS,
        "tc": (d or {}).get("tc"),
        "total": (d or {}).get("total", 0.0),
        "posiciones": (d or {}).get("posiciones", []),
    }


def actualizar_precio_posicion(*, fecha: str, unidad: str, precio: float) -> dict[str, Any]:
    """Corrige a mano el PRECIO de una unidad en la tenencia HD de un día y recalcula
    la valuación de las 3 cuentas (cartera HD = paridad → `cantidad × precio / 100`) +
    los totales del día. Edita el doc FROZEN `Valuaciones.TenenciaHD` (no la fuente AuM).

    Requiere que el doc tenga `cant` por posición (job nuevo + backfill). Si una
    posición vieja no lo tiene, devuelve error pidiendo re-correr el job."""
    col = get_db_valuaciones()["TenenciaHD"]
    doc = col.find_one({"fecha_snapshot": fecha})
    if not doc:
        return {"ok": False, "error": f"no hay tenencia HD para {fecha}"}
    posiciones = doc.get("posiciones", [])
    pos = next((p for p in posiciones if p.get("unidad") == unidad), None)
    if pos is None:
        return {"ok": False, "error": f"unidad {unidad!r} no está en {fecha}"}
    if "cant" not in pos:
        return {"ok": False, "error": "esta posición no tiene cantidades — re-corré "
                "jobs.tenencia_hd para ese día antes de editar el precio"}

    try:
        precio = float(precio)
    except (TypeError, ValueError):
        return {"ok": False, "error": f"precio inválido: {precio!r}"}

    # Recalcular valuación por cuenta = cantidad × precio / 100 (HD = paridad).
    cant = pos.get("cant", {})
    nuevas = {c: round(float(cant.get(c, 0.0)) * precio / 100.0, 2) for c in CUENTAS}
    pos["precio"] = round(precio, 4)
    pos.update(nuevas)
    pos["total"] = round(sum(nuevas.values()), 2)

    # Recalcular aum (por cuenta) + total del día sobre TODAS las posiciones.
    aum = {c: round(sum(float(p.get(c, 0.0)) for p in posiciones), 2) for c in CUENTAS}
    total = round(sum(aum.values()), 2)
    col.update_one({"fecha_snapshot": fecha},
                   {"$set": {"posiciones": posiciones, "aum": aum, "total": total}})
    invalidate("tenencia_dias")   # el total/aum del día cambió → refrescar tabla izquierda

    return {"ok": True, "fecha": fecha, "unidad": unidad, "precio": pos["precio"],
            "valuaciones": nuevas, "total_dia": total}
