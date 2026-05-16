"""diag_cuentas_con_error.py — resume docs/cuentas_con_error.json.

Lee la lista acumulativa que escribe `jobs/aum_backfill.py`
(`[{fecha_snapshot, id_cuenta, error}]`), agrupa por snapshot y, para
cada mes con cuentas fallidas, imprime el comando de reintento listo
para copiar.

Corre:  python -m scripts.diag_cuentas_con_error
"""
from __future__ import annotations

import json
import os
from collections import Counter, defaultdict

_PATH = os.path.join(os.path.dirname(__file__), "..", "docs",
                     "cuentas_con_error.json")


def main() -> None:
    if not os.path.exists(_PATH):
        print(f"docs/cuentas_con_error.json no existe — 0 cuentas con error.")
        return

    with open(_PATH, encoding="utf-8") as f:
        registros = json.load(f)
    if not isinstance(registros, list) or not registros:
        print("docs/cuentas_con_error.json vacío — 0 cuentas con error.")
        return

    por_fecha: dict[str, list[dict]] = defaultdict(list)
    for r in registros:
        if isinstance(r, dict):
            por_fecha[r.get("fecha_snapshot") or "(sin fecha)"].append(r)

    print(f"docs/cuentas_con_error.json — {len(registros)} filas, "
          f"{len(por_fecha)} snapshots con error.")
    print("=" * 72)

    for fecha in sorted(por_fecha):
        filas = por_fecha[fecha]
        ids = sorted({str(r.get("id_cuenta")) for r in filas if r.get("id_cuenta")})
        errores = Counter(str(r.get("error") or "(sin error)") for r in filas)
        print(f"\n# {fecha} — {len(ids)} cuentas")
        print(f"  ids: {','.join(ids)}")
        for err, n in errores.most_common():
            print(f"  · {n}× {err}")
        print(f"  reintento:\n    python -m jobs.aum_backfill {fecha} "
              f"--cuenta {','.join(ids)} --workers 1 --timeout 360 --retries 5")


if __name__ == "__main__":
    main()
