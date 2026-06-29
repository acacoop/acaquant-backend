"""scripts/diag_segmentacion_none.py — POR QUÉ caen a None en segmentar_patrimonial.

Read-only. Reproduce la clasificación SQL-native y, para las cuentas que pasarían de
un nivel_3 con valor → None, desglosa el MOTIVO (tipo_cliente null / cupo null / cupo<=0).
Sirve para decidir si el cambio es legítimo (cuentas sin cupo real) o un agujero de datos
(cupo que no migró de Mongo a SQL). NO escribe nada.

    python -m scripts.diag_segmentacion_none
"""
from __future__ import annotations

from collections import Counter

from api.services.macro import get_ultimo_mep, get_ultimo_uva
from api.services.segmentacion import cargar_ids_contrapartes, clasificar_nivel_3
from core.postgres import get_pool


def main() -> int:
    mep = get_ultimo_mep().get("mep")
    mep = float(mep) if mep else None
    uva = get_ultimo_uva()
    contrapartes = cargar_ids_contrapartes()
    print(f"MEP={mep}  UVA={uva}  contrapartes={len(contrapartes)}")

    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id_cuenta, tipo_cliente, nivel_3, cupo_transaccional_ars "
            "FROM comitentes WHERE estado = 'Activa'")
        rows = cur.fetchall()

    caen: list[tuple] = []          # nivel_3 actual ≠ None y nuevo == None
    suben: list[tuple] = []         # actual None y nuevo con valor (info)
    motivo: Counter = Counter()
    cupo_nulls_total = 0
    for idc, tipo, nivel3, cupo in rows:
        cupo_f = float(cupo) if cupo is not None else None
        if cupo is None:
            cupo_nulls_total += 1
        nuevo = clasificar_nivel_3(
            tipo, cupo_f, mep=mep, uva=uva, es_contraparte=str(idc) in contrapartes)
        if nuevo is None and nivel3 is not None:
            caen.append((idc, tipo, cupo, str(idc) in contrapartes, nivel3))
            if not tipo:
                motivo["tipo_cliente null/empty"] += 1
            elif cupo is None:
                motivo["cupo null (no migró?)"] += 1
            elif cupo_f is not None and cupo_f <= 0:
                motivo["cupo <= 0"] += 1
            else:
                motivo["otro (revisar)"] += 1
        elif nuevo is not None and nivel3 is None:
            suben.append((idc, tipo, nuevo))

    print(f"\nActivas: {len(rows)}  |  cupo_transaccional_ars NULL en SQL: {cupo_nulls_total}")
    print(f"\nCAEN a None (hoy tienen valor): {len(caen)}  →  desglose por motivo:")
    for k, v in motivo.most_common():
        print(f"  {k:<26} {v:>5}")
    print(f"\nSUBEN (hoy None → nuevo valor): {len(suben)}")
    print("\nPrimeras 20 que caen (id, tipo_cliente, cupo, es_contraparte, nivel_3_actual):")
    for r in caen[:20]:
        print(f"  {r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
