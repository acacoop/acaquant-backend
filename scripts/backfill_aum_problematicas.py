"""Backfill AuM enfocado en las cuentas grandes con timeouts crónicos.

Wrapper sobre `jobs.aum_backfill` con:
  - Lista hardcodeada de cuentas problemáticas (las que repetidamente
    timeoutean en Aunesa para data histórica).
  - Defaults conservadores: workers=1 (secuencial), timeout=360s, retries=5.
  - Acepta una o más fechas YYYY-MM-DD en una sola corrida.

Si la lista cambia, editala en `CUENTAS_PROBLEMATICAS` abajo (una sola
fuente de verdad — no copiar-pegar el CSV en cada comando).

Uso:
    python -m scripts.backfill_aum_problematicas 2025-07-01
    python -m scripts.backfill_aum_problematicas 2025-07-01 2025-08-01
    python -m scripts.backfill_aum_problematicas 2025-07-01 --cuenta 101,106
    python -m scripts.backfill_aum_problematicas 2025-07-01 --workers 2 --timeout 300
"""
from __future__ import annotations

import argparse
import subprocess
import sys

# Cuentas que históricamente timeoutean en backfill (curado a mano —
# editar acá si aparecen más).
CUENTAS_PROBLEMATICAS: list[str] = [
    "101", "106", "108", "110", "121", "130", "132", "133", "1431",
    "163", "170", "176", "184", "194", "210", "255", "455", "462",
    "523", "569", "573", "580", "586",
]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("fechas", nargs="+",
                    help="Una o más fechas YYYY-MM-DD a backfilear")
    ap.add_argument("--cuenta",
                    help="Override de la lista (CSV de id_cuenta). "
                         "Si se omite, usa CUENTAS_PROBLEMATICAS hardcodeada.")
    ap.add_argument("--workers", type=int, default=1,
                    help="Paralelismo (default 1, secuencial)")
    ap.add_argument("--timeout", type=int, default=360,
                    help="Timeout HTTP por cuenta (default 360s)")
    ap.add_argument("--retries", type=int, default=5,
                    help="Reintentos por cuenta con backoff (default 5)")
    args = ap.parse_args()

    cuentas = args.cuenta or ",".join(CUENTAS_PROBLEMATICAS)
    n = cuentas.count(",") + 1 if cuentas else 0

    print(f"Backfill enfocado: {len(args.fechas)} fechas × {n} cuentas")
    print(f"  cuentas: {cuentas}")
    print(f"  workers={args.workers}  timeout={args.timeout}s  retries={args.retries}")

    failed: list[str] = []
    for i, fecha in enumerate(args.fechas, 1):
        print(f"\n{'=' * 70}")
        print(f"[{i}/{len(args.fechas)}] fecha: {fecha}")
        print("=" * 70)
        cmd = [
            sys.executable, "-m", "jobs.aum_backfill", fecha,
            "--cuenta",   cuentas,
            "--workers",  str(args.workers),
            "--timeout",  str(args.timeout),
            "--retries",  str(args.retries),
        ]
        result = subprocess.run(cmd)
        if result.returncode != 0:
            failed.append(fecha)
            print(f"⚠ {fecha} terminó con código {result.returncode}, sigo con el resto.")

    print(f"\n{'=' * 70}")
    print(f"FINAL: {len(args.fechas) - len(failed)}/{len(args.fechas)} fechas OK")
    if failed:
        print(f"⚠ Fallaron: {', '.join(failed)}")
        sys.exit(1)


if __name__ == "__main__":
    main()
