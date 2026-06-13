"""scripts/diag_aunesa_job_diario.py — replica EXACTA del job diario de AuM, AHORA.

Hace la MISMA consulta que jobs/aum.py corriendo en este momento:
  * desde          = fecha_t2()  (T+2 días hábiles adelante, igual que el job)
  * fecha_snapshot = HOY         (la etiqueta con la que el job guardaría el doc)
para UNA cuenta (default 805). Dumpea lo que trae Aunesa + con qué fecha lo
etiquetaría. Read-only (no escribe nada en Mongo).

Recordá la regla confirmada: `desde=X` devuelve la posición del día hábil
ANTERIOR a X.  → con desde=T+2, el dato es la posición al T+1, etiquetado como HOY.

Uso:
    python -m scripts.diag_aunesa_job_diario          # cuenta 805
    python -m scripts.diag_aunesa_job_diario 255
"""
from __future__ import annotations

import sys
from datetime import datetime

from jobs.aum import autenticar, consultar_posicion, fecha_t2

CUENTA_DEFAULT = "805"


def main() -> int:
    cuenta = sys.argv[1] if len(sys.argv) > 1 else CUENTA_DEFAULT
    desde = fecha_t2()                               # lo que manda el job DIARIO
    fecha_snapshot = datetime.now().strftime("%Y-%m-%d")  # como lo etiqueta el job

    print(f"=== JOB DIARIO simulado (jobs/aum.py) — cuenta {cuenta} ===")
    print(f"  fecha_snapshot (etiqueta con la que guardaría el doc): {fecha_snapshot}")
    print(f"  desde que manda el job (fecha_t2 = T+2 hábiles): {desde}")
    print("  regla: desde=X → posición del día hábil ANTERIOR a X "
          "(con T+2, el dato sería al T+1, etiquetado HOY)\n")

    headers = autenticar()
    data, reauth = consultar_posicion(cuenta, headers, desde=desde)
    if reauth:
        headers = autenticar()
        data, _ = consultar_posicion(cuenta, headers, desde=desde)
    if data is None:
        print("  (sin respuesta / error HTTP de Aunesa)")
        return 0

    rows = data if isinstance(data, list) else []
    if not rows and isinstance(data, dict):
        for k in ("posiciones", "data", "items", "result", "registros"):
            if isinstance(data.get(k), list):
                rows = data[k]
                break
    acum = [r for r in rows if r.get("informacion") == "Acumulado"]
    print(f"  registros totales: {len(rows)}   |   Acumulado (lo que usa el job): {len(acum)}")
    if not acum:
        return 0

    date_keys = [k for k in acum[0] if "fecha" in k.lower() or "date" in k.lower()]
    print(f"  CAMPOS DE FECHA detectados: {date_keys or '(ninguno)'}")
    print(f"  {'UNIDAD':<42} {'CANTIDAD':>16} {'PRECIO':>14} "
          + " ".join(f"{k:>12}" for k in date_keys))
    total = 0.0
    for r in acum:
        try:
            cant = float(r.get("cantidad") or 0)
        except (TypeError, ValueError):
            cant = 0.0
        total += cant
        print(f"  {str(r.get('unidad'))[:42]:<42} {cant:>16,.2f} "
              f"{r.get('precio')!s:>14} "
              + " ".join(f"{r.get(k)!s:>12}" for k in date_keys))
    print(f"  SUMA cantidades (cruda; el job la multiplica por -1): {total:,.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
