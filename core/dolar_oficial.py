"""Fuente única para el "dólar oficial" usado en watchlist + cálculo de
futuros DLR + cualquier endpoint que reporte spot de referencia.

Antes el cálculo estaba duplicado:
  - api/services/argy.py usaba `venta` directamente (1430).
  - engines/futuros_dlr.py usaba `(compra+venta)/2` (1405).

Eso hacía que la watchlist mostrara un número distinto del TC que la
tabla de futuros usaba para el directo y la TNA. La mesa pidió mid
porque es la mejor proxy del A3500 (que liquida los futuros) cuando
el fixing del día todavía no salió.

Esta helper es la fuente única: mid = (compra+venta)/2 si ambos > 0,
fallback a `venta` si solo hay venta.

Lee de Valuaciones.DolarOficial — escrito por `jobs/dolar_api.py` cada
5 min durante la rueda con datos de dolarapi.com.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from core.mongo import get_mongo_client_read

DB = "Valuaciones"
COL = "DolarOficial"


def _mid(compra: float | None, venta: float | None) -> float | None:
    """Mid de las puntas. None si no hay venta."""
    try:
        v = float(venta) if venta is not None else 0.0
    except (TypeError, ValueError):
        return None
    if v <= 0:
        return None
    try:
        c = float(compra) if compra is not None else 0.0
    except (TypeError, ValueError):
        c = 0.0
    if c > 0:
        return (c + v) / 2
    return v


def mid_oficial_live(casa: str = "oficial") -> dict[str, Any]:
    """Snapshot live del dólar oficial. Devuelve mid + ambas puntas.

    Returns:
        {
          "value":  float | None,   # mid
          "compra": float | None,
          "venta":  float | None,
          "ts":     datetime | None,
          "source": "dolarapi.com" | "none",
        }
    """
    doc = get_mongo_client_read()[DB][COL].find_one(
        {"casa": casa},
        {"_id": 0, "venta": 1, "compra": 1, "fechaActualizacion": 1, "updated_at": 1},
        sort=[("updated_at", -1)],
    )
    if not doc:
        return {"value": None, "compra": None, "venta": None, "ts": None, "source": "none"}
    compra = doc.get("compra")
    venta = doc.get("venta")
    ts = doc.get("fechaActualizacion") or doc.get("updated_at")
    return {
        "value":  _mid(compra, venta),
        "compra": compra,
        "venta":  venta,
        "ts":     ts,
        "source": "dolarapi.com",
    }


def serie_oficial_mid(casa: str = "oficial") -> list[tuple[Any, float]]:
    """Serie histórica del mid oficial. Cada entry: (fecha, mid).

    Lee Valuaciones.DolarOficial filtrando por casa, ordenado ascendente
    por fecha. Las entradas sin venta válida se descartan.
    """
    cursor = (
        get_mongo_client_read()[DB][COL]
        .find(
            {"casa": casa},
            {"_id": 0, "venta": 1, "compra": 1, "fecha": 1, "fechaActualizacion": 1},
        )
        .sort("fechaActualizacion", 1)
    )
    out: list[tuple[Any, float]] = []
    for doc in cursor:
        m = _mid(doc.get("compra"), doc.get("venta"))
        if m is None:
            continue
        f = doc.get("fecha") or doc.get("fechaActualizacion")
        if f is None:
            continue
        # Aceptamos `date` o ISO string; `argy._serie_dolar_api` lo
        # normaliza después usando _last_le.
        if isinstance(f, datetime):
            f = f.date()
        out.append((f, m))
    return out
