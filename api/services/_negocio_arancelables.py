"""Filtro de `op` no arancelables para CashFlow.NegocioMovimientos.

Estos movimientos QUEDAN en la base (a diferencia de los de
`_negocio_informacion_filter.py` que se borran), pero NO van a tener arancel
nunca porque por naturaleza del movimiento no son arancelables (cobros del
emisor al tenedor, suscripciones/rescates de FCI sin comisión, aperturas de
caución, etc.).

Aplicado en:
- Endpoint `/api/manager/aunesa/boletos/faltantes`: no aparecen como
  "boletos sin arancel" (sería ruido visual — están bien así).
- `api/services/aunesa_aranceles.py::run_backfill`: no se cuentan como
  `sin_match` cuando Aunesa /informes no los devuelve (sería false positive).

Match: EXACTO en `op` (no substring). Lista canónica confirmada por el user.
"""
from __future__ import annotations

# Tupla canónica de `op` no arancelables.
OP_NO_ARANCELABLES: tuple[str, ...] = (
    "Rescate provisional",
    "Suscripción provisional",
    "Caución colocadora · Apertura",
    "Rescate final",
    "Caución tomadora · Apertura",
    "Solicitud suscripción FCI",
    "Interest payment",
    "Liquidación de suscripción",
    "Solicitud rescate FCI",
    "Cash dividend",
    "Partial redemption",
)


def match_solo_arancelables() -> dict:
    """Sub-doc $match para EXCLUIR los `op` no arancelables.

    Uso:
        {"$match": {"categoria": {"$in": ...}, **match_solo_arancelables()}}
    """
    return {"op": {"$nin": list(OP_NO_ARANCELABLES)}}
