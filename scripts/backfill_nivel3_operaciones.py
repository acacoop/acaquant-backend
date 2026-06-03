"""scripts/backfill_nivel3_operaciones.py — rellena nivel_3 / segmento en Operaciones.

Diag (scripts/diag_nivel3) confirmó: ~403k docs (90% de los vacíos, 853 cuentas)
son CAUSA 2 — el Comitente SÍ tiene nivel_3 pero el doc de Operaciones no se
re-enriquerió. Este backfill los llena, server-side y por cuenta indexada (NO
itera un cursor sobre las 487k como enriquecer() → sin riesgo de CursorNotFound,
y sin re-crear el índice moneda_concertacion que ya dropeamos).

Join: `Operaciones.cuenta` (id pelado) == `Clientes.Comitentes.id_cuenta` →
`nivel_1` (segmento) / `nivel_3`. Solo toca cuentas cuyo Comitente tiene nivel
cargado, y solo los docs con nivel_3/segmento vacío (idempotente). Las cuentas SIN
Comitente (propias/FCI) quedan vacías a propósito.

Uso:
    python -m scripts.backfill_nivel3_operaciones --dry-run
    python -m scripts.backfill_nivel3_operaciones
"""
from __future__ import annotations

import argparse

from pymongo import UpdateMany

from core.mongo import get_mongo_client

_VACIO = {"$in": [None, ""]}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="reporta sin escribir")
    args = ap.parse_args()

    client = get_mongo_client()
    ops = client["CashFlow"]["Operaciones"]
    comit = client["Clientes"]["Comitentes"]

    # Comitentes con algún nivel cargado → {id_cuenta: (n1, n3)}.
    niveles = {}
    for d in comit.find({}, {"_id": 0, "id_cuenta": 1, "nivel_1": 1, "nivel_3": 1}):
        idc = str(d.get("id_cuenta") or "").strip()
        n1 = (d.get("nivel_1") or "").strip()
        n3 = (d.get("nivel_3") or "").strip()
        if idc and (n1 or n3):
            niveles[idc] = (n1, n3)

    ids = list(niveles)
    # Docs candidatos: de esas cuentas, con nivel_3 o segmento vacío.
    candidatos = ops.count_documents(
        {"cuenta": {"$in": ids}, "$or": [{"nivel_3": _VACIO}, {"segmento": _VACIO}]}
    )
    print(f"Comitentes con nivel: {len(ids):,} | docs candidatos (nivel_3/segmento "
          f"vacío en esas cuentas): {candidatos:,}")

    if args.dry_run:
        print("(dry-run: no se escribió nada)")
        return

    # Una UpdateMany por cuenta (match por `cuenta` → índice cuenta_concertacion).
    bulk = [
        UpdateMany(
            {"cuenta": idc, "$or": [{"nivel_3": _VACIO}, {"segmento": _VACIO}]},
            {"$set": {"segmento": n1, "nivel_3": n3}},
        )
        for idc, (n1, n3) in niveles.items()
    ]
    res = ops.bulk_write(bulk, ordered=False)
    print(f"✅ matched={res.matched_count:,} modified={res.modified_count:,} "
          f"({len(bulk):,} cuentas procesadas)")
    restantes = ops.count_documents({"nivel_3": _VACIO})
    print(f"   docs con nivel_3 aún vacío: {restantes:,} "
          f"(esperado: SIN COMITENTE — propias/FCI/CUITs)")


if __name__ == "__main__":
    main()
