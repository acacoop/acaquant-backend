"""jobs/comercial_rollup.py — precompute Clientes.ComercialCache (rollup comercial).

Las vistas comerciales re-agregaban TODA la historia sin filtro de fecha ni cuenta
→ COLLSCAN de las ~338k de NegocioMovimientos en cada apertura del INFORME (2.4s
medido en diag_comercial_rollup) → desalojaba el cache del M10. Causas C1/C2 de
docs/AUDITORIA_DATOS_2026-06.md.

Este job pre-agrega al grano {fecha, id_cuenta}, COMBINANDO las dos fuentes que el
informe usa hoy (mismas categorías / mismo $match → equivalente por construcción):
    - VOLUMEN: CashFlow.NegocioMovimientos — categorías de volumen, pesificado ARS
      (_PESIF: ARS=|importe|, USD=|importe|×mep del boleto). Campo de fecha: `fecha`.
    - ARANCEL: CashFlow.Operaciones — arancel>0, etapa≠solicitud (incl. cierre de caución).
      Join cuenta==id_cuenta. Campo de fecha: `concertacion` (→ se guarda como `fecha`).
Grano (1 doc por combo): {fecha, id_cuenta} → {vol, n_ops, arancel}. El informe y la
serie pasan a leer ~40k filas indexadas en vez de escanear 338k+488k. La dolarización
(÷MEP en vista USD) la sigue haciendo el service al cierre — el rollup guarda ARS.

Default: recalcula los últimos _LOOKBACK_DIAS (boletos que llegan tarde / retro).
--full: rebuild completo (swap atómico, sin ventana de vacío).

Uso:
    python -m jobs.comercial_rollup            # incremental (últimos N días)
    python -m jobs.comercial_rollup --full     # rebuild completo (1ra vez / correcciones)
"""
from __future__ import annotations

import argparse
from datetime import UTC, datetime, timedelta

from pymongo import ASCENDING

from api.services._negocio_futuros import match_no_futuros
from api.services.comercial import _CATS_VOLUMEN, _PESIF
from core.job_runs import JobRunLogger
from core.mongo import get_mongo_client, reemplazar_coleccion_atomico

_COL = "ComercialCache"
_LOOKBACK_DIAS = 7


def _ensure_indexes(col) -> None:
    col.create_index([("fecha", ASCENDING)], name="fecha")
    col.create_index([("id_cuenta", ASCENDING), ("fecha", ASCENDING)], name="idcuenta_fecha")


def _agg_volumen(nm, desde: str | None):
    """{fecha, id_cuenta} → vol (Σ pesificado), n_ops. Mismo match que informe_comercial."""
    match: dict = {"categoria": {"$in": list(_CATS_VOLUMEN)}, **match_no_futuros()}
    if desde:
        match["fecha"] = {"$gte": desde}
    return nm.aggregate([
        {"$match": match},
        {"$group": {
            "_id": {"f": "$fecha", "c": "$id_cuenta"},
            "vol": {"$sum": _PESIF},
            "n_ops": {"$sum": 1},
        }},
    ], allowDiskUse=True)


def _agg_arancel(ops, desde: str | None):
    """{concertacion, cuenta} → arancel (Σ). Mismo match que _aranceles_por_cuenta.
    SIN es_cierre: el arancel de caución vive en el cierre (arancel>0 ya descarta
    los cierres no-caución)."""
    match: dict = {"arancel": {"$gt": 0}, "etapa": {"$ne": "solicitud"}}
    if desde:
        match["concertacion"] = {"$gte": desde}
    return ops.aggregate([
        {"$match": match},
        {"$group": {
            "_id": {"f": "$concertacion", "c": "$cuenta"},
            "arancel": {"$sum": "$arancel"},
        }},
    ], allowDiskUse=True)


def _construir(nm, ops, desde: str | None) -> list[dict]:
    """Merge vol (NegocioMov) + arancel (Operaciones) al grano {fecha, id_cuenta}.
    Una fila puede tener solo volumen, solo arancel, o ambos."""
    acc: dict[tuple[str, str], dict] = {}
    for r in _agg_volumen(nm, desde):
        f, c = r["_id"].get("f"), r["_id"].get("c")
        if not f or c in (None, ""):
            continue
        d = acc.setdefault((str(f), str(c)), {"vol": 0.0, "n_ops": 0, "arancel": 0.0})
        d["vol"] = round(float(r.get("vol") or 0.0), 2)
        d["n_ops"] = int(r.get("n_ops") or 0)
    for r in _agg_arancel(ops, desde):
        f, c = r["_id"].get("f"), r["_id"].get("c")
        if not f or c in (None, ""):
            continue
        d = acc.setdefault((str(f), str(c)), {"vol": 0.0, "n_ops": 0, "arancel": 0.0})
        d["arancel"] = round(float(r.get("arancel") or 0.0), 2)
    return [{"fecha": f, "id_cuenta": c, **v} for (f, c), v in acc.items()]


def run(full: bool = False) -> dict:
    with JobRunLogger("comercial_rollup") as jr:
        db = get_mongo_client()["Clientes"]
        cf = get_mongo_client()["CashFlow"]
        nm, ops, col = cf["NegocioMovimientos"], cf["Operaciones"], db[_COL]
        if full:
            docs = _construir(nm, ops, None)
            reemplazar_coleccion_atomico(db, _COL, docs)  # swap atómico
            _ensure_indexes(col)  # la colección renombrada no trae índices 2rios
            modo = "FULL"
        else:
            hoy = (datetime.now(UTC) - timedelta(hours=3)).date()  # ART
            cutoff = (hoy - timedelta(days=_LOOKBACK_DIAS)).isoformat()
            docs = _construir(nm, ops, cutoff)
            _ensure_indexes(col)
            col.delete_many({"fecha": {"$gte": cutoff}})
            if docs:
                col.insert_many(docs)
            modo = f"incremental (desde {cutoff})"
        jr.set_stat("docs_rollup", len(docs))
        jr.log(f"ComercialCache: {len(docs)} filas · {modo}")
        return {"docs": len(docs), "modo": modo}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--full", action="store_true", help="rebuild completo")
    args = ap.parse_args()
    res = run(full=args.full)
    print(f"→ {res}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
