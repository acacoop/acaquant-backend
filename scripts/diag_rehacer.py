"""¿FALTA EL DÍA? — y rehacerlo, pero solo si falta de verdad.

    python -m scripts.diag_rehacer                    # solo MIRA
    python -m scripts.diag_rehacer --rehacer          # ejecuta lo que falte

Nace del 2026-08-20: Aunesa devolvió HTTP 500 a las 11:00, `jobs/aum` murió y
**el AuM del día no se escribió**. Nadie se enteró hasta que estábamos mirando
otra cosa.

La condición que lo hace seguro la puso el user: *«ejecutar fecha de hoy por
haber detectado un error Y haber verificado 100% en la base que no hay fecha
realmente»*. No se relanza porque el job falló — se relanza porque **se miró la
tabla y el dato no está**. Un job puede fallar habiendo escrito, y puede salir en
verde sin escribir nada.

Sin `--rehacer` NO ejecuta nada: solo dice qué falta.
"""
from __future__ import annotations

import sys


def main() -> int:
    from api.services import av_agent_rehacer as reh

    ejecutar = "--rehacer" in sys.argv

    print("═" * 74)
    print("  ¿ESTÁ EL DÍA?"
          + ("   (modo REHACER)" if ejecutar else "   (solo mira)"))
    print("═" * 74)

    # ⚠️ **La fecha NO la elige el diag.** Cada job declara qué día le toca
    # (`--diario` escribe el hábil ANTERIOR, no hoy) y lo resuelve
    # `av_agent_rehacer` con el mismo reloj que usa el job. Si el que pregunta
    # calculara su propia fecha, un día se desfasarían y las dos mitades
    # seguirían siendo coherentes consigo mismas (REGLA #9).
    falta: list[tuple[str, str]] = []
    for job, cfg in reh.REHACIBLES.items():
        fecha = reh.fecha_objetivo(job)
        if not fecha:
            print(f"\n  ?  {cfg['titulo']:<30} no sé qué día le toca")
            continue
        hay = reh.hay_dato(job, fecha)
        if hay is None:
            print(f"\n  ?  {cfg['titulo']:<30} no pude consultar {cfg['tabla']}")
            continue
        if hay:
            print(f"\n  ✔  {cfg['titulo']:<30} {cfg['tabla']} tiene {fecha}")
            continue
        falta.append((job, fecha))
        print(f"\n  ✖  {cfg['titulo']:<30} {cfg['tabla']} NO tiene {fecha}")
        print(f"       {cfg['rompe']}")

    if not falta:
        print("\n  Nada que rehacer.\n")
        return 0
    if not ejecutar:
        print(f"\n  → {len(falta)} sin dato. Para rehacerlos:"
              f"  python -m scripts.diag_rehacer --rehacer\n")
        return 0

    for job, fecha in falta:
        print(f"\n── rehaciendo {job} · {fecha} …  (lock + timeout, puede tardar)")
        r = reh.rehacer(job, fecha, por="diag")
        marca = "✔" if r.get("ok") else "✖"
        print(f"  {marca} {r.get('detalle') or r.get('error')}")
        if r.get("salida"):
            print("     " + r["salida"].replace("\n", "\n     "))
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
