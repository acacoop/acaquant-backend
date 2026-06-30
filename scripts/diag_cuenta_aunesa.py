"""diag_cuenta_aunesa.py — busca una cuenta en el ORIGEN (Aunesa listadoCuentas).

Cuando una cuenta NO está en clientes.comitentes (ver scripts/diag_cuenta), este
diag mira si Aunesa la conoce y con qué `tipo`/`estado` — para saber por qué el
sync (`jobs.sync_comitentes`, que filtra tipoCuenta=Comitente + estado=Activa) la
salteó. Read-only (solo GET a Aunesa, sin filtro → trae TODOS los tipos/estados).

Uso: python -m scripts.diag_cuenta_aunesa 1839
"""
from __future__ import annotations

import sys

import requests

from jobs.sync_comitentes import LISTADO_URL, _auth, _map_cuenta


def main(idc: str) -> None:
    idc = idc.strip()
    headers = _auth()
    r = requests.get(LISTADO_URL, headers=headers, timeout=180)  # SIN filtro → todas
    r.raise_for_status()
    data = r.json()
    if isinstance(data, dict):
        data = data.get("cuentas") or data.get("data") or [data]
    print(f"Aunesa listadoCuentas (sin filtro): {len(data)} cuentas\n")

    # Match por id / numeroCuenta exacto.
    hits = [
        c for c in data
        if idc in (str(c.get("id")), str(c.get("numeroCuenta") or ""), str(c.get("numero") or ""))
    ]
    print(f"=== Match exacto (id/numeroCuenta == {idc!r}): {len(hits)} ===")
    for c in hits:
        d = _map_cuenta(c)
        print("    ", {k: d.get(k) for k in
                       ("id_cuenta", "denominacion", "tipo", "estado", "clase", "operador_email")})

    # Búsqueda amplia: el string aparece en algún campo del JSON de la cuenta.
    amplio = [c for c in data if idc in str(c)]
    print(f"\n=== El string {idc!r} aparece en alguna cuenta: {len(amplio)} ===")
    for c in amplio[:8]:
        print("    id=", c.get("id"), "· num=", c.get("numeroCuenta"),
              "· denom=", c.get("denominacion"), "· tipo=", c.get("tipo"), "· estado=", c.get("estado"))

    print("\n=== CÓMO LEERLO ===")
    print("- En Aunesa pero tipo≠Comitente o estado≠Activa → el sync la saltea. Fix: ajustar el sync.")
    print("- En Aunesa, Comitente + Activa, pero no está en nuestro master → el sync falló/no corrió.")
    print("- NO aparece en Aunesa → el id 1839 no es un comitente (¿sub-cuenta? ¿número distinto?).")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "1839")
