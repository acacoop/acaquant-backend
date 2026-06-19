"""api/services/bonos_admin.py — alta/edición de bonos NO-ON directo en Trading.Curvas.

Las ONs viven en BondsMaster y se sincronizan a Curvas (ver `ons.py`). Los bonos
soberanos / tasa_fija (Lecaps/Boncaps) / CER viven DIRECTO en `Trading.Curvas`
(no hay master intermedio). Este servicio les da el mismo CRUD que las ONs, para
que el panel Manager → TÍTULOS los gestione desde un solo lugar.

Dos formas de flujo:
  - BULLET (Lecap/Boncap tasa_fija): `flujo_vencimiento` (por 100 VN) al `fecha_vencimiento`.
    No lleva array de flujos. El motor de acreencias/valuación lo proyecta como pago único.
  - CRONOGRAMA (cupón / amortización): array `flujos` [{fecha, amortizacion, interes,
    valor_residual}] — mismo shape y parser que las ONs (`ons.parse_flujos_texto`).

Escritura directa a Curvas con upsert por `ticker_corto` (su clave única) → el motor
lo toma en el próximo loop. Puro (sin FastAPI).
"""
from __future__ import annotations

from datetime import UTC, datetime

from api.services.ons import _f, _fecha_flujo_iso, _fecha_iso
from core.mongo import get_mongo_client, get_mongo_client_read

# Curvas que gestiona este editor (las ONs van por ons.py; mercado las maneja el motor).
CURVAS_BONO = ("tasa_fija", "cer", "soberanos", "dolar_linked", "tamar", "dual")


def list_bonos(curva: str | None = None) -> list[dict]:
    """Bonos NO-ON de Trading.Curvas (excluye curva ^on, que son del editor de ONs)."""
    read = get_mongo_client_read()["Trading"]["Curvas"]
    filtro: dict = {"curva": {"$not": {"$regex": "^on"}}}
    if curva:
        filtro["curva"] = curva
    out = list(read.find(filtro, {"_id": 0}).limit(5000))
    out.sort(key=lambda d: (d.get("curva") or "", str(d.get("fecha_vencimiento") or ""),
                            d.get("ticker_corto") or ""))
    return out


# Campos doc nivel-bono que el editor puede setear (según tipo).
_DOC_STR = ("tipo", "moneda_flujo", "tasa_referencia")     # strings tal cual
_DOC_NUM = ("cer_emision", "cupon_anual")                  # numéricos
_DOC_FECHA = ("fecha_emision", "fecha_vencimiento")        # fechas ISO
# Campos numéricos de un FLUJO (pass-through; el subset depende del tipo de bono).
_FLUJO_NUM = ("amortizacion", "interes", "valor_residual", "amortizacion_pct",
              "cupon_sobre_residual", "residual_previo_pct", "cupon_anual")


def upsert_bono(payload: dict, actor: str = "") -> dict:
    """Crea/edita un bono directo en Trading.Curvas (upsert por `ticker_corto`).
    Guarda las MISMAS shapes de Trading.Curvas según el tipo (no se inventa nada):
      - lecap/boncap (tasa_fija) → bullet `flujo_vencimiento`.
      - cer  → flujos {fecha, amortizacion_pct, cupon_sobre_residual, residual_previo_pct} + cer_emision/cupon_anual.
      - dual/tamar → flujos {fecha, amortizacion_pct} + tasa_referencia.
      - tasa_fija con cupón → flujos {fecha, amortizacion, interes}.
      - soberanos → flujos {fecha, amortizacion_pct, cupon_sobre_residual}.
    """
    tc = (payload.get("ticker_corto") or "").strip()
    if not tc:
        raise ValueError("falta 'ticker_corto'")
    ticker = (payload.get("ticker") or "").strip()
    if not ticker:
        raise ValueError("falta 'ticker' (completo, ej 'MERV - XMEV - T30J6 - 24hs')")
    curva = (payload.get("curva") or "").strip()
    if curva not in CURVAS_BONO:
        raise ValueError(f"curva inválida: {curva!r} (válidas: {', '.join(CURVAS_BONO)})")

    doc: dict = {
        "ticker": ticker,
        "ticker_corto": tc,
        "curva": curva,
        "valor_nominal": _f(payload.get("valor_nominal"), 100.0) or 100.0,
        "actualizado_por": actor,
        "actualizado_at": datetime.now(UTC),
    }
    for k in _DOC_STR:
        v = payload.get(k)
        if v not in (None, ""):
            doc[k] = v.upper() if k == "moneda_flujo" else v
    for k in _DOC_NUM:
        if payload.get(k) is not None:
            doc[k] = _f(payload.get(k))
    for k in _DOC_FECHA:
        fi = _fecha_iso(payload.get(k))
        if fi:
            doc[k] = fi

    # Flujo: bullet (Lecap/Boncap) o cronograma (pass-through de los campos del tipo).
    fv = payload.get("flujo_vencimiento")
    flujos_in = payload.get("flujos")
    if flujos_in:
        flujos = []
        for fl in flujos_in:
            fi = _fecha_flujo_iso(fl.get("fecha"))
            if not fi:
                continue
            row: dict = {"fecha": fi}
            for ff in _FLUJO_NUM:
                if fl.get(ff) is not None:
                    row[ff] = _f(fl.get(ff))
            flujos.append(row)
        if not flujos:
            raise ValueError("los flujos no tienen ninguna fecha válida")
        doc["flujos"] = flujos
        doc["flujo_vencimiento"] = None   # cronograma → sin bullet
    elif fv is not None and _f(fv) > 0:
        if not doc.get("fecha_vencimiento"):
            raise ValueError("el bullet (flujo_vencimiento) necesita 'fecha_vencimiento'")
        doc["flujo_vencimiento"] = _f(fv)
    else:
        raise ValueError("falta el flujo: 'flujo_vencimiento' (bullet) o 'flujos' (cronograma)")

    col = get_mongo_client()["Trading"]["Curvas"]
    col.update_one({"ticker_corto": tc}, {"$set": doc}, upsert=True)
    saved = col.find_one({"ticker_corto": tc}, {"_id": 0}) or {}
    return {"bono": saved}


def delete_bono(ticker_corto: str) -> dict:
    """Baja un bono de Trading.Curvas por ticker_corto (no toca ONs ^on)."""
    tc = (ticker_corto or "").strip()
    if not tc:
        raise ValueError("falta 'ticker_corto'")
    res = get_mongo_client()["Trading"]["Curvas"].delete_one(
        {"ticker_corto": tc, "curva": {"$not": {"$regex": "^on"}}})
    return {"borrado": res.deleted_count}
