"""Service puro — Pase Agro (Trigo / Maíz / Soja Rosario).

Arma la tabla PASE AGRO replicando la planilla de la mesa: por cada
commodity se rendea una fila PIZARRA (manual, editable), una fila DISPO
(placeholder #N/A) y N filas de futuros (last live de Trading.AgroSnapshot).

Cálculos puros (no se persisten):
- ars      = us  × dolar_oficial_mid
- pase     = us_pizarra − us_futuro
- tnav_us  = (us_pizarra / us_futuro)^(365/dias_a_vto) − 1   (compuesta)

La fórmula TNAV se validó contra la planilla:
- TRI.ROS/DIC26 last=229.60, pizarra=202.79, dias≈236 → -17.41% (planilla -17.47%)
- MAI.ROS/SEP26 last=191.90, pizarra=190.00, dias≈149 →  -2.41% (planilla -2.41%)
"""
from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from core.dolar_oficial import mid_oficial_live
from core.mongo import get_mongo_client, get_mongo_client_read

COMMODITY_ORDER = ("TRIGO", "MAIZ", "SOJA")

PIZARRA_LABELS = {
    "TRIGO": "TRIGO PIZARRA",
    "MAIZ":  "MAIZ PIZARRA",
    "SOJA":  "SOJA PIZARRA",
}

DISPO_LABELS = {
    "TRIGO": "TRI.ROS.P/DISPO",
    "MAIZ":  "MAI.ROS.P/DISPO",
    "SOJA":  "SOJ.ROS.P/DISPO",
}


def _dias_entre(mat_str: str, hoy: date) -> int:
    """Días calendario hoy→maturity (formato YYYYMMDD del snapshot)."""
    try:
        vto = date(int(mat_str[:4]), int(mat_str[4:6]), int(mat_str[6:8]))
        return max(1, (vto - hoy).days)
    except Exception:
        return 1


def _tnav_us(pizarra_us: float | None, last_us: float | None, dias: int) -> float | None:
    """TNAV compuesta en US$: (pizarra/last)^(365/dias) − 1.

    None si falta data o magnitudes no positivas. Devuelve fracción (no %).
    """
    if not pizarra_us or not last_us or pizarra_us <= 0 or last_us <= 0 or dias <= 0:
        return None
    try:
        return round((pizarra_us / last_us) ** (365 / dias) - 1, 6)
    except Exception:
        return None


def _validate_commodity(commodity: str) -> str:
    if commodity not in COMMODITY_ORDER:
        raise ValueError(f"Commodity inválido: {commodity}. Válidos: {COMMODITY_ORDER}")
    return commodity


# ─────────────────────────────────────────────────────────────────────────────
# READS
# ─────────────────────────────────────────────────────────────────────────────


def get_pase_agro() -> dict[str, Any]:
    """Tabla PASE AGRO completa, lista para renderear.

    Output:
        {
          "oficial": {"value": float | None, "ts": dt | None, "source": str},
          "ts":      datetime,
          "bloques": [
            {
              "commodity": "TRIGO",
              "rows": [
                {"tipo":"pizarra", "vencimiento":"2026-04-29", "posicion":"TRIGO PIZARRA", "us":202.79, "pase":None, "ars":..., "tnav_us":None},
                {"tipo":"dispo",   "vencimiento":"2026-04-29", "posicion":"TRI.ROS.P/DISPO", "us":None, ...},
                {"tipo":"futuro",  "vencimiento":"20260724", "posicion":"TRI.ROS/JUL26", "us":223.10, "pase":-20.31, "ars":..., "tnav_us":-0.3331, "bid":..., "offer":..., "vol_efectivo":..., "updated_at":dt},
                ...
              ]
            }, ...
          ]
        }
    """
    db_read = get_mongo_client_read()
    pizarras_raw = list(db_read["Derivados"]["AgroPizarra"].find({}))
    pizarras = {p["_id"]: p for p in pizarras_raw}
    snapshots = list(db_read["Trading"]["AgroSnapshot"].find({}))

    oficial = mid_oficial_live("oficial")
    oficial_value = oficial.get("value")

    hoy = date.today()
    bloques = []
    for commodity in COMMODITY_ORDER:
        bloques.append(_build_bloque(
            commodity, pizarras.get(commodity, {}), snapshots, oficial_value, hoy,
        ))

    return {
        "oficial": {
            "value":  oficial_value,
            "ts":     oficial.get("ts"),
            "source": oficial.get("source"),
        },
        "ts":      datetime.now(UTC),
        "bloques": bloques,
    }


def _build_bloque(
    commodity: str,
    pizarra: dict,
    snapshots: list[dict],
    oficial_value: float | None,
    hoy: date,
) -> dict[str, Any]:
    vto_p = pizarra.get("vencimiento_pizarra")
    us_p = pizarra.get("us_pizarra")
    ars_p = (us_p * oficial_value) if (us_p and oficial_value) else None

    pizarra_row = {
        "tipo":        "pizarra",
        "vencimiento": vto_p,
        "posicion":    PIZARRA_LABELS[commodity],
        "us":          us_p,
        "pase":        None,
        "ars":         round(ars_p, 2) if ars_p is not None else None,
        "tnav_us":     None,
        "updated_by":  pizarra.get("updated_by"),
        "updated_at":  pizarra.get("updated_at"),
    }

    dispo_row = {
        "tipo":        "dispo",
        "vencimiento": vto_p,
        "posicion":    DISPO_LABELS[commodity],
        "us":          None,
        "pase":        None,
        "ars":         None,
        "tnav_us":     None,
    }

    snaps_commodity = sorted(
        (s for s in snapshots if s.get("commodity") == commodity),
        key=lambda s: s.get("vencimiento") or "9999",
    )

    futuros_rows = []
    for s in snaps_commodity:
        ticker = s.get("ticker", "")
        mat = s.get("vencimiento") or ""
        last = s.get("last_price")
        dias = s.get("dias_a_vto") or _dias_entre(mat, hoy)

        ars_f = round(last * oficial_value, 2) if (last and oficial_value) else None
        # Si no hay last, pase queda None (no replicamos el comportamiento de
        # Excel donde celda vacía = 0 → pase = pizarra).
        pase = round(us_p - last, 4) if (us_p and last) else None
        tnav = _tnav_us(us_p, last, dias)

        futuros_rows.append({
            "tipo":          "futuro",
            "ticker":        ticker,
            "vencimiento":   mat,
            "posicion":      ticker,
            "us":            last,
            "pase":          pase,
            "ars":           ars_f,
            "tnav_us":       tnav,
            "bid":           s.get("bid_price"),
            "offer":         s.get("offer_price"),
            "vol_efectivo":  s.get("vol_efectivo"),
            "dias_a_vto":    dias,
            "updated_at":    s.get("updated_at"),
        })

    return {
        "commodity": commodity,
        "rows":      [pizarra_row, dispo_row, *futuros_rows],
    }


# ─────────────────────────────────────────────────────────────────────────────
# WRITES — solo trader+admin (gate en el router)
# ─────────────────────────────────────────────────────────────────────────────


def set_pizarra(
    commodity: str,
    vencimiento_pizarra: str | None,
    us_pizarra: float | None,
    email: str,
) -> dict[str, Any]:
    """Upsert manual de la fila PIZARRA + audit en Derivados.AgroPizarraAudit.

    `vencimiento_pizarra` y `us_pizarra` se pueden actualizar de a uno —
    null/None = no tocar ese campo. Si ambos son None y no existe doc previo,
    crea uno vacío.
    """
    _validate_commodity(commodity)

    client = get_mongo_client()
    col = client["Derivados"]["AgroPizarra"]
    audit = client["Derivados"]["AgroPizarraAudit"]
    now = datetime.now(UTC)

    prev = col.find_one({"_id": commodity}) or {}
    new = {
        "_id":                 commodity,
        "vencimiento_pizarra": vencimiento_pizarra
                                if vencimiento_pizarra is not None
                                else prev.get("vencimiento_pizarra"),
        "us_pizarra":          float(us_pizarra)
                                if us_pizarra is not None
                                else prev.get("us_pizarra"),
        "updated_by":          email,
        "updated_at":          now,
    }
    if new["us_pizarra"] is not None and new["us_pizarra"] <= 0:
        raise ValueError("us_pizarra debe ser > 0")

    col.replace_one({"_id": commodity}, new, upsert=True)

    audit.insert_one({
        "commodity":  commodity,
        "prev": {
            "vencimiento_pizarra": prev.get("vencimiento_pizarra"),
            "us_pizarra":          prev.get("us_pizarra"),
        },
        "new": {
            "vencimiento_pizarra": new["vencimiento_pizarra"],
            "us_pizarra":          new["us_pizarra"],
        },
        "updated_by": email,
        "updated_at": now,
    })
    return new
