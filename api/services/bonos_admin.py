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


def upsert_bono(payload: dict, actor: str = "") -> dict:
    """Crea/edita un bono directo en Trading.Curvas (upsert por `ticker_corto`).

    Requiere `ticker_corto`, `ticker` (full, lo usa el motor) y `curva` válida.
    Flujo: o `flujo_vencimiento` (bullet) o `flujos` (cronograma) — al menos uno.
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
    if payload.get("tipo"):
        doc["tipo"] = payload["tipo"]
    if payload.get("moneda_flujo"):
        doc["moneda_flujo"] = payload["moneda_flujo"].upper()
    venc = _fecha_iso(payload.get("fecha_vencimiento"))
    if venc:
        doc["fecha_vencimiento"] = venc
    if payload.get("cer_emision") is not None:
        doc["cer_emision"] = _f(payload.get("cer_emision"))

    # Flujo: bullet o cronograma.
    fv = payload.get("flujo_vencimiento")
    flujos_in = payload.get("flujos")
    if flujos_in:
        flujos = []
        for fl in flujos_in:
            fi = _fecha_flujo_iso(fl.get("fecha"))
            if not fi:
                continue
            flujos.append({
                "fecha": fi,
                "amortizacion": _f(fl.get("amortizacion"), 0.0),
                "interes": _f(fl.get("interes"), 0.0),
                "valor_residual": _f(fl.get("valor_residual"), 100.0) or 100.0,
            })
        if not flujos:
            raise ValueError("los flujos no tienen ninguna fecha válida")
        doc["flujos"] = flujos
        # un cronograma deja sin sentido el bullet → lo limpiamos si existía
        doc["flujo_vencimiento"] = None
    elif fv is not None and _f(fv) > 0:
        if not venc:
            raise ValueError("el bullet (flujo_vencimiento) necesita 'fecha_vencimiento'")
        doc["flujo_vencimiento"] = _f(fv)
    else:
        raise ValueError("falta el flujo: pasá 'flujo_vencimiento' (bullet) o 'flujos' (cronograma)")

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
