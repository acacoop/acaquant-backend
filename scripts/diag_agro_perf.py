"""scripts/diag_agro_perf.py — en qué se van los 503ms de /api/derivados/agro.

POR QUÉ ESTE DIAG
=================
`/api/derivados/agro` es el endpoint #1 de la plataforma: 16.072 llamadas en 7
días × 503ms = **8.084s, el 17% de TODO el tiempo de la app** (telemetría
2026-08-13). Ya fue optimizado una vez —tiene `@cached(5s)` y sus 5 lecturas
corren en paralelo, lo que lo bajó de 662ms a 503ms— y sigue siendo el #1.

Lo que YA se descartó, con datos, para no repetir el análisis:

  · NO es SQL. Ninguna de sus lecturas aparece en el top 25 de
    pg_stat_statements por tiempo de ejecución.
  · NO es el pool. La concurrencia media de TODA la app en rueda es 0,33
    requests simultáneos contra un pool de 16 — no se satura. (Puede explicar
    el pico de 3,5s, no el promedio.)

Queda medir ADENTRO. Este script cronometra cada pieza por separado, la
compara con el total, y dice cuánto es I/O y cuánto es Python puro.

CÓMO LEERLO
===========
Las 5 lecturas corren en PARALELO, así que el piso teórico del endpoint es la
MÁS LENTA de ellas, no la suma. Si el total real es mucho mayor que esa más
lenta, la diferencia es CPU de Python (los builders) — y ahí no hay índice ni
cache de base que ayude.

Read-only. No escribe nada. Uso (en el Droplet):
    python -m scripts.diag_agro_perf [repeticiones]     # default 3
    python -m scripts.diag_agro_perf --profile          # QUÉ función se come el tiempo
"""
from __future__ import annotations

import sys
import time

from api.services import agro_sql as _agro_sql
from api.services import camara_cereales as _cam
from core.dolar_oficial import mid_oficial_live


def _ms(t0: float) -> float:
    return (time.perf_counter() - t0) * 1000


def _cronometrar(label: str, fn, *args) -> tuple[str, float]:
    t0 = time.perf_counter()
    try:
        fn(*args)
        return label, _ms(t0)
    except Exception as e:  # una pieza rota no puede tumbar la medición
        print(f"   {label:<26} ERROR: {str(e).splitlines()[0][:60]}")
        return label, -1.0


def _profile(crudo) -> int:
    """cProfile sobre UNA corrida: qué función concreta se come los ms.

    Se ordena por TIEMPO PROPIO (tottime), no acumulado: el acumulado siempre
    pone arriba a la función de más afuera, que no dice nada. El propio es
    dónde se está quemando el CPU de verdad."""
    import cProfile
    import io
    import pstats
    pr = cProfile.Profile()
    pr.enable()
    crudo()
    pr.disable()
    buf = io.StringIO()
    pstats.Stats(pr, stream=buf).sort_stats("tottime").print_stats(18)
    print("\nPERFIL (ordenado por tiempo PROPIO de cada función)")
    # Se recorta el encabezado de pstats y se deja la tabla.
    for linea in buf.getvalue().splitlines():
        if linea.strip():
            print("   " + linea)
    print("\n   Cómo leerlo: `tottime` = segundos quemados DENTRO de esa función")
    print("   (sin contar lo que llama). El de arriba es el que hay que mirar.")
    print("   `ncalls` enorme con tottime alto = se está llamando de más, no es")
    print("   que la función sea lenta.")
    return 0


def main() -> int:
    if "--profile" in sys.argv:
        crudo = getattr(_agro_sql.get_pase_agro, "__wrapped__", _agro_sql.get_pase_agro)
        crudo()          # calentar: la 1ª corrida paga imports y conexiones del pool
        return _profile(crudo)
    reps = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    # El endpoint tiene @cached(5s) y `functools.wraps` deja la función real en
    # __wrapped__: se mide SIEMPRE en frío, que es lo que paga el usuario cuando
    # el poll cae fuera de la ventana de cache (que es casi siempre).
    crudo = getattr(_agro_sql.get_pase_agro, "__wrapped__", _agro_sql.get_pase_agro)

    print("PIEZAS (cada una por separado, en serie)")
    piezas: list[tuple[str, float]] = []
    for _ in range(reps):
        piezas += [
            _cronometrar("agro_pizarra (SQL)", _agro_sql._rows, "agro_pizarra"),
            _cronometrar("agro_snapshot (SQL)", _agro_sql._rows, "agro_snapshot"),
            _cronometrar("camara_cereales", _cam.get_camara_cereales),
            _cronometrar("tasas_cobertura", _cam.get_tasas_cobertura),
            _cronometrar("dolar oficial live", mid_oficial_live, "oficial"),
        ]
    # Mejor caso por pieza: descarta el ruido de una corrida puntual.
    mejores: dict[str, float] = {}
    for label, ms in piezas:
        if ms >= 0:
            mejores[label] = min(mejores.get(label, 1e9), ms)
    for label, ms in mejores.items():
        print(f"   {label:<26} {ms:7.1f} ms")
    if not mejores:
        print("   (ninguna pieza pudo medirse)")
        return 1
    suma = sum(mejores.values())
    lenta = max(mejores.values())
    print(f"   {'—' * 34}")
    print(f"   {'suma (si fueran en serie)':<26} {suma:7.1f} ms")
    print(f"   {'la más lenta (piso real)':<26} {lenta:7.1f} ms   ← corren en paralelo")

    print("\nENDPOINT COMPLETO (sin cache)")
    totales = []
    for i in range(reps):
        t0 = time.perf_counter()
        crudo()
        t = _ms(t0)
        totales.append(t)
        print(f"   corrida {i + 1}: {t:7.1f} ms")
    total = min(totales)

    python_ms = total - lenta
    print(f"\nVEREDICTO (mejor corrida: {total:.1f} ms)")
    print(f"   I/O (la lectura más lenta) : {lenta:7.1f} ms   {lenta / total * 100:4.0f}%")
    print(f"   Python + armado            : {python_ms:7.1f} ms   {python_ms / total * 100:4.0f}%")
    if python_ms > lenta:
        print("\n   → MANDA PYTHON. No hay índice, cache de base ni región que")
        print("     ayude: el tiempo se va armando los bloques en memoria.")
        print("     Se ataca perfilando esa función, o cacheando el RESULTADO")
        print("     con un TTL alineado al poll del front (hoy 5s, y si el poll")
        print("     es más lento que eso, CADA request paga el precio completo).")
    else:
        print("\n   → MANDA LA LECTURA. Ahí sí conviene mirar esa consulta puntual")
        print("     (la más lenta de las 5) antes que cualquier otra cosa.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
