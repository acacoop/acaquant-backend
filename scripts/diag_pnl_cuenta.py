"""diag_pnl_cuenta.py — ¿conviene cambiar el regex de pnl_por_cuenta por id_cuenta?

pnl_por_cuenta filtra boletos con `cuenta: {$regex: '^\\[ID\\]'}` (línea 321 de
api/services/pnl.py) → 99% Mongo, ~1.1s, casi todo en escanear NegocioMovimientos.
El módulo comercial ya denormalizó `id_cuenta` (indexado) para no usar regex.

Antes de cambiar el filtro (PNL = cost-basis, sensible), este diag verifica:
  1. Índices de NegocioMovimientos (¿hay uno en id_cuenta?).
  2. COBERTURA: ¿cuántos docs NO tienen id_cuenta? (si hay, el filtro nuevo
     perdería boletos → NO aplicar sin backfill).
  3. EQUIVALENCIA: para la cuenta, el set de boletos del regex == el de id_cuenta?
  4. EXPLAIN de ambas queries: COLLSCAN vs IXSCAN, docs examinados, ms.

    python -m scripts.diag_pnl_cuenta            # cuenta 10
    python -m scripts.diag_pnl_cuenta --id 123
"""
from __future__ import annotations

import argparse
import re

from core.mongo import get_mongo_client_read

# Mismas categorías que usa pnl._pnl_por_cuenta_core (no las importamos para no
# arrastrar el módulo; si cambian allá, actualizar acá — es solo diagnóstico).
_PROJ = {"_id": 0, "comprobante": 1}


def _stats(cursor) -> dict:
    """Extrae stage + docs examinados + nReturned + ms de un cursor.explain()."""
    exp = cursor.explain()
    qp = exp.get("queryPlanner", {}).get("winningPlan", {})
    ex = exp.get("executionStats", {})
    names: list[str] = []

    def walk(p):
        if not isinstance(p, dict):
            return
        if "stage" in p:
            names.append(p["stage"])
        for k in ("inputStage", "inputStages"):
            v = p.get(k)
            if isinstance(v, list):
                for s in v:
                    walk(s)
            elif v:
                walk(v)

    walk(qp)
    return {
        "stages": " → ".join(names) or "?",
        "docs_examinados": ex.get("totalDocsExamined", "?"),
        "keys_examinadas": ex.get("totalKeysExamined", "?"),
        "nReturned": ex.get("nReturned", "?"),
        "ms": ex.get("executionTimeMillis", "?"),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--id", default="10", help="id_cuenta a diagnosticar")
    args = ap.parse_args()
    idc = str(args.id)

    cli = get_mongo_client_read()
    col = cli["CashFlow"]["NegocioMovimientos"]

    f_regex = {"cuenta": {"$regex": f"^\\[{re.escape(idc)}\\]"}}
    f_idc = {"id_cuenta": idc}

    print("=" * 72)
    print("ÍNDICES de CashFlow.NegocioMovimientos")
    print("=" * 72)
    tiene_idx_idc = False
    for name, spec in col.index_information().items():
        keys = spec.get("key")
        print(f"  {name}: {keys}")
        if any(k[0] == "id_cuenta" for k in keys):
            tiene_idx_idc = True
    print(f"  → índice en id_cuenta: {'SÍ' if tiene_idx_idc else 'NO (habría que crearlo)'}")

    print("\n" + "=" * 72)
    print("COBERTURA de id_cuenta (toda la colección)")
    print("=" * 72)
    total = col.estimated_document_count()
    sin_idc = col.count_documents({"$or": [{"id_cuenta": {"$exists": False}}, {"id_cuenta": None}]})
    print(f"  total docs:           {total:,}")
    print(f"  SIN id_cuenta:        {sin_idc:,}")
    if sin_idc:
        print("  ⚠️  HAY docs sin id_cuenta → cambiar el filtro perdería boletos.")
        print("     Correr backfill (scripts/backfill_id_cuenta_negocio.py) ANTES de aplicar.")
    else:
        print("  ✅ todos los docs tienen id_cuenta.")

    print("\n" + "=" * 72)
    print(f"EQUIVALENCIA para cuenta {idc} (regex vs id_cuenta)")
    print("=" * 72)
    set_regex = {d["comprobante"] for d in col.find(f_regex, _PROJ) if d.get("comprobante") is not None}
    set_idc = {d["comprobante"] for d in col.find(f_idc, _PROJ) if d.get("comprobante") is not None}
    print(f"  boletos por regex:     {len(set_regex):,}")
    print(f"  boletos por id_cuenta: {len(set_idc):,}")
    solo_regex = set_regex - set_idc
    solo_idc = set_idc - set_regex
    if not solo_regex and not solo_idc:
        print("  ✅ IDÉNTICO — mismos comprobantes. El filtro id_cuenta es equivalente.")
    else:
        print(f"  ⚠️  DIFIEREN: solo_regex={len(solo_regex)}  solo_idcuenta={len(solo_idc)}")
        if solo_regex:
            print(f"     ejemplos solo en regex: {sorted(solo_regex)[:5]}")

    print("\n" + "=" * 72)
    print("EXPLAIN — query ACTUAL (regex sobre cuenta)")
    print("=" * 72)
    st = _stats(col.find(f_regex, _PROJ).sort([("fecha", 1), ("comprobante", 1)]))
    for k, v in st.items():
        print(f"  {k}: {v}")

    print("\n" + "=" * 72)
    print("EXPLAIN — query PROPUESTA (id_cuenta exacto)")
    print("=" * 72)
    st2 = _stats(col.find(f_idc, _PROJ).sort([("fecha", 1), ("comprobante", 1)]))
    for k, v in st2.items():
        print(f"  {k}: {v}")

    print("\nLectura: si la actual es COLLSCAN con docs_examinados >> nReturned y")
    print("la propuesta es IXSCAN con docs_examinados ≈ nReturned, el cambio es el fix.")


if __name__ == "__main__":
    main()
