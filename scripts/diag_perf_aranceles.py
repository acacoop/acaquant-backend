"""Diag READ-ONLY de performance del endpoint /ops/aranceles.

Mide con DATOS (no corazonadas — REGLA #2) si el primer load de la serie de
ARANCELES es realmente lento y por qué:
  1. Wall-clock del pipeline $facet COMPLETO tal cual lo corre el endpoint
     (modo ULTIMA: desde=hasta=último día), varias corridas, para ARS y USD.
  2. explain(executionStats) del sub-pipeline de la SERIE (match+group) →
     muestra si es COLLSCAN o IXSCAN, docs/keys examinados y ms del servidor.
  3. Proporción REAL en CashFlow.Operaciones: total vs futuros (regex) vs
     futuros agro (SOJA/TRIGO/MAIZ) — para verificar con número la afirmación
     "los futuros son una minoría" (que se dijo SIN medir).

NO escribe nada. Usa el cliente de SOLO LECTURA.

Uso (desde la raíz del repo, en el Droplet):
    python -m scripts.diag_perf_aranceles
    python -m scripts.diag_perf_aranceles --runs 5
"""
from __future__ import annotations

import argparse
import time

from core.mongo import get_mongo_client_read

_CIERRE = {"$regex": "Cierre", "$options": "i"}
_FUTUROS = {"$regex": "Futuros", "$options": "i"}


def _serie_pipeline(moneda: str, plen: int, segmento: str | None,
                    desde: str | None = None) -> list[dict]:
    """Sub-pipeline de la SERIE, idéntico al de ops_aranceles (router). Si `desde`
    se pasa, acota la serie a concertacion >= desde (opción 'ventana acotada')."""
    match: dict = {"moneda": moneda, "tipo_operacion": {"$not": _CIERRE}}
    if segmento:
        match["segmento"] = segmento
    if desde:
        match["concertacion"] = {"$gte": desde}
    arancel = {"$abs": {"$ifNull": ["$arancel", 0]}}
    return [
        {"$match": match},
        {"$group": {"_id": {"$substr": ["$concertacion", 0, plen]}, "ar": {"$sum": arancel}}},
        {"$sort": {"_id": 1}},
        {"$project": {"_id": 0, "periodo": "$_id", "arancel": {"$round": ["$ar", 2]}}},
    ]


def _facet_pipeline(moneda: str, plen: int, desde: str, hasta: str, segmento: str | None) -> list[dict]:
    """Pipeline $facet COMPLETO, idéntico al de ops_aranceles (serie + tablas)."""
    match: dict = {"moneda": moneda, "tipo_operacion": {"$not": _CIERRE}}
    if segmento:
        match["segmento"] = segmento
    arancel = {"$abs": {"$ifNull": ["$arancel", 0]}}
    date_m = {"$match": {"concertacion": {"$gte": desde, "$lte": hasta}}}
    return [
        {"$match": match},
        {"$facet": {
            "serie": [
                {"$group": {"_id": {"$substr": ["$concertacion", 0, plen]}, "ar": {"$sum": arancel}}},
                {"$sort": {"_id": 1}},
                {"$project": {"_id": 0, "periodo": "$_id", "arancel": {"$round": ["$ar", 2]}}},
            ],
            "por_nivel3": [
                date_m,
                {"$group": {"_id": "$nivel_3", "ar": {"$sum": arancel}, "n": {"$sum": 1}}},
                {"$match": {"ar": {"$gt": 0}}}, {"$sort": {"ar": -1}},
            ],
            "por_cuenta": [
                date_m,
                {"$group": {"_id": "$denominacion", "ar": {"$sum": arancel}, "n": {"$sum": 1}}},
                {"$match": {"ar": {"$gt": 0}}}, {"$sort": {"ar": -1}},
            ],
        }},
    ]


def _find_key(obj, key):
    """Primer valor de `key` en cualquier nivel del árbol (dict/list). La forma
    del explain de un aggregate varía por versión/topología → buscar recursivo
    es robusto en vez de asumir dónde viven executionStats/winningPlan."""
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        for v in obj.values():
            r = _find_key(v, key)
            if r is not None:
                return r
    elif isinstance(obj, list):
        for v in obj:
            r = _find_key(v, key)
            if r is not None:
                return r
    return None


def _time_aggregate(coll, pipeline: list[dict], runs: int) -> list[float]:
    """Corre el aggregate `runs` veces y devuelve los ms de pared de cada corrida."""
    out = []
    for _ in range(runs):
        t0 = time.perf_counter()
        list(coll.aggregate(pipeline))
        out.append((time.perf_counter() - t0) * 1000)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--runs", type=int, default=3)
    args = ap.parse_args()

    coll = get_mongo_client_read()["CashFlow"]["Operaciones"]

    # Rango como lo abre el front (modo ULTIMA): desde=hasta=último día.
    ult = list(coll.aggregate([{"$group": {"_id": "$concertacion"}}, {"$sort": {"_id": -1}}, {"$limit": 1}]))
    hasta = ult[0]["_id"] if ult else ""
    desde = hasta
    plen = 7  # MENSUAL (default del gráfico)
    print(f"Último día (concertacion): {hasta} | runs={args.runs} | agg=MENSUAL")

    # ── 1) Proporciones REALES (verificar la afirmación sin medir) ──
    total = coll.count_documents({})
    n_fut = coll.count_documents({"tipo_operacion": _FUTUROS})
    n_agro = coll.count_documents({"$and": [
        {"tipo_operacion": _FUTUROS},
        {"tipo_operacion": {"$not": {"$regex": "Financieros", "$options": "i"}}},
        {"denominacion": {"$not": {"$regex": "OTC", "$options": "i"}}},
        {"instrumento": {"$not": {"$regex": "OTC", "$options": "i"}}},
        {"$or": [{"instrumento": {"$regex": "SOJ|TRI|MAI", "$options": "i"}}]},
    ]})
    def pct(n: int) -> str:
        return f"{100 * n / total:.1f}%" if total else "—"
    print("\n── Proporciones en CashFlow.Operaciones ──")
    print(f"  total docs            {total}")
    print(f"  futuros (regex)       {n_fut}  ({pct(n_fut)})")
    print(f"  futuros agro          {n_agro}  ({pct(n_agro)})")

    # ── 2) Timing del $facet completo (lo que siente el usuario) ──
    print("\n── Wall-clock del $facet completo (primer load real) ──")
    for moneda in ("ARS", "USD"):
        ms = _time_aggregate(coll, _facet_pipeline(moneda, plen, desde, hasta, None), args.runs)
        print(f"  {moneda}: " + " | ".join(f"{m:.0f}ms" for m in ms) + f"  (min {min(ms):.0f}ms)")

    # ── 3) explain() de la serie sola (plan + docs examinados) ──
    print("\n── explain(executionStats) de la SERIE (match+group) ──")
    db = coll.database
    for moneda in ("ARS", "USD"):
        serie = _serie_pipeline(moneda, plen, None)
        ex = db.command("explain", {"aggregate": "Operaciones", "pipeline": serie, "cursor": {}},
                        verbosity="executionStats")
        win = _find_key(ex, "winningPlan") or {}
        docs_ex = _find_key(ex, "totalDocsExamined")
        keys_ex = _find_key(ex, "totalKeysExamined")
        n_ret = _find_key(ex, "nReturned")
        ms = _find_key(ex, "executionTimeMillis")
        idx = _find_key(win, "indexName")
        plan = str(win)
        kind = "COLLSCAN" if "COLLSCAN" in plan else ("IXSCAN" if "IXSCAN" in plan else "?")
        print(f"  {moneda}: plan={kind}{f' ({idx})' if idx else ''} "
              f"| docsExaminados={docs_ex} | keysExaminadas={keys_ex} "
              f"| nReturned={n_ret} | server={ms}ms")
        if docs_ex is None:
            import json
            print("   [estructura cruda del explain, top keys]:", list(ex.keys()))
            print("   " + json.dumps(ex, default=str)[:1500])

    # ── 4) Potencial de ACOTAR LA VENTANA (opción C, sin infra) ──
    # Mide cuánto baja el wall-clock de la serie si solo se agregan los últimos
    # N meses (en vez de toda la historia). Así decidimos ventana vs rollup CON dato.
    print("\n── Serie acotada a últimos N meses (potencial opción 'ventana') ──")

    def _cutoff(hasta_iso: str, days: int) -> str:
        from datetime import date, timedelta
        y, m, d = (int(x) for x in hasta_iso.split("-"))
        return (date(y, m, d) - timedelta(days=days)).isoformat()

    for moneda in ("ARS", "USD"):
        full = _time_aggregate(coll, _serie_pipeline(moneda, plen, None), args.runs)
        line = f"  {moneda}: historia completa min {min(full):.0f}ms"
        for label, days in (("12m", 365), ("18m", 545)):
            cut = _cutoff(hasta, days)
            w = _time_aggregate(coll, _serie_pipeline(moneda, plen, None, desde=cut), args.runs)
            line += f" | {label} min {min(w):.0f}ms"
        print(line)

    print("\n(read-only: no se escribió nada)")


if __name__ == "__main__":
    main()
