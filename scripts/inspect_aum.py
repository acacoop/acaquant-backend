"""Inspector general para Valuaciones.AuM — buscá keywords en `cuenta`,
`unidad` o ambos, y devuelve count + valores únicos + muestra de docs.

Pensado para entender qué hay en la colección antes de borrar / filtrar.
Es read-only — no modifica nada.

Ejemplos:
    python -m scripts.inspect_aum --cuenta LOMBARD
    python -m scripts.inspect_aum --cuenta SCHRODER --limit 20
    python -m scripts.inspect_aum --unidad OTC
    python -m scripts.inspect_aum --cuenta TORONTO --fecha 2026-05-05
    python -m scripts.inspect_aum --cuenta-contraparte   # cruza con ContrapartesAPI
"""
from __future__ import annotations

import argparse
import re

from core.mongo import get_mongo_client_read


def _build_match(args) -> dict:
    clauses: list[dict] = []
    if args.cuenta:
        clauses.append({"cuenta": {"$regex": re.escape(args.cuenta), "$options": "i"}})
    if args.unidad:
        clauses.append({"unidad": {"$regex": re.escape(args.unidad), "$options": "i"}})
    if args.fecha:
        clauses.append({"fecha_snapshot": args.fecha})
    if not clauses:
        return {}
    return clauses[0] if len(clauses) == 1 else {"$and": clauses}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cuenta", help="Substring case-insensitive en `cuenta`")
    ap.add_argument("--unidad", help="Substring case-insensitive en `unidad`")
    ap.add_argument("--fecha",  help="Match exacto fecha_snapshot YYYY-MM-DD")
    ap.add_argument("--limit",  type=int, default=10, help="Cantidad de docs a mostrar")
    ap.add_argument(
        "--cuenta-contraparte", action="store_true",
        help="Cruza Valuaciones.AuM ↔ CuentasAPI.ContrapartesAPI por `cuenta` "
             "exacto y reporta cuántas matchean — para diagnosticar si los "
             "formatos coinciden.",
    )
    args = ap.parse_args()

    c = get_mongo_client_read()
    aum = c["Valuaciones"]["AuM"]

    if args.cuenta_contraparte:
        contrapartes = sorted({
            d["cuenta"]
            for d in c["CuentasAPI"]["ContrapartesAPI"].find({}, {"_id": 0, "cuenta": 1})
            if d.get("cuenta")
        })
        print(f"contrapartes en CuentasAPI.ContrapartesAPI: {len(contrapartes)}")
        cuentas_aum = sorted(set(aum.distinct("cuenta")))
        print(f"cuentas distintas en Valuaciones.AuM:        {len(cuentas_aum)}")
        intersect = set(contrapartes) & set(cuentas_aum)
        print(f"intersección exacta (cuenta == cuenta):      {len(intersect)}")

        # Vemos algunos ejemplos de cada lado para detectar mismatches.
        print("\nmuestra ContrapartesAPI (5):")
        for c_ in contrapartes[:5]:
            print(f"  {c_!r}")
        print("\nmuestra Valuaciones.AuM (5):")
        for c_ in cuentas_aum[:5]:
            print(f"  {c_!r}")

        # Si hay 0 intersección, miramos si hay match parcial — quizás solo
        # difiere el prefijo "[NN]".
        if not intersect:
            print("\n— sin matches exactos. Buscando matches por sufijo "
                  "(quitando prefijo '[NN] ') para detectar el bug de formato:")
            def _suf(s: str) -> str:
                m = re.match(r"^\[\d+\]\s*", s)
                return s[m.end():] if m else s
            cont_suf = {_suf(s): s for s in contrapartes}
            aum_suf = {_suf(s): s for s in cuentas_aum}
            inter = set(cont_suf) & set(aum_suf)
            print(f"  matches por sufijo: {len(inter)}")
            for k in sorted(inter)[:10]:
                print(f"    contraparte={cont_suf[k]!r:60s}  aum={aum_suf[k]!r}")
        return

    match = _build_match(args)
    if not match:
        ap.error("Pasá al menos --cuenta, --unidad, --fecha o --cuenta-contraparte.")

    total = aum.count_documents(match)
    print(f"docs que matchean: {total}")
    if total == 0:
        return

    # Valores únicos de cuenta y unidad que matchearon — útil para ver el
    # formato real (mayúsculas, espacios, prefijos numéricos).
    cuentas_unq = sorted(set(aum.distinct("cuenta", match)))
    unidades_unq = sorted(set(aum.distinct("unidad", match)))
    print(f"cuentas distintas:  {len(cuentas_unq)}")
    for c_ in cuentas_unq[:10]:
        print(f"  {c_!r}")
    if len(cuentas_unq) > 10:
        print(f"  ... y {len(cuentas_unq) - 10} más")
    print(f"unidades distintas: {len(unidades_unq)}")
    for u_ in unidades_unq[:10]:
        print(f"  {u_!r}")

    # Muestra de docs.
    print(f"\nmuestra ({args.limit} primeros):")
    for d in aum.find(
        match,
        {"_id": 0, "fecha_snapshot": 1, "cuenta": 1, "unidad": 1,
         "cantidad": 1, "precio": 1, "valuacion": 1, "tipoTitulo": 1},
    ).limit(args.limit):
        print(f"  {d.get('fecha_snapshot')}  {d.get('cuenta','')[:50]:50s}  "
              f"{d.get('unidad','')!r:14s}  qty={d.get('cantidad')}  "
              f"px={d.get('precio')}  val={d.get('valuacion')}  "
              f"tipo={d.get('tipoTitulo','')}")


if __name__ == "__main__":
    main()
