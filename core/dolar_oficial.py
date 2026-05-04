"""Fuente única para el "dólar oficial" mayorista.

Lee de Valuaciones.DolarOficialLive — escrito por el script `mae_forex.py`
que corre en una PC dedicada en la oficina (la API key de MAE se bloqueó
desde el Droplet, IP rejected). El script pollea api.mae.com.ar cada 30s
y upsertea el último precio + variación % del ticker UST$T (USA
Transferencia mayorista A3500).

Si el script local está caído (PC apagada, Internet caída, IP no
whitelisteada en Atlas), `value` queda en None y el frontend muestra
"—". Sin fallback: el feed retail (dolarapi.com) está ~30 pesos arriba
del mayorista y confundiría a la mesa. La integración con dolarapi se
eliminó el 2026-05-04.
"""
from __future__ import annotations

from typing import Any

from core.mongo import get_mongo_client_read

DB = "Valuaciones"
COL_LIVE = "DolarOficialLive"   # MAE (script local oficina)

# Filtro EXACTO del dólar oficial mayorista spot (= A3500 / liquidación T+0).
# MAE devuelve muchos instrumentos con `ticker == "UST$T"` (mayorista,
# minorista, T+0, T+1...). El "dólar oficial" que usa la mesa y que liquida
# los futuros DLR es Mayorista (M) plazo 000 (contado, mismo día).
# Sin este filtro estricto, agarraríamos cualquiera y el TC saltaría 5
# pesos al azar entre plazos.
MAE_FILTER_OFICIAL = {
    "data.ticker":         "UST$T",
    "data.codigoSegmento": "M",
    "data.codigoPlazo":    "000",
}


def mid_oficial_live(casa: str = "oficial") -> dict[str, Any]:
    """Spot mayorista vivo desde MAE.

    Returns:
        {
          "value":     float | None,    # data.precioUltimo
          "variacion": float | None,    # data.variacion (% del día, ya en %)
          "ts":        datetime | None,
          "source":    "mae_local_pc" | "none",
        }

    `casa` queda como parámetro por compat con la API anterior — pero
    MAE solo expone el mayorista oficial. Si `casa != "oficial"`,
    devuelve none.
    """
    if casa != "oficial":
        return {"value": None, "variacion": None, "ts": None, "source": "none"}

    doc = get_mongo_client_read()[DB][COL_LIVE].find_one(
        MAE_FILTER_OFICIAL,
        {"_id": 0, "data.precioUltimo": 1, "data.variacion": 1, "updated_at": 1},
        sort=[("updated_at", -1)],
    )
    if not doc:
        return {"value": None, "variacion": None, "ts": None, "source": "none"}

    data = doc.get("data") or {}
    px = data.get("precioUltimo")
    var = data.get("variacion")
    try:
        value = float(px) if px is not None else None
    except (TypeError, ValueError):
        value = None
    try:
        variacion = float(var) if var is not None else None
    except (TypeError, ValueError):
        variacion = None

    return {
        "value":     value,
        "variacion": variacion,
        "ts":        doc.get("updated_at"),
        "source":    "mae_local_pc",
    }
