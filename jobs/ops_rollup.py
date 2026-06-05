"""jobs/ops_rollup.py — precompute CashFlow.OpsSerieDiaria (rollup de las series).

Las series de /ops/serie y /ops/aranceles re-agregaban TODA la historia de
Operaciones (~200k docs por request, SIN filtro de fecha) → escaneaban los 213MB
de la colección y desalojaban el cache del M10 (2 vCPU/~512MB cache) → CPU.

Este job pre-agrega por día × dimensiones de BAJA cardinalidad. La serie pasa a
leer unos miles de filas (no escanear 487k). Grano (1 doc por combo, sobre el
subconjunto "countable" = NO cierre, NO solicitud):
    {fecha, moneda, mercado, operacion, segmento, nivel_3} → {bruto, arancel, n}
NO incluye denominacion/cuenta (alta cardinalidad → esos filtros y el scope caen
a live en el endpoint, que ya usan índice). El día de HOY se agrega en vivo en el
endpoint (live-fallback); el rollup cubre hasta ayer.

Default: recalcula los últimos _LOOKBACK_DIAS (boletos que llegan tarde / retro).
--full: rebuild completo (swap atómico, sin ventana de vacío).

Uso:
    python -m jobs.ops_rollup            # incremental (últimos N días)
    python -m jobs.ops_rollup --full     # rebuild completo (1ra vez / correcciones)
"""
from __future__ import annotations

import argparse
from datetime import UTC, datetime, timedelta

from pymongo import ASCENDING

from core.job_runs import JobRunLogger
from core.mongo import get_mongo_client, reemplazar_coleccion_atomico

_COL = "OpsSerieDiaria"
_LOOKBACK_DIAS = 7
_DIMS = ("moneda", "mercado", "operacion", "segmento", "nivel_3")


def _ensure_indexes(col) -> None:
    col.create_index([("moneda", ASCENDING), ("fecha", ASCENDING)], name="moneda_fecha")
    col.create_index([("fecha", ASCENDING)], name="fecha")


def _agregar(ops, desde: str | None) -> list[dict]:
    """Agrega Operaciones (subconjunto countable) al grano del rollup. Si `desde`,
    solo desde esa fecha de concertación."""
    # Incluimos los CIERRES con arancel (cauciones: el arancel vive SOLO en el
    # cierre — ver diag_aranceles_caucion). Su `bruto` NO cuenta como volumen
    # (repetiría el nocional) → se fuerza a 0; sólo aporta arancel. Los cierres
    # SIN arancel (no-caución) quedan afuera del $or.
    match: dict = {"etapa": {"$ne": "solicitud"},
                   "$or": [{"es_cierre": False}, {"es_cierre": True, "arancel": {"$ne": 0}}]}
    if desde:
        match["concertacion"] = {"$gte": desde}
    grupo: dict = {d: f"${d}" for d in _DIMS}
    grupo["fecha"] = "$concertacion"
    rows = ops.aggregate([
        {"$match": match},
        {"$group": {
            "_id": grupo,
            # bruto del cierre = 0 (no es volumen nuevo); el resto suma normal.
            "bruto": {"$sum": {"$cond": [{"$eq": ["$es_cierre", True]},
                                         0, {"$ifNull": ["$bruto", 0]}]}},
            # arancel en valor ABSOLUTO: la serie de /ops/aranceles usa $abs
            # (los aranceles negativos —reintegros— se cuentan en magnitud).
            "arancel": {"$sum": {"$abs": {"$ifNull": ["$arancel", 0]}}},
            "n": {"$sum": 1},
        }},
    ], allowDiskUse=True)
    return [{**r["_id"], "bruto": round(r["bruto"], 2),
             "arancel": round(r["arancel"], 2), "n": r["n"]}
            for r in rows if r["_id"].get("fecha")]


def run(full: bool = False) -> dict:
    with JobRunLogger("ops_rollup") as jr:
        db = get_mongo_client()["CashFlow"]
        ops = db["Operaciones"]
        col = db[_COL]
        if full:
            docs = _agregar(ops, None)
            reemplazar_coleccion_atomico(db, _COL, docs)  # swap atómico
            _ensure_indexes(col)  # la colección renombrada no trae índices 2rios
            modo = "FULL"
        else:
            hoy = (datetime.now(UTC) - timedelta(hours=3)).date()  # ART
            cutoff = (hoy - timedelta(days=_LOOKBACK_DIAS)).isoformat()
            docs = _agregar(ops, cutoff)
            _ensure_indexes(col)
            col.delete_many({"fecha": {"$gte": cutoff}})
            if docs:
                col.insert_many(docs)
            modo = f"incremental (desde {cutoff})"
        jr.set_stat("docs_rollup", len(docs))
        jr.log(f"OpsSerieDiaria: {len(docs)} filas · {modo}")
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
