"""scripts/fix_dato_partido.py — SINCRONIZAR dos copias del mismo dato.

Doc madre: `docs/AGENT.md` §0.aa · `CLAUDE.md` REGLA #9.
**DRY-RUN por default** (REGLA #4). Idempotente: correrlo dos veces no hace nada
la segunda, porque el WHERE del arreglo es el mismo que el de la detección.

QUÉ ARREGLA Y QUÉ NO
====================

Solo los duplicados que declaran un `arreglo_sql` en `core/duplicados`. Los otros
**no se tocan a propósito** y el script dice por qué: sincronizar dos copias
parece siempre lo mismo y no lo es. Cuando el árbitro es un JOB, escribir a mano
deja las copias coincidiendo en un valor que ninguna fuente respalda —peor que la
divergencia, porque además la esconde—; y cuando el cambio no surte efecto hasta
reiniciar un motor, el UPDATE se verifica en verde y en la pantalla no cambia
nada.

POR QUÉ ESTO NO ES UNA ACCIÓN DEL AGENTE
=========================================

Es la decisión de §0.aa y se sostiene: **pisar la copia «mala» borra la evidencia
de que hubo una divergencia**, y puede que la equivocada sea la del árbitro. El
agente muestra los dos valores; elegir es de una persona. Por eso esto es un
script con dry-run y no un botón.

Uso:
    python -m scripts.fix_dato_partido                    # DRY-RUN, muestra todo
    python -m scripts.fix_dato_partido --aplicar          # escribe
    python -m scripts.fix_dato_partido --aplicar --solo simbolo_columna_vs_blob
"""
from __future__ import annotations

import argparse

from core import duplicados as D
from core.postgres import get_pool


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--aplicar", action="store_true",
                    help="escribe (por default solo muestra)")
    ap.add_argument("--solo", default="", help="un id de `core.duplicados`")
    args = ap.parse_args()

    res = D.divergencias()
    partidos = res.get("partidos") or []
    if res.get("sin_mirar"):
        print("\n  ⚠️  NO se pudieron chequear (no es que estén bien — es que no "
              "se miraron):")
        for x in res["sin_mirar"]:
            print(f"     · {x['id']}: {x['error']}")

    if not partidos:
        print("\n  ✔ todas las copias coinciden. Nada que hacer.\n")
        return 0

    por_id = {d.id: d for d in D.DUPLICADOS}
    total = 0
    for p in partidos:
        if args.solo and p["id"] != args.solo:
            continue
        d = por_id[p["id"]]
        print(f"\n{'=' * 78}\n{p['id']}  —  {p['n']} caso(s)\n{'=' * 78}")
        print(f"  qué es    : {p['que']}")
        print(f"  copia A   : {p['a']}")
        print(f"  copia B   : {p['b']}")
        print(f"  MANDA     : {p['arbitro']}")
        print(f"  qué rompe : {p['rompe']}\n")
        for x in p["ejemplos"]:
            print(f"    {x['sujeto']:<12} A=«{x['valor_a']}»   B=«{x['valor_b']}»")
        if p["n"] > len(p["ejemplos"]):
            print(f"    … y {p['n'] - len(p['ejemplos'])} más")

        if not d.arreglo_sql:
            print(f"\n  ✖ SIN ARREGLO MECÁNICO — no se toca.\n    {d.arreglo_manual}")
            continue
        if not args.aplicar:
            print(f"\n  → con --aplicar se le escribe a la copia B el valor del "
                  f"árbitro.\n    Scopeado a esas {p['n']} fila(s); las que ya "
                  f"coinciden no se tocan.")
            continue
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(d.arreglo_sql)
            n = cur.rowcount
            conn.commit()
        total += n
        print(f"\n  ✔ {n} fila(s) sincronizada(s)")

    if args.aplicar:
        # Se RELEE. «Apliqué» no es «pasó»: el mismo criterio que verifica
        # cualquier acción del agente.
        quedan = {x["id"]: x["n"] for x in (D.divergencias().get("partidos") or [])}
        print(f"\n{'=' * 78}\n✔ {total} fila(s) escritas. Relectura:")
        for d in D.DUPLICADOS:
            n = quedan.get(d.id, 0)
            estado = "✔ coinciden" if not n else (
                f"✖ siguen {n} (sin arreglo mecánico)" if not d.arreglo_sql
                else f"✖ SIGUEN {n} — el arreglo no alcanzó")
            print(f"    {d.id:<32} {estado}")
        print()
    else:
        print(f"\n{'=' * 78}\n  DRY-RUN. Nada se escribió. Para aplicar: "
              f"--aplicar\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
