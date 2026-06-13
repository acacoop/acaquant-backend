"""scripts/test_cronjob.py — SANDBOX del job diario de AuM (jobs/aum.py).

Hace EXACTAMENTE lo que el cron diario por cada cuenta:
  autenticar → consultar_posicion(mismos params) → procesar(mismos filtros + valuación)
PERO sin recorrer todas las cuentas (no llama listadoCuentas) y con la FECHA ORIGEN
y las CUENTAS hardcodeadas acá arriba para que juegues. NO escribe nada en Mongo.

────────────────────────────────────────────────────────────────────────────
EDITÁ SOLO ESTO (lo único que cambia respecto del cron real):
"""
DESDE   = "30/05/2026"      # fecha origen que se le manda a Aunesa (DD/MM/YYYY)
CUENTAS = ["255"]           # cuenta(s) a consultar
# ────────────────────────────────────────────────────────────────────────────
# Override opcional por CLI (sin editar el archivo, así no choca con git pull):
#   python -m scripts.test_cronjob 255 30/05/2026
#   python -m scripts.test_cronjob 255,256 02/06/2026
# Todo lo demás (params de la consulta, procesar, valuación, filtros) = IDÉNTICO al cron.

from __future__ import annotations

import sys
from datetime import datetime

from jobs.aum import autenticar, consultar_posicion, procesar


def main() -> int:
    cuentas = CUENTAS
    desde = DESDE
    if len(sys.argv) > 1:
        cuentas = [c.strip() for c in sys.argv[1].split(",") if c.strip()]
    if len(sys.argv) > 2:
        desde = sys.argv[2]

    timestamp = datetime.utcnow()
    fecha_snapshot = timestamp.strftime("%Y-%m-%d")  # = HOY, igual que el cron

    print("=" * 70)
    print("SANDBOX cron AuM (jobs/aum.py) — NO escribe en Mongo")
    print(f"  desde (lo que se le manda a Aunesa) : {desde}")
    print(f"  fecha_snapshot (etiqueta del cron)  : {fecha_snapshot}  (= hoy)")
    print(f"  cuentas                             : {cuentas}")
    print("=" * 70)

    headers = autenticar()
    for cuenta in cuentas:
        print(f"\n--- cuenta {cuenta} ---")
        data, reauth = consultar_posicion(cuenta, headers, desde=desde)
        if reauth:
            headers = autenticar()
            data, _ = consultar_posicion(cuenta, headers, desde=desde)
        if data is None:
            print("  (sin respuesta / error HTTP de Aunesa)")
            continue

        crudas = len(data) if isinstance(data, list) else "?"
        # MISMO procesado que el cron: Acumulado, cantidad×-1, groupby, exclusiones, valuación.
        registros = procesar(data, fecha_snapshot, timestamp)
        print(f"  filas crudas: {crudas}   |   registros que el cron GUARDARÍA: {len(registros)}")
        if not registros:
            continue
        print(f"  {'UNIDAD':<42} {'CANTIDAD':>15} {'PRECIO':>14} {'VALUACION':>18}")
        total = 0.0
        for r in sorted(registros, key=lambda x: -float(x.get('valuacion') or 0)):
            total += float(r.get("valuacion") or 0)
            print(f"  {str(r.get('unidad'))[:42]:<42} {float(r.get('cantidad') or 0):>15,.2f} "
                  f"{r.get('precio')!s:>14} {float(r.get('valuacion') or 0):>18,.2f}")
        print(f"  TOTAL valuación: {total:,.2f}")

    print("\n(NO se escribió nada en Mongo — es solo prueba.)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
