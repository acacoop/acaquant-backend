"""scripts/diag_comercial_perf.py — dónde se va el tiempo del Tablero Comercial.

`/api/operaciones/comercial/operador` (191ms × 8.487) y `/comercial/serie`
(94ms × 15.817) suman ~3.100s por semana, y sus queries de
`SUM(CASE WHEN moneda…)` sobre `negocio_movimientos`/`tenencia` son el mayor
bloque de LECTURA de toda la base (~8.800s acumulados, 35-40ms cada una).

Antes de elegir remedio hay que saber cuál es el problema, porque cada uno
lleva a un arreglo distinto y solo uno de los tres es gratis:

  · Muchos VIAJES (queries repetidas / N+1)  → agrupar. No cambia nada. GRATIS.
  · UNA query cara                            → índice o forma de la tabla.
  · Trabajo legítimo irreductible             → cache (cambia frescura) o
                                                agregado precomputado (proyecto).

Es el mismo método que en `/api/derivados/agro`, donde el diagnóstico "a ojo"
(parecía Python) resultó ser 66 viajes a la base: mirar `psycopg.connection
(wait)` en el perfil es lo que lo destapó.

Read-only: solo LEE, con parámetros reales sacados de la propia base.

Uso (en el Droplet):
    python -m scripts.diag_comercial_perf              # tiempos
    python -m scripts.diag_comercial_perf --profile    # qué función/viajes
"""
from __future__ import annotations

import sys
import time

from api.services import comercial_sql as _com


def _ms(t0: float) -> float:
    return (time.perf_counter() - t0) * 1000


def _un_operador() -> str | None:
    """Un operador REAL de la base: medir con datos de verdad, no inventados.

    La clave es `operador_email` (así lo devuelve listar_operadores_comercial y
    así lo filtra el service). Y como esa lista viene ORDER BY n_cuentas DESC,
    el primero es el operador con MÁS cuentas: el peor caso, que es justo el
    que conviene medir."""
    try:
        ops = _com.listar_operadores_comercial()
    except Exception as e:
        print(f"no se pudo listar operadores ({str(e).splitlines()[0]})")
        return None
    for o in ops or []:
        email = o.get("operador_email") if isinstance(o, dict) else o
        if email:
            return str(email)
    return None


def main() -> int:
    op = _un_operador()
    if op is None:
        print("sin operadores en la base — nada que medir.")
        return 1
    print(f"operador de prueba: {op!r}\n")

    # Los dos endpoints calientes del tablero, con su llamada real.
    casos = [
        ("operador_comercial (todos)", lambda: _com.operador_comercial(operador=[])),
        ("operador_comercial (uno)", lambda: _com.operador_comercial(operador=[op])),
    ]

    if "--profile" in sys.argv:
        import cProfile
        import io
        import pstats
        casos[0][1]()                      # calentar pool e imports
        pr = cProfile.Profile()
        pr.enable()
        casos[0][1]()
        pr.disable()
        buf = io.StringIO()
        pstats.Stats(pr, stream=buf).sort_stats("tottime").print_stats(15)
        print("PERFIL (tiempo PROPIO por función)")
        for linea in buf.getvalue().splitlines():
            if linea.strip():
                print("   " + linea)
        print("\n   MIRAR: `psycopg/connection.py(wait)`. Su `ncalls` es la")
        print("   CANTIDAD DE VIAJES a la base en un solo request, y su tiempo")
        print("   es espera pura — no CPU. Si domina, se arregla agrupando")
        print("   queries y no hay que tocar ni índices ni frescura.")
        return 0

    for label, fn in casos:
        tiempos = []
        for _ in range(3):
            t0 = time.perf_counter()
            try:
                fn()
            except Exception as e:
                print(f"{label:<32} ERROR: {str(e).splitlines()[0][:60]}")
                break
            tiempos.append(_ms(t0))
        if tiempos:
            print(f"   {label:<32} min {min(tiempos):7.1f} ms   "
                  f"p50 {sorted(tiempos)[len(tiempos) // 2]:7.1f} ms")
    print("\n   Para saber POR QUÉ tarda eso: --profile")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
