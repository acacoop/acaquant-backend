"""core/postrade_posicion.py — PositionReport → posición de FUTUROS, plana.

Traer la posición de la cámara y dejarla usable son dos cosas distintas, y esta
es la segunda. `PosTrade/PositionReport` devuelve una estructura ANIDADA (el
instrumento adentro de un objeto, las cantidades adentro de un array) y mezcla
futuros con lo que no lo es. Acá se aplana, se filtra y se completa lo que la
API omite.

Está separado del transporte para que `aplanar()` sea **pura**: recibe la
respuesta y devuelve filas, sin red y sin base. Eso es lo que la hace testeable
de verdad — las reglas de abajo se verifican sin llamar a producción.

## Tres reglas que no son obvias

**1. La API OMITE el campo cuando la cantidad es cero.** No manda `LongQty: 0`:
directamente no manda `LongQty`. Si eso se guardara como NULL, "no tengo
posición larga" y "no sé si tengo posición larga" quedarían escritos igual —
y solo uno de los dos es cierto. Se normaliza a **0**.

**2. `PositionQty` es un ARRAY y se EXPANDE.** Un mismo instrumento en una
misma cuenta puede traer varios tipos de posición. Guardarlo como blob obligaría
a re-parsear en cada consulta y haría imposible sumar por tipo.

**3. Solo `SecurityType == 'Futuro'`.** Opciones, PAF G y cualquier otro tipo
quedan afuera — y se CUENTAN, no se descartan en silencio: si un día el mix
cambia, el número de descartados es lo único que lo delata.
"""
from __future__ import annotations

import logging
from typing import Any

from core import postrade

logger = logging.getLogger(__name__)

METODO = "PositionReport"
SECURITY_TYPE_FUTURO = "Futuro"


def _num(v: Any) -> float:
    """El número, o 0 si la API omitió el campo (que es como manda los ceros)."""
    if v is None or v == "":
        return 0.0
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _opcional(v: Any) -> float | None:
    """Igual que `_num` pero preserva el NULL: para precios e importes, donde
    "no vino" y "vale cero" NO son lo mismo (un settlement de 0 es un dato)."""
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def aplanar(crudo: Any) -> tuple[list[dict], dict[str, int]]:
    """`Value` de PositionReport → (filas planas de futuros, contadores).

    PURA: no toca red ni base. Devuelve también los contadores porque el
    descarte tiene que ser visible — un filtro silencioso es indistinguible de
    un bug que se come la mitad de los datos.
    """
    filas: list[dict] = []
    stats = {"recibidas": 0, "futuros": 0, "descartadas_no_futuro": 0, "sin_position_qty": 0}

    if not isinstance(crudo, list):
        return filas, stats

    for p in crudo:
        if not isinstance(p, dict):
            continue
        stats["recibidas"] += 1

        inst = p.get("Instrument") or {}
        if not isinstance(inst, dict):
            inst = {}
        if str(inst.get("SecurityType") or "").strip() != SECURITY_TYPE_FUTURO:
            stats["descartadas_no_futuro"] += 1
            continue
        stats["futuros"] += 1

        # La fecha viene ISO con o sin hora ("2026-08-21", "2026-08-21T00:00:00").
        business_date = str(p.get("ClearingBusinessDate") or "")[:10]

        cantidades = p.get("PositionQty")
        if not isinstance(cantidades, list) or not cantidades:
            # Un futuro sin cantidades no es una posición. Se cuenta para que
            # no desaparezca sin dejar rastro.
            stats["sin_position_qty"] += 1
            continue

        for q in cantidades:
            if not isinstance(q, dict):
                continue
            filas.append({
                "business_date": business_date,
                "account": str(p.get("Account") or "").strip(),
                "symbol": str(inst.get("Symbol") or "").strip(),
                "position_type": str(q.get("PosType") or "").strip(),
                "cfi_code": inst.get("CFICode"),
                "unit_of_measure": inst.get("UnitOfMeasure"),
                "currency": p.get("Currency"),
                "avg_px": _opcional(p.get("AvgPX")),
                "daily_settlement": _opcional(p.get("DailySettlement")),
                "settlement_price": _opcional(p.get("SettlPrice")),
                "settlement_currency": p.get("SettlCurrency"),
                "long_qty": _num(q.get("LongQty")),
                "short_qty": _num(q.get("ShortQty")),
            })

    return filas, stats


def traer(fecha: str) -> tuple[list[dict], dict[str, int]]:
    """Pide la posición de `fecha` (AAAAMMDD) y la devuelve aplanada.

    ⚠️ **`viewDetails=false`, y esto se midió (2026-08-24).** El parámetro no
    cambia el formato: cambia QUÉ se devuelve.

    - `viewDetails=true` NO es "la posición con más detalle": son las
      **operaciones individuales**. Cada fila trae su `ExecID`, `TradeNumber` y
      `PX`. Para una sola posición devolvió **1115 filas** — un trade cada una.
    - `viewDetails=false` es la **posición consolidada**, y consolida bien:
      medido contra la suma de esos 1115 trades da idéntico
      (LongQty 1.889,0 y DailySettlement 1.694.750,0 en las dos).

    Se usa el consolidado en vez de sumar el detalle porque sumarlo sería
    reimplementar algo que la cámara ya hace: cualquier diferencia futura entre
    nuestra suma y la de ellos sería un bug nuestro, y encima uno silencioso.
    De yapa el consolidado trae `AvgPX`, que el detalle no tiene.

    **Sin `viewPafg`**: ese parámetro cambia el reporte por el de contratos PAF
    G, que es otra cosa y no es lo que se pide acá.
    """
    crudo = postrade.leer(METODO, {
        "clearingBusinessDate": postrade.fecha_api(fecha),
        "viewDetails": "false",
    })
    filas, stats = aplanar(crudo)
    logger.info(
        "PositionReport %s: %d recibidas, %d futuros → %d filas",
        fecha, stats["recibidas"], stats["futuros"], len(filas),
    )
    return filas, stats
