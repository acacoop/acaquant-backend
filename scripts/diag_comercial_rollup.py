"""scripts/diag_comercial_rollup.py — MEDIR antes de diseñar ComercialCache (C1/C2).

REGLA #2: no diseñar el grano del rollup por corazonada. Este diag read-only mide
lo único que decide el diseño de `Clientes.ComercialCache`:

  1. Cuánto tardan REALMENTE los dos killers (cache fría):
       - informe_comercial()  → C1 (escanea NegocioMovimientos ~337k sin fecha/cuenta)
       - serie_comercial(TODOS) → C2 (group por fecha de toda la colección)
  2. explain() de los $match calientes → confirma COLLSCAN vs IXSCAN.
  3. Cardinalidad del grano candidato {fecha × id_cuenta}: cuántas filas tendría
     el rollup. Si son ~miles, el ComercialCache lee eso en vez de escanear 337k.
     - VOLUMEN: NegocioMovimientos agrupado por {fecha, id_cuenta}.
     - ARANCEL: Operaciones agrupado por {concertacion, cuenta} (arancel>0).
  4. Índices existentes en ambas colecciones.

Read-only (get_mongo_client_read). Correr en el Droplet:
    python -m scripts.diag_comercial_rollup
"""
from __future__ import annotations

import time

from api.services._negocio_futuros import match_no_futuros
from api.services.comercial import (
    _CATS_VOLUMEN,
    TODOS,
    informe_comercial,
    serie_comercial,
)
from core.mongo import get_mongo_client_read


def _timed(label, fn, **k):
    t0 = time.perf_counter()
    out = fn(**k)
    ms = (time.perf_counter() - t0) * 1000
    print(f"  {ms:9.1f} ms  {label}")
    return out


def _stage(plan: dict) -> str:
    """Cadena de stages del winningPlan. COLLSCAN = sin índice."""
    out = []
    cur = plan
    while cur:
        st = cur.get("stage")
        if st == "COLLSCAN":
            return "⚠️  COLLSCAN (sin índice)"
        if st:
            out.append(st + (f"[{cur['indexName']}]" if cur.get("indexName") else ""))
        cur = cur.get("inputStage")
    return " → ".join(out) or "?"


def _explain_match(coll, match: dict) -> str:
    """explain() del $match (proxy con find() — el index que elige para el filtro
    es el mismo que usaría el primer stage del aggregate)."""
    plan = coll.find(match).explain().get("queryPlanner", {}).get("winningPlan", {})
    return _stage(plan)


def _combo_count(coll, match: dict, dims: dict) -> int:
    """# filas si agrupás por `dims` (cardinalidad del grano del rollup)."""
    rows = list(coll.aggregate(
        [{"$match": match}, {"$group": {"_id": dims}}, {"$count": "n"}],
        allowDiskUse=True,
    ))
    return rows[0]["n"] if rows else 0


def main() -> None:
    db = get_mongo_client_read()
    nm = db["CashFlow"]["NegocioMovimientos"]
    ops = db["CashFlow"]["Operaciones"]
    cats = list(_CATS_VOLUMEN)

    # Los $match exactos que corren hoy (copiados del service).
    m_informe = {**match_no_futuros(),
                 "$or": [{"categoria": {"$in": cats}}, {"arancel": {"$gt": 0}}]}
    m_serie_todos = {"categoria": {"$in": cats}, **match_no_futuros()}
    m_arancel = {"arancel": {"$gt": 0}, "es_cierre": False, "etapa": {"$ne": "solicitud"}}

    print("══ 1. Latencias (cache fría, primer hit del proceso) ══")
    _timed("informe_comercial()        [C1]", informe_comercial, moneda="ARS")
    _timed("serie_comercial(TODOS,vol) [C2]", serie_comercial, operador=TODOS, metric="volumen")

    print("\n══ 2. explain() de los $match (COLLSCAN = el problema) ══")
    print(f"  C1 informe  (NegocioMov $or): {_explain_match(nm, m_informe)}")
    print(f"  C2 serie    (NegocioMov cat): {_explain_match(nm, m_serie_todos)}")
    print(f"  arancel     (Operaciones)   : {_explain_match(ops, m_arancel)}")

    print("\n══ 3. Tamaños y cardinalidad del grano candidato {fecha × cuenta} ══")
    tot_nm = nm.estimated_document_count()
    tot_ops = ops.estimated_document_count()
    print(f"  NegocioMovimientos total docs : {tot_nm:>10,}")
    print(f"  Operaciones        total docs : {tot_ops:>10,}")

    nm_vol_match = {"categoria": {"$in": cats}, **match_no_futuros()}
    n_cuentas = len(nm.distinct("id_cuenta", nm_vol_match))
    n_fechas = len(nm.distinct("fecha", nm_vol_match))
    combo_vol = _combo_count(nm, nm_vol_match, {"f": "$fecha", "c": "$id_cuenta"})
    print("\n  VOLUMEN (NegocioMov, categorías de volumen):")
    print(f"    distinct id_cuenta          : {n_cuentas:>10,}")
    print(f"    distinct fecha              : {n_fechas:>10,}")
    print(f"    filas rollup {{fecha,cuenta}} : {combo_vol:>10,}  ← tamaño ComercialCache (volumen)")

    n_ops_cuentas = len(ops.distinct("cuenta", m_arancel))
    combo_ar = _combo_count(ops, m_arancel, {"f": "$concertacion", "c": "$cuenta"})
    print("\n  ARANCEL (Operaciones, arancel>0):")
    print(f"    distinct cuenta             : {n_ops_cuentas:>10,}")
    print(f"    filas rollup {{conc,cuenta}}  : {combo_ar:>10,}  ← tamaño ComercialCache (arancel)")

    print("\n══ 4. Índices existentes ══")
    for nombre, coll in (("NegocioMovimientos", nm), ("Operaciones", ops)):
        print(f"  {nombre}:")
        for ix, info in coll.index_information().items():
            print(f"    {ix:32} {info.get('key')}")


if __name__ == "__main__":
    main()
