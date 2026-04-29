"""Fuente única para el "dólar oficial" mayorista.

Lee de Valuaciones.DolarOficialLive — escrito por el script `mae_forex.py`
que corre en una PC dedicada en la oficina (la API key de MAE se bloqueó
desde el Droplet, IP rejected). El script pollea api.mae.com.ar cada 30s
y upsertea el último precio + variación % del ticker UST$T (USA
Transferencia mayorista A3500).

Antes leía Valuaciones.DolarOficial (dolarapi.com — feed retail con 5min
de delay). Eso hacía que la watchlist y los futuros DLR mostraran un
número distinto del A3500 que liquida los futuros (~30 pesos arriba,
porque "oficial" en dolarapi es retail bancario, no mayorista). Ahora
MAE da exactamente UST$T = el mayorista que necesitamos.

Si el script local está caído (PC apagada, Internet caída, IP no
whitelisteada en Atlas), `value` queda en None y el frontend muestra
"—". NO hay fallback silencioso a dolarapi, porque ese feed es de
retail (~30 pesos arriba) y confundiría a la mesa.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from core.mongo import get_mongo_client_read

DB = "Valuaciones"
COL_LIVE = "DolarOficialLive"   # MAE (script local oficina)
COL_HIST = "DolarOficial"       # dolarapi.com cron 5min — solo para series históricas

MAE_TICKER_OFICIAL = "UST$T"    # USA Transferencia mayorista (proxy A3500 spot)


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
        {"data.ticker": MAE_TICKER_OFICIAL},
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


def serie_oficial_mid(casa: str = "oficial") -> list[tuple[Any, float]]:
    """Serie histórica del oficial — anchors 7d/MTD/YTD del watchlist.

    Sigue leyendo Valuaciones.DolarOficial (dolarapi.com) por ahora,
    porque DolarOficialLive recién arrancó hoy y no tiene history.
    Cuando MAE acumule >30 días, podemos migrar la serie también.

    Mientras tanto: el spot live es MAE (mayorista ~1400) y los anchors
    son dolarapi (retail ~1430). Las variaciones porcentuales día a día
    son comparables en magnitud aunque el nivel difiera. Es un compromiso
    consciente — la alternativa era que 7d/MTD/YTD quedaran en None hasta
    juntar history.
    """
    cursor = (
        get_mongo_client_read()[DB][COL_HIST]
        .find(
            {"casa": casa},
            {"_id": 0, "venta": 1, "compra": 1, "fecha": 1, "fechaActualizacion": 1},
        )
        .sort("fechaActualizacion", 1)
    )
    out: list[tuple[Any, float]] = []
    for doc in cursor:
        venta = doc.get("venta")
        compra = doc.get("compra")
        try:
            v = float(venta) if venta is not None else 0.0
        except (TypeError, ValueError):
            continue
        if v <= 0:
            continue
        try:
            c = float(compra) if compra is not None else 0.0
        except (TypeError, ValueError):
            c = 0.0
        m = (c + v) / 2 if c > 0 else v
        f = doc.get("fecha") or doc.get("fechaActualizacion")
        if f is None:
            continue
        if isinstance(f, datetime):
            f = f.date()
        out.append((f, m))
    return out
