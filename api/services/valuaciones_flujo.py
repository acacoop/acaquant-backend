"""api/services/valuaciones_flujo.py — flujo directo de la cartera por cuenta.

NEGOCIO → Valuaciones (solo admin + asistente_comercial). Arma el flujo NETO de
una cuenta desde los movimientos de títulos (CashFlow.NegocioMovimientos — los
mismos boletos del PnL Títulos), por mes y categoría, para TODA la cartera. Así
se evita el ruido de depósitos/extracciones administrativas (que NO son boletos
de títulos y rompen el neto por depósitos/extracciones).

5 columnas: compra→COMPRAS, venta→VENTAS, rescate_fci→RESCATES,
suscripcion_fci→SUSCRIPCIONES, acreencia→OTROS.

El usuario incluye/excluye movimientos (sí/no); la elección se guarda POR CUENTA
en CashFlow.ValuacionFlujoExcluidos (set de `comprobante` excluidos; default =
todos incluidos). El flujo neto considera solo los incluidos.

Pesificación idéntica al PnL (api/services/pnl.py::_pesificar): ARS → importe;
otra moneda → importe × mep (mep del boleto; fallback get_mep_for_date).
"""
from __future__ import annotations

from datetime import UTC, datetime

from api.services._mep import get_mep_for_date
from core.mongo import get_mongo_client, get_mongo_client_read

# Mapeo categoria del boleto → columna de la vista. Mismo universo que el PnL.
_CAT_TO_COL: dict[str, str] = {
    "compra":          "COMPRAS",
    "venta":           "VENTAS",
    "rescate_fci":     "RESCATES",
    "suscripcion_fci": "SUSCRIPCIONES",
    "acreencia":       "OTROS",
}
COLUMNAS: tuple[str, ...] = ("COMPRAS", "VENTAS", "RESCATES", "SUSCRIPCIONES", "OTROS")

_SEL_DB, _SEL_COL = "CashFlow", "ValuacionFlujoExcluidos"


def _mes(fecha) -> str:
    if isinstance(fecha, datetime):
        return fecha.strftime("%Y-%m")
    return str(fecha or "")[:7]


def _pesificar(importe, moneda, mep, fecha, cache: dict) -> float:
    try:
        imp = float(importe or 0)
    except (TypeError, ValueError):
        return 0.0
    if (moneda or "ARS") == "ARS":
        return imp
    if mep is None:
        f = str(fecha or "")
        if f not in cache:
            cache[f] = get_mep_for_date(f)
        mep = cache[f]
    if not mep or mep <= 0:
        return imp  # fallback: queda en moneda original (no se pudo pesificar)
    return imp * mep


def _excluidos(id_cuenta: str) -> set:
    doc = get_mongo_client_read()[_SEL_DB][_SEL_COL].find_one(
        {"id_cuenta": str(id_cuenta)}, {"_id": 0, "excluidos": 1})
    return set(doc.get("excluidos") or []) if doc else set()


def get_flujo(id_cuenta: str) -> dict:
    """Movimientos de títulos de la cuenta (con flag `incluido`) para la vista.

    El frontend arma la matriz mes×categoría sumando los `incluido=True`.
    """
    db_cf = get_mongo_client_read()["CashFlow"]
    cur = db_cf["NegocioMovimientos"].find(
        {"id_cuenta": str(id_cuenta), "categoria": {"$in": list(_CAT_TO_COL)}},
        {"_id": 0, "fecha": 1, "categoria": 1, "op": 1, "ticker": 1,
         "importe": 1, "moneda": 1, "comprobante": 1, "mep": 1},
    ).sort([("fecha", 1), ("comprobante", 1)])

    excl = _excluidos(id_cuenta)
    cache: dict = {}
    movimientos: list[dict] = []
    for m in cur:
        comp = m.get("comprobante")
        importe_ars = _pesificar(
            m.get("importe"), m.get("moneda"), m.get("mep"), m.get("fecha"), cache)
        movimientos.append({
            "comprobante": comp,
            "fecha":       m.get("fecha"),
            "mes":         _mes(m.get("fecha")),
            "categoria":   _CAT_TO_COL.get(m.get("categoria"), "OTROS"),
            "op":          m.get("op") or m.get("categoria"),
            "ticker":      m.get("ticker"),
            "importe":     m.get("importe"),
            "moneda":      m.get("moneda"),
            "importe_ars": round(importe_ars, 2),
            "incluido":    comp not in excl,
        })
    return {
        "id_cuenta":   str(id_cuenta),
        "columnas":    list(COLUMNAS),
        "movimientos": movimientos,
        "n":           len(movimientos),
    }


def set_incluido(id_cuenta: str, comprobante, incluido: bool, actor: str) -> dict:
    """Incluye/excluye un comprobante para la cuenta. Guarda SOLO las exclusiones
    (default = incluido). Idempotente; upsert por id_cuenta."""
    col = get_mongo_client()[_SEL_DB][_SEL_COL]
    cambio = ({"$pull": {"excluidos": comprobante}} if incluido
              else {"$addToSet": {"excluidos": comprobante}})
    cambio["$set"] = {"actualizado_por": actor, "actualizado_at": datetime.now(UTC)}
    col.update_one({"id_cuenta": str(id_cuenta)}, cambio, upsert=True)
    return {"id_cuenta": str(id_cuenta), "comprobante": comprobante, "incluido": incluido}
