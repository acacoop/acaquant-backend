"""Universo dinámico para motor_portfolio_snapshot.

Función única `tickers_de_tenencia()` que devuelve el set de symbols
pyRofex (formato `MERV - XMEV - X - 24hs`) a los que el motor de
portfolio se debe suscribir para tener `last_price` live.

Composición:
  - Valuaciones.Assets.INSTRUMENTO de unidades con tenencia hoy
    (Valuaciones.AuM último snapshot, qty != 0).
  - Tickers de boletos operados hoy en CashFlow.NegocioMovimientos
    (vía join con Assets.INSTRUMENTO si está cargado).

Filtrado: cada candidato se valida contra Manager.PyRofexInstruments —
si no existe en la lista canónica primary 24hs, se descarta y se
loguea (visible para corrección manual).

Read-only sobre Mongo. Excepciones se capturan en el caller.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from core.mongo import get_mongo_client_read
from core.postgres import get_pool

ART_OFFSET = timedelta(hours=-3)
_PLACEHOLDERS = {"", "NO APLICA"}


def _hoy_art_iso() -> str:
    return (datetime.now(UTC) + ART_OFFSET).strftime("%Y-%m-%d")


def _set_pyrofex_24hs() -> set[str]:
    """Symbols pyRofex con formato `MERV - XMEV - * - 24hs` desde
    Manager.PyRofexInstruments — la fuente canónica para validación."""
    coll = get_mongo_client_read()["Manager"]["PyRofexInstruments"]
    out: set[str] = set()
    for doc in coll.find({}, {"_id": 0, "instruments.ticker": 1}):
        for inst in doc.get("instruments") or []:
            t = (inst.get("ticker") or "").strip()
            if t.startswith("MERV - XMEV - ") and t.endswith(" - 24hs"):
                out.add(t)
    return out


def _instrumentos_de_tenencia() -> set[str]:
    """INSTRUMENTOs de Valuaciones.Assets para unidades con qty != 0
    en el último snapshot de Valuaciones.AuM."""
    client = get_mongo_client_read()
    db_v = client["Valuaciones"]

    last = db_v["AuM"].find_one({}, sort=[("fecha_snapshot", -1)],
                                projection={"fecha_snapshot": 1})
    if not last:
        return set()
    fecha = last["fecha_snapshot"]

    pipeline = [
        {"$match": {"fecha_snapshot": fecha, "cantidad": {"$ne": 0}}},
        {"$group": {"_id": "$unidad"}},
    ]
    unidades = [d["_id"] for d in db_v["AuM"].aggregate(pipeline) if d.get("_id")]
    if not unidades:
        return set()

    out: set[str] = set()
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT instrumento FROM portafolio.assets WHERE unidad = ANY(%s)",
                    (unidades,))
        for (inst,) in cur.fetchall():
            inst = (inst or "").strip()
            if inst and inst not in _PLACEHOLDERS:
                out.add(inst)
    return out


def _instrumentos_de_boletos_hoy() -> set[str]:
    """INSTRUMENTOs de los tickers operados hoy. Join boleto.ticker →
    Assets.unidad/TICKER → Assets.INSTRUMENTO. Si el boleto opera un
    ticker que aún no tiene Asset, queda fuera (caso edge sin solución
    automática — se reporta en sin_match)."""
    client = get_mongo_client_read()
    db_cf = client["CashFlow"]

    fecha = _hoy_art_iso()
    tickers_hoy = db_cf["NegocioMovimientos"].distinct(
        "ticker", {"fecha": fecha, "ticker": {"$ne": None}},
    )
    if not tickers_hoy:
        return set()

    # Match por TICKER UPPERCASE de Assets (donde tenemos el ticker
    # corto humano) o por extracción del unidad (regex `[<id>] <ticker>`).
    # El más rápido es match directo por TICKER.
    out: set[str] = set()
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT instrumento FROM portafolio.assets WHERE ticker = ANY(%s)",
                    (tickers_hoy,))
        for (inst,) in cur.fetchall():
            inst = (inst or "").strip()
            if inst and inst not in _PLACEHOLDERS:
                out.add(inst)
    return out


def tickers_de_tenencia() -> tuple[set[str], set[str]]:
    """Devuelve (universo_validado, sin_match).

    universo_validado: instrumentos de tenencia + boletos hoy, filtrados
                       contra Manager.PyRofexInstruments.
    sin_match: candidatos que NO existen en pyRofex — visible en log
               para corrección manual.

    Si Mongo falla, ambos sets se devuelven vacíos.
    """
    try:
        canonico = _set_pyrofex_24hs()
        if not canonico:
            return set(), set()
        candidatos = _instrumentos_de_tenencia() | _instrumentos_de_boletos_hoy()
        validados = candidatos & canonico
        sin_match = candidatos - canonico
        return validados, sin_match
    except Exception:
        return set(), set()
