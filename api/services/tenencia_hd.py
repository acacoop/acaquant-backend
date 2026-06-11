"""Service Tenencia Valorizada (cartera HD, cuentas propias 100/255/256).

Lee la colección materializada `Valuaciones.TenenciaHD` (1 doc/día, la escribe
`jobs/tenencia_hd.py`). Lógica pura — sin FastAPI. Dos lecturas:
  - `tenencia_dias`      → tabla izquierda: 1 fila por día, AuM HD de cada cuenta.
  - `tenencia_posiciones`→ tabla derecha: posiciones HD por título de un día.
"""
from __future__ import annotations

from typing import Any

from api.cache import cached
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
        fila = {"fecha": d["fecha_snapshot"], "total": d.get("total", 0.0)}
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
                     {"_id": 0, "fecha_snapshot": 1, "posiciones": 1, "total": 1})
    return {
        "fecha": fecha,
        "cuentas": CUENTAS,
        "total": (d or {}).get("total", 0.0),
        "posiciones": (d or {}).get("posiciones", []),
    }
