"""Saca el módulo `operar` del role `sales` en la matriz viva (Mongo).

Contexto: `core/roles.py::DEFAULT_MATRIX` ya tiene `operar` como admin-only
(decisión 2026-05-17), pero `Manager.RoleMatrix` PISA ese default y ahí
`sales` todavía lo tiene → un sales puede enviar/cancelar órdenes. Esto lo
quita de la matriz persistida (con audit log + invalidación de cache, vía
`set_role_modules`).

Solo toca `sales`. No modifica `trader` ni `admin`.

Uso (en el Droplet, dentro del venv):
    python -m scripts.quitar_operar_sales            # DRY-RUN: muestra antes/después
    python -m scripts.quitar_operar_sales --apply    # escribe el cambio

Idempotente: si `sales` ya no tiene `operar`, es no-op.
"""
from __future__ import annotations

import sys

from core.roles import get_matrix, set_role_modules


def main() -> int:
    apply = "--apply" in sys.argv

    matrix = get_matrix()
    actuales = list(matrix.get("sales", ()))
    if "operar" not in actuales:
        print("sales NO tiene `operar` en la matriz viva — no-op.")
        print(f"  módulos sales: {actuales}")
        return 0

    nuevos = [m for m in actuales if m != "operar"]
    print("Cambio en role `sales`:")
    print(f"  antes:   {actuales}")
    print(f"  después: {nuevos}")
    print("  quitado: operar")

    if not apply:
        print("\n[DRY-RUN] No se escribió nada. Re-corré con --apply para aplicar.")
        return 0

    res = set_role_modules("sales", nuevos, actor="EXT-audit: quitar operar de sales")
    print("\nAPLICADO. Doc resultante en Manager.RoleMatrix:")
    print(f"  {res}")
    print("Cache invalidada — el cambio rige en <60s para todos los procesos.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
