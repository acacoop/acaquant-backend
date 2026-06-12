"""scripts/diag_valuaciones_flujo.py — diag de la vista NEGOCIO→Valuaciones.

Corre get_resumen y get_mensual del service DIRECTO (sin pasar por la API), para
aislar si "MENSUAL vacío" es (a) la API sin reiniciar o (b) un error/0 datos del
backend. Read-only.

Uso:
    python -m scripts.diag_valuaciones_flujo            # cuenta 100
    python -m scripts.diag_valuaciones_flujo 255        # otra cuenta
"""
from __future__ import annotations

import sys
import traceback

from api.services import valuaciones_flujo as svc


def main() -> int:
    idc = sys.argv[1] if len(sys.argv) > 1 else "100"

    print(f"== get_resumen(id_cuenta={idc}) ==")
    try:
        r = svc.get_resumen(id_cuenta=idc)
        print(f"  filas: {len(r.get('filas', []))}   neto_total: {r.get('neto_total')}")
        print(f"  rango: {r.get('desde')} → {r.get('hasta')}")
    except Exception:
        traceback.print_exc()

    print(f"\n== get_mensual(id_cuenta={idc}) ==")
    try:
        m = svc.get_mensual(id_cuenta=idc)
        meses = m.get("meses", [])
        print(f"  n_meses: {len(meses)}")
        for row in meses[:4]:
            print(f"    {row.get('mes')}  cierre={row.get('valuacion_cierre')}  "
                  f"flujo={row.get('flujo_neto')}  tea={row.get('tea_mensual')}  "
                  f"tem={row.get('tem_periodo')}")
        if not meses:
            print("  ⚠ 0 meses → no hay snapshots de AuM para esta cuenta "
                  "(o id_cuenta no matchea Valuaciones.AuM). Probá otra cuenta.")
    except Exception:
        traceback.print_exc()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
