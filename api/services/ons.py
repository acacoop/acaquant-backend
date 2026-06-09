"""api/services/ons.py — maestro de ONs (Trading.BondsMaster) + sync a Curvas.

`Trading.BondsMaster` es la FUENTE editable de las ONs (panel Manager + seed
CLI). Este servicio centraliza:
  - el transform BondsMaster → doc de Trading.Curvas (curva='on_<sector>') que
    consumen el motor (motor_curvas) y la vista (renta_fija.listar_curva). Es
    el MISMO transform que usa `scripts/seed_curvas_ons.py` (sin divergencia).
  - el sync BondsMaster → Curvas (upsert + borra stale).
  - el CRUD para Manager (list / values / upsert / delete / set_sector).

Puro (sin FastAPI). Lectura con get_mongo_client_read(); escritura con
get_mongo_client(). El sector va codificado en la curva ('on_energia', etc.),
DB-driven (campo BondsMaster.sector). Ver `api/services/renta_fija.py`.
"""
from __future__ import annotations

from datetime import UTC, datetime

from pymongo import UpdateOne

from core.mongo import get_mongo_client, get_mongo_client_read

# Campos editables del maestro (lo que el form de Manager puede setear).
EDITABLES = ("emisor", "moneda_flujo", "tasa_cupon", "vencimiento", "sector", "tickers", "flujos")


def slug_sector(raw) -> str:
    """Normaliza el sector que se carga en BondsMaster.sector a un slug para la
    curva: 'Energía' → 'energia', 'ON Finanzas' → 'finanzas'. Vacío → 'otros'."""
    s = (raw or "otros").strip().lower()
    for a, b in (("á", "a"), ("é", "e"), ("í", "i"), ("ó", "o"), ("ú", "u")):
        s = s.replace(a, b)
    s = s.replace("on ", "").replace(" ", "_").strip("_")
    return s or "otros"


def _fecha_iso(raw) -> str | None:
    if isinstance(raw, datetime):
        return raw.date().isoformat()
    if isinstance(raw, str) and raw.strip():
        return raw.strip()[:10]
    return None


def _fecha_flujo_iso(raw) -> str | None:
    """Fecha de un flujo → ISO 'YYYY-MM-DD'. Acepta datetime o string."""
    if isinstance(raw, datetime):
        return raw.date().isoformat()
    if isinstance(raw, str) and raw.strip():
        try:
            return datetime.strptime(raw.strip()[:10], "%Y-%m-%d").date().isoformat()
        except ValueError:
            return None
    return None


def bondmaster_to_curva_doc(bm: dict) -> dict | None:
    """BondsMaster doc → doc de Trading.Curvas (curva='on_<sector>'). None si no
    se puede (sin asset o sin ticker). Pata canónica por moneda: USD → ticker D,
    ARS → ticker O. Flujos en shape nativo {fecha, amortizacion, interes,
    valor_residual} (el motor usa monto_flujo = amortizacion + interes)."""
    asset = bm.get("asset")
    moneda = (bm.get("moneda_flujo") or "USD").upper()
    tickers = bm.get("tickers") or {}
    ticker_full = tickers.get("USD") if moneda == "USD" else tickers.get("ARS")
    if not ticker_full:
        ticker_full = tickers.get("ARS") or tickers.get("USD")
    if not asset or not ticker_full:
        return None

    flujos = []
    for f in bm.get("flujos") or []:
        fi = _fecha_flujo_iso(f.get("fecha"))
        if not fi:
            continue
        flujos.append({
            "fecha": fi,
            "amortizacion": float(f.get("amortizacion", 0) or 0),
            "interes": float(f.get("interes", 0) or 0),
            "valor_residual": float(f.get("valor_residual", 100) or 100),
        })

    return {
        "ticker": ticker_full,
        "ticker_corto": asset,
        "curva": f"on_{slug_sector(bm.get('sector'))}",
        "moneda_flujo": moneda,
        "emisor": bm.get("emisor"),
        "sector": bm.get("sector") or "otros",
        "tasa_cupon": bm.get("tasa_cupon"),
        "valor_nominal": 100,
        "fecha_vencimiento": _fecha_iso(bm.get("vencimiento")),
        "flujos": flujos,
    }


def sync_ons_to_curvas() -> dict:
    """Sincroniza TODAS las ONs de BondsMaster → Trading.Curvas (upsert por
    ticker + borra las curva ^on que ya no estén). Idempotente. Devuelve counts.

    Lo llaman el panel Manager (tras cada mutación) y el seed CLI --commit."""
    read = get_mongo_client_read()["Trading"]["BondsMaster"]
    docs = [d for d in (bondmaster_to_curva_doc(b) for b in read.find({}, {"_id": 0})) if d]
    curvas = get_mongo_client()["Trading"]["Curvas"]
    ops = [UpdateOne({"ticker": d["ticker"]}, {"$set": d}, upsert=True) for d in docs]
    if ops:
        curvas.bulk_write(ops, ordered=False)
    tickers_ok = [d["ticker"] for d in docs]
    borradas = curvas.delete_many(
        {"curva": {"$regex": "^on"}, "ticker": {"$nin": tickers_ok}}).deleted_count
    return {"sincronizadas": len(ops), "borradas": borradas}


# ─────────────────────────────────────────────
# CRUD para Manager (panel TÍTULOS → ONs)
# ─────────────────────────────────────────────

def list_ons(sector: str | None = None, emisor: str | None = None) -> list[dict]:
    """ONs del maestro BondsMaster, con filtros opcionales, ordenadas por emisor."""
    read = get_mongo_client_read()["Trading"]["BondsMaster"]
    filtro: dict = {}
    if sector:
        filtro["sector"] = sector
    if emisor:
        filtro["emisor"] = emisor
    out = list(read.find(filtro, {"_id": 0}).limit(5000))
    out.sort(key=lambda d: ((d.get("emisor") or "").lower(), d.get("asset") or ""))
    return out


def ons_values() -> dict:
    """Valores únicos para filtros/autocomplete del panel."""
    read = get_mongo_client_read()["Trading"]["BondsMaster"]
    return {
        "emisores": sorted(e for e in read.distinct("emisor") if e),
        "sectores": sorted(s for s in read.distinct("sector") if s),
        "monedas": sorted(m for m in read.distinct("moneda_flujo") if m),
    }


def upsert_on(payload: dict, actor: str = "") -> dict:
    """Crea/edita una ON en BondsMaster (upsert por `asset`) y re-sincroniza
    Curvas. `payload` trae asset + los campos editables (incluido `flujos`,
    que reemplaza el array completo). Devuelve el doc guardado + el resultado
    del sync."""
    asset = (payload.get("asset") or "").strip()
    if not asset:
        raise ValueError("falta 'asset'")

    doc = {k: payload[k] for k in EDITABLES if k in payload}
    doc["asset"] = asset
    doc["actualizado_por"] = actor
    doc["actualizado_at"] = datetime.now(UTC)

    col = get_mongo_client()["Trading"]["BondsMaster"]
    col.update_one({"asset": asset}, {"$set": doc}, upsert=True)
    saved = col.find_one({"asset": asset}, {"_id": 0}) or {}
    sync = sync_ons_to_curvas()
    return {"on": saved, "sync": sync}


def delete_on(asset: str) -> dict:
    """Borra una ON de BondsMaster y la saca de Curvas (vía sync)."""
    asset = (asset or "").strip()
    if not asset:
        raise ValueError("falta 'asset'")
    col = get_mongo_client()["Trading"]["BondsMaster"]
    deleted = col.delete_one({"asset": asset}).deleted_count
    sync = sync_ons_to_curvas()
    return {"borrada": deleted, "sync": sync}


def set_sector(asset: str, sector: str, actor: str = "") -> dict:
    """Setea el sector de una ON (segmentación) y re-sincroniza. Live: el cambio
    de sector se refleja en la vista sin reiniciar motores."""
    asset = (asset or "").strip()
    if not asset:
        raise ValueError("falta 'asset'")
    col = get_mongo_client()["Trading"]["BondsMaster"]
    res = col.update_one(
        {"asset": asset},
        {"$set": {"sector": sector, "actualizado_por": actor, "actualizado_at": datetime.now(UTC)}})
    if res.matched_count == 0:
        raise ValueError(f"ON '{asset}' no existe")
    sync = sync_ons_to_curvas()
    saved = col.find_one({"asset": asset}, {"_id": 0}) or {}
    return {"on": saved, "sync": sync}
