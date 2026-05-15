"""backfill_meses_cuenta.py — corre aum_backfill para los cierres de mes
de jun-2025 a feb-2026, para una cuenta puntual.

Sirve para rehacer una cuenta a la que le faltaban posiciones en varios
snapshots (ej. la que no tenía [4306] Adcap Balanceado XVI en julio).

Llama a jobs.aum_backfill como subproceso por cada fecha — reusa el job
tal cual, con --cuenta (no pega a listadoCuentas de Aunesa).

Snapshots: cierres reales de fin de mes. Octubre (2025-10-31) NO está
en la lista. Para cambiar el set, editá _SNAPSHOTS.

Corre:  python -m scripts.backfill_meses_cuenta <id_cuenta>
        python -m scripts.backfill_meses_cuenta 123,456     (varias cuentas)
"""
from __future__ import annotations

import subprocess
import sys

# Cierres de mes a rebackfilear (fechas reales de fin de mes).
_SNAPSHOTS = [
    "2025-06-30",
    "2025-07-31",
    "2025-08-31",
    "2025-09-30",
    "2025-11-30",
    "2025-12-31",
    "2026-01-31",
    "2026-02-28",
]


def main() -> None:
    if len(sys.argv) < 2:
        print("Uso: python -m scripts.backfill_meses_cuenta <id_cuenta[,id_cuenta...]>")
        return
    cuentas = sys.argv[1]

    print(f"Backfill de {len(_SNAPSHOTS)} snapshots para cuenta(s): {cuentas}")
    print("=" * 72)

    fallidos: list[str] = []
    for fecha in _SNAPSHOTS:
        print(f"\n{'#' * 72}")
        print(f"# BACKFILL {fecha} — cuenta {cuentas}")
        print(f"{'#' * 72}", flush=True)
        r = subprocess.run(
            [sys.executable, "-m", "jobs.aum_backfill", fecha, "--cuenta", cuentas],
        )
        if r.returncode != 0:
            print(f"⚠ {fecha} terminó con código {r.returncode}", flush=True)
            fallidos.append(fecha)

    print("\n" + "=" * 72)
    if fallidos:
        print(f"⚠ Snapshots con error: {fallidos}")
    else:
        print(f"✅ Los {len(_SNAPSHOTS)} snapshots corrieron OK.")
    print("Después: correr fix_precios_aum para cada mes con su precios_*.json.")


if __name__ == "__main__":
    main()
