"""scripts/test_cronjob_dia_siguiente.py — como test_cronjob, pero CORRIGIENDO el desde.

Confirmado: Aunesa con `desde=X` devuelve la posición del día hábil ANTERIOR a X.
Entonces, para ver la posición AL día OBJETIVO, hay que mandar `desde = día hábil
SIGUIENTE al objetivo`. Este script hace justamente eso (mismo procesar/valuación
que el cron) para validar el fix. NO escribe nada en Mongo.

Override por CLI:
    python -m scripts.test_cronjob_dia_siguiente 255 29/05/2026
    python -m scripts.test_cronjob_dia_siguiente 255,256 30/05/2026
"""
import sys
from datetime import datetime, timedelta

import holidays

from jobs.aum import autenticar, consultar_posicion, procesar

# ════════════════ EDITÁ SOLO ESTO ════════════════
OBJETIVO = "29/05/2026"     # la fecha que QUERÉS ver la posición (DD/MM/YYYY)
CUENTAS  = ["255"]
# ══════════════════════════════════════════════════

_FERIADOS = holidays.Argentina()


def _proximo_habil(d):
    d = d + timedelta(days=1)
    while d.weekday() >= 5 or d in _FERIADOS:
        d = d + timedelta(days=1)
    return d


def main() -> int:
    objetivo_str = OBJETIVO
    cuentas = CUENTAS
    if len(sys.argv) > 1:
        cuentas = [c.strip() for c in sys.argv[1].split(",") if c.strip()]
    if len(sys.argv) > 2:
        objetivo_str = sys.argv[2]

    objetivo = datetime.strptime(objetivo_str, "%d/%m/%Y").date()
    desde = _proximo_habil(objetivo).strftime("%d/%m/%Y")   # ← día hábil SIGUIENTE
    timestamp = datetime.utcnow()
    fecha_snapshot = objetivo.strftime("%Y-%m-%d")           # etiquetamos con el OBJETIVO

    print("=" * 70)
    print("SANDBOX cron AuM — desde CORREGIDO (día hábil siguiente) — NO escribe Mongo")
    print(f"  OBJETIVO (lo que querés ver)        : {objetivo_str}")
    print(f"  desde que se manda a Aunesa (obj+1) : {desde}")
    print(f"  fecha_snapshot (etiqueta)           : {fecha_snapshot}")
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
        registros = procesar(data, fecha_snapshot, timestamp)
        print(f"  filas crudas: {crudas}   |   registros que se guardarían: {len(registros)}")
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
