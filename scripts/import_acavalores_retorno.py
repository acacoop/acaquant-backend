"""scripts/import_acavalores_retorno.py — carga del informe "ACA VALORES RETORNO TOTAL".

Carga el Excel "OP Aca Valores FCI - <mes>.xls" (informe de operaciones bursátiles
del fondo ACA R.TOTAL) en SQL `operaciones.acavalores_retorno`. Alimenta la tab
"ACA VALORES RETORNO TOTAL" de Mesa de Dinero.

⚠️ Este script NO tiene lógica propia: parseo, validaciones y escritura viven en
`api/services/acavalores_retorno.py::importar`, la MISMA función que corre el
botón «IMPORTAR .XLS» de la tab (admin-only). Acá solo está la línea de comandos.
Dos parsers habrían divergido el día que el informe cambie de formato.

Idempotente por PERÍODO ('YYYY-MM' de la fecha de concertación): re-importar un mes
BORRA y reinserta solo ese mes; los otros meses no se tocan. Cortarlo y re-correrlo
no duplica.

Uso (desde la raíz del repo, con .env que tenga POSTGRES_URI):
    python -m scripts.import_acavalores_retorno "C:/ruta/OP Aca Valores FCI - jul2026.xls"
    python -m scripts.import_acavalores_retorno <archivo.xls> --dry-run   # no escribe, solo resume
"""
from __future__ import annotations

import argparse
from pathlib import Path

from api.services.acavalores_retorno import importar


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Carga el informe 'OP Aca Valores FCI' en operaciones.acavalores_retorno.")
    ap.add_argument("archivo", help="ruta al .xls del informe.")
    ap.add_argument("--dry-run", action="store_true",
                    help="parsea y resume, sin escribir en la base.")
    ap.add_argument("--actor", default="cli",
                    help="quién importa (queda en el audit y en importado_por).")
    args = ap.parse_args()

    path = Path(args.archivo)
    if not path.exists():
        raise SystemExit(f"No existe el archivo: {path}")

    try:
        res = importar(path, archivo=path.name, actor=args.actor, dry_run=args.dry_run)
    except ValueError as e:      # archivo que no es el informe / sin períodos
        raise SystemExit(str(e)) from e

    print(f"Archivo:  {res['archivo']}")
    print(f"Períodos: {', '.join(res['periodos'])}")
    print(f"Filas:    {res['filas']}  ·  Σ Valor Nominal = {res['total_vn']:,.2f}"
          f"  ·  Σ CASH = {res['total_cash']:,.2f}")
    for d in res["detalle"]:
        print(f"  {d['periodo']}: {d['filas']} fila(s) nuevas · "
              f"{d['existentes']} en la base hoy (se reemplazan)")
    if res["sin_fecha"]:
        print(f"  ⚠️ {res['sin_fecha']} fila(s) SIN fecha de concertación → no se importan.")

    if res["dry_run"]:
        print("\n[dry-run] No se escribió nada.")
    else:
        print(f"\nOK — reemplazadas {res['borradas']} fila(s) previa(s); "
              f"insertadas {res['filas']}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
