"""backfill_meses_cuenta.py — backfill + corrección de precios por cuenta.

Para una cuenta puntual, por cada cierre de mes de jun-2025 a feb-2026:
  1. corre jobs.aum_backfill <fecha> --cuenta <id>  (trae la posición de
     Aunesa y la persiste en Valuaciones.AuM).
  2. corre scripts.fix_precios_aum --cuenta <id>     (reemplaza los precios
     malos de Aunesa por los correctos del Excel del mes y recalcula la
     valuación).

Sirve para rehacer de punta a punta una cuenta a la que le faltaban
posiciones / tenía precios mal en varios meses.

Octubre (2025-10-31) NO está en la lista. Para cambiar el set, editá
_SNAPSHOTS.

Dry-run por default: el fix de precios corre SIN escribir (solo muestra).
El backfill SIEMPRE corre (pega a Aunesa) — no tiene dry-run.
Pasá --apply para que el fix de precios escriba.

Corre:  python -m scripts.backfill_meses_cuenta <id_cuenta> [--apply]
        python -m scripts.backfill_meses_cuenta 123,456 --apply
"""
from __future__ import annotations

import subprocess
import sys

# (fecha_snapshot, JSON de precios del mes). Cierres reales de fin de mes.
_SNAPSHOTS: list[tuple[str, str]] = [
    ("2025-06-30", "docs/precios_3006.json"),
    ("2025-07-31", "docs/precios_3107.json"),
    ("2025-08-31", "docs/precios_3108.json"),
    ("2025-09-30", "docs/precios_3009.json"),
    ("2025-11-30", "docs/precios_3011.json"),
    ("2025-12-31", "docs/precios_3112.json"),
    ("2026-01-31", "docs/precios_3101.json"),
    ("2026-02-28", "docs/precios_2802.json"),
]


def main() -> None:
    args = [a for a in sys.argv[1:]]
    apply = "--apply" in args
    cuentas = next((a for a in args if not a.startswith("-")), None)
    if not cuentas:
        print("Uso: python -m scripts.backfill_meses_cuenta <id_cuenta[,id...]> [--apply]")
        return

    print(f"Cuenta(s): {cuentas}   |   {len(_SNAPSHOTS)} meses   |   "
          f"fix precios: {'APPLY' if apply else 'DRY-RUN'}")
    print("=" * 72)

    fallidos: list[str] = []
    for fecha, json_precios in _SNAPSHOTS:
        print(f"\n{'#' * 72}\n# {fecha} — cuenta {cuentas}\n{'#' * 72}", flush=True)

        # 1. Backfill desde Aunesa.
        print(f"--- backfill {fecha} ---", flush=True)
        r1 = subprocess.run(
            [sys.executable, "-m", "jobs.aum_backfill", fecha, "--cuenta", cuentas],
        )
        if r1.returncode != 0:
            print(f"⚠ backfill {fecha} falló (código {r1.returncode}) — salteo fix precios", flush=True)
            fallidos.append(fecha)
            continue

        # 2. Corrección de precios para esa cuenta.
        print(f"--- fix precios {fecha} ({json_precios}) ---", flush=True)
        cmd = [sys.executable, "-m", "scripts.fix_precios_aum",
               "--precios", json_precios, "--snapshot", fecha, "--cuenta", cuentas]
        if apply:
            cmd.append("--apply")
        r2 = subprocess.run(cmd)
        if r2.returncode != 0:
            print(f"⚠ fix precios {fecha} falló (código {r2.returncode})", flush=True)
            fallidos.append(fecha)

    print("\n" + "=" * 72)
    if fallidos:
        print(f"⚠ Meses con error: {sorted(set(fallidos))}")
    else:
        print(f"✅ Los {len(_SNAPSHOTS)} meses corrieron OK.")
    if not apply:
        print("Fue DRY-RUN del fix de precios. Re-corré con --apply para escribir.")


if __name__ == "__main__":
    main()
