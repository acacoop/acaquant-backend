"""diag_aunesa_posicion_cruda.py — posición de Aunesa para el DÍA exacto.

El job pide posicionValuada con desde=T+2 → posición proyectada a
liquidación, NO la del día (de ahí las diferencias). Este script pide
desde=<fecha exacta>, sin T+2, para ver la posición real del día.

Corre:  python -m scripts.diag_aunesa_posicion_cruda <id_cuenta> <fecha YYYY-MM-DD>
"""
from __future__ import annotations

import sys
from datetime import datetime

from jobs.aum import autenticar, consultar_posicion


def main() -> None:
    if len(sys.argv) < 3:
        print("Uso: python -m scripts.diag_aunesa_posicion_cruda <id_cuenta> <YYYY-MM-DD>")
        return
    id_cuenta = sys.argv[1]
    try:
        fecha_dt = datetime.strptime(sys.argv[2], "%Y-%m-%d")
    except ValueError:
        print(f"fecha mal formada: {sys.argv[2]} (esperado YYYY-MM-DD)")
        return
    desde = fecha_dt.strftime("%d/%m/%Y")  # la fecha EXACTA — sin T+2

    print(f"cuenta {id_cuenta}  |  desde={desde}  (día exacto, SIN T+2)")
    headers = autenticar()
    data, reauth = consultar_posicion(id_cuenta, headers, desde)
    if reauth:
        headers = autenticar()
        data, _ = consultar_posicion(id_cuenta, headers, desde)
    if not data:
        print("Aunesa: sin datos / respuesta no-200")
        return

    items = [r for r in data if r.get("informacion") == "Acumulado"]
    print(f"{len(items)} posiciones (Acumulado):")
    for r in items:
        u = str(r.get("unidad"))[:46]
        print(f"  {u:46}  cant={r.get('cantidad')}  precio={r.get('precio')}")


if __name__ == "__main__":
    main()
