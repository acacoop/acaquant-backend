"""diag_compara_contrapartes.py — compara la vista Contrapartes VIEJA vs NUEVA.

Read-only. NO borra ni modifica nada. Sirve para validar, ANTES de migrar, que
reconstruir la vista desde NegocioMovimientos (joineado por id_cuenta) captura al
menos lo mismo que el feed viejo — por CANTIDAD de movimientos por contraparte.

  VIEJA : OperacionesAPI.MesaAPI agrupado por `contraparte`  (feed flujo_contrapartes:
          solo trades, solo del día en adelante).
  NUEVA : CashFlow.NegocioMovimientos cuyo `id_cuenta` ∈ las cuentas de la
          contraparte (CashFlow.Contrapartes.cuenta), excluyendo futuros (USDL).
          Trae TODO (depósitos incluidos) y con historia.

Es esperable que la NUEVA traiga MÁS (depósitos/extracciones + histórico). Lo que
queremos ver es que no traiga MENOS para los fondos que ya estaban.

Uso (desde la raíz del repo en el Droplet):
    venv/bin/python -m scripts.diag_compara_contrapartes
    venv/bin/python -m scripts.diag_compara_contrapartes --cat     # + desglose categoría
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict

from core.mongo import get_mongo_client_read


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cat", action="store_true",
                    help="muestra el desglose por categoría del feed nuevo")
    ap.add_argument("--cp", default=None,
                    help="filtrar a estas contrapartes (coma-separado, ej. DRACMA,IEB)")
    args = ap.parse_args()
    cp_filtro = [t.strip().lower() for t in (args.cp or "").split(",") if t.strip()]

    c = get_mongo_client_read()
    contrapartes = c["CashFlow"]["Contrapartes"]
    mesa = c["OperacionesAPI"]["MesaAPI"]
    nego = c["CashFlow"]["NegocioMovimientos"]

    # contraparte (nombre) → set de id_cuenta (sus fondos); y el inverso.
    cp_to_ids: dict[str, set[str]] = defaultdict(set)
    id_to_cp: dict[str, str] = {}
    id_to_denom: dict[str, str] = {}
    for d in contrapartes.find({}, {"_id": 0, "contraparte": 1, "cuenta": 1, "denominacion": 1}):
        cp = (d.get("contraparte") or "").strip()
        idc = str(d.get("cuenta") or "").strip()
        if cp and idc:
            cp_to_ids[cp].add(idc)
            id_to_cp[idc] = cp
            id_to_denom[idc] = d.get("denominacion") or ""

    # VIEJA: MesaAPI por contraparte.
    old_counts: Counter[str] = Counter()
    for d in mesa.find({}, {"_id": 0, "contraparte": 1}):
        cp = (d.get("contraparte") or "").strip()
        if cp:
            old_counts[cp] += 1

    # NUEVA: NegocioMovimientos por id_cuenta (excluye futuros USDL).
    all_ids = list(id_to_cp.keys())
    new_by_id: Counter[str] = Counter()
    cat_by_cp: dict[str, Counter[str]] = defaultdict(Counter)
    for d in nego.find(
        {"id_cuenta": {"$in": all_ids}, "unidad": {"$nin": ["USDL"]}},
        {"_id": 0, "id_cuenta": 1, "categoria": 1},
    ):
        idc = str(d.get("id_cuenta") or "")
        new_by_id[idc] += 1
        cp = id_to_cp.get(idc)
        if cp:
            cat_by_cp[cp][d.get("categoria") or "(sin categoria)"] += 1

    rows = []
    for cp, ids in cp_to_ids.items():
        if cp_filtro and not any(t in cp.lower() for t in cp_filtro):
            continue
        old_n = old_counts.get(cp, 0)
        new_n = sum(new_by_id[i] for i in ids)
        rows.append((cp, sorted(ids), old_n, new_n))
    rows.sort(key=lambda r: -r[3])

    print("=" * 96)
    print(f"COMPARA Contrapartes — VIEJA (MesaAPI) vs NUEVA (NegocioMovimientos por id_cuenta)")
    print(f"{len(rows)} contrapartes con cuenta asignada")
    print("=" * 96)
    print(f"{'CONTRAPARTE':<26} {'IDs (fondos)':<22} {'VIEJA':>8} {'NUEVA':>8}  {'Δ':>8}")
    print("-" * 96)
    tot_old = tot_new = 0
    for cp, ids, old_n, new_n in rows:
        tot_old += old_n
        tot_new += new_n
        flag = "  ⚠ NUEVA<VIEJA" if new_n < old_n else ""
        print(f"{cp[:25]:<26} {(','.join(ids))[:21]:<22} {old_n:>8} {new_n:>8}  {new_n - old_n:>+8}{flag}")
    print("-" * 96)
    print(f"{'TOTAL':<26} {'':<22} {tot_old:>8} {tot_new:>8}  {tot_new - tot_old:>+8}")

    # Contrapartes que están en MesaAPI pero NO tienen cuenta en Contrapartes
    # (no se podrían linkear por id_cuenta) — para no perderlas en la migración.
    sin_cuenta = sorted(set(old_counts) - set(cp_to_ids))
    if sin_cuenta:
        print(f"\n⚠ {len(sin_cuenta)} contrapartes con movimientos en MesaAPI pero SIN `cuenta` "
              f"en CashFlow.Contrapartes (no linkean por id_cuenta):")
        for cp in sin_cuenta:
            print(f"    {cp}  (MesaAPI: {old_counts[cp]})")

    if args.cat:
        print("\n" + "=" * 96)
        print("DESGLOSE por categoría del feed NUEVO (qué trae de más la vista nueva)")
        print("=" * 96)
        for cp, ids, old_n, new_n in rows:
            if new_n == 0:
                continue
            cats = ", ".join(f"{k}={v}" for k, v in cat_by_cp[cp].most_common())
            print(f"  {cp}: {cats}")


if __name__ == "__main__":
    main()
