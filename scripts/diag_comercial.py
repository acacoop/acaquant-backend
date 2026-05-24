"""scripts/diag_comercial.py — valida la vista COMERCIAL (OPERACIONES).

Lee `Clientes.Comitentes` × `NegocioMovimientos` × `Valuaciones.AuM` y muestra,
para el operador con más cuentas (o el que pases con --operador), los números
del nuevo endpoint fusionado `operador_comercial` + un sample de la serie.
Read-only. Correr en el Droplet:  python -m scripts.diag_comercial
"""
from __future__ import annotations

import argparse

from api.services.comercial import (
    listar_operadores_comercial,
    operador_comercial,
    serie_comercial,
)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--operador", help="operador_email (default: el de más cuentas)")
    args = ap.parse_args()

    ops = listar_operadores_comercial()
    if not ops:
        print("❌ Sin operadores en Clientes.Comitentes (estado Activa, operador_email no nulo).")
        return
    print(f"Operadores: {len(ops)}. Top 5 por # cuentas:")
    for o in ops[:5]:
        print(f"  {o['n_cuentas']:>4}  {o['operador_nombre'] or '—':<28}  {o['operador_email']}")

    email = args.operador or ops[0]["operador_email"]
    print(f"\n── operador_comercial(operador={email!r}) ──")
    data = operador_comercial(operador=email)
    r = data["resumen"]
    print(f"  AuM gestionado : {r['aum_gestionado']:,.2f}")
    print(f"  # clientes     : {r['n_clientes']}")
    print(f"  Volumen MTD    : {r['volumen_mtd']:,.2f}")
    print(f"  Volumen YTD    : {r['volumen_ytd']:,.2f}")

    cli = data["clientes"]
    print(f"\n  Top {min(3, len(cli))} clientes por AuM (con ficha):")
    for c in cli[:3]:
        ficha_pobladas = {k: v for k, v in c["ficha"].items() if v not in (None, "")}
        print(f"    [{c['id_cuenta']}] {c['denominacion'][:32]:<32} "
              f"AuM={c['aum']:>14,.0f}  VolYTD={c['volumen_ytd']:>14,.0f}")
        print(f"       ficha pobladas ({len(ficha_pobladas)}): "
              f"{', '.join(sorted(ficha_pobladas)) or '—'}")

    if cli:
        idc = cli[0]["id_cuenta"]
        s_op = serie_comercial(operador=email, metric="volumen")
        s_cli = serie_comercial(operador=email, metric="volumen", id_cuenta=idc)
        print(f"\n  Serie volumen — operador: {len(s_op['serie'])} pts · "
              f"cliente [{idc}]: {len(s_cli['serie'])} pts")
        s_aum = serie_comercial(operador=email, metric="aum")
        print(f"  Serie AuM      — operador: {len(s_aum['serie'])} pts")


if __name__ == "__main__":
    main()
