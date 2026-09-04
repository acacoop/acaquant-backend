"""`scripts/diag_caducidad.py` — QUÉ CADUCARÍA, sin escribir una sola fila.

**Read-only. No hace ni un `UPDATE`.** Existe por la REGLA #2: la caducidad
cierra hallazgos sola, y antes de dejarla suelta hay que poder mirar con datos
REALES a quién le pegaría — no confiar en que los tests de estructura pasan.

    python -m scripts.diag_caducidad            # el resumen
    python -m scripts.diag_caducidad --detalle  # fila por fila, con el motivo

QUÉ CONTESTA
============

    1. ¿Cuántos hallazgos ABIERTOS hay sobre sujetos que ya no existen?
    2. ¿Cuáles, y con qué fundamento y de qué fuente?
    3. ¿Alguna habilidad se pasaría del tope por corrida?
    4. ¿Cuántas REINCIDENCIAS quedan activas con el criterio nuevo?

⚠️ **Mira los ABIERTOS y el motor caduca los que NO VINIERON en la corrida.**
Son conjuntos distintos a propósito: acá se ve el universo COMPLETO de sujetos
muertos con hallazgo vivo —que es la pregunta «¿a quién le pegaría esto?»—,
mientras que el motor sólo toca los que además dejó de ver. O sea: **lo que
liste este diag es el TECHO, nunca menos que lo que va a pasar de verdad.**

Cuando el tema cierre, este archivo se borra (REGLA #5).
"""
from __future__ import annotations

import argparse
import sys
from collections import defaultdict

from agente import catalogo, tipos, vigencia
from core.postgres import get_pool


def _titulo(s: str) -> None:
    print(f"\n{'═' * 78}\n {s}\n{'═' * 78}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--detalle", action="store_true",
                    help="lista cada hallazgo con su motivo y su fuente")
    args = ap.parse_args()

    con_tipo = {n: h.sujeto_es for n, h in catalogo.HABILIDADES.items()
                if h.sujeto_es}
    _titulo("1. QUIÉN PUEDE CADUCAR — las habilidades que declaran `sujeto_es`")
    if not con_tipo:
        print("  ninguna. Sin declaración no se caduca nada: es el default seguro.")
        return 0
    for nombre, tipo in sorted(con_tipo.items()):
        print(f"  {nombre:<24} sujeto_es={tipo}")
    print(f"\n  Las otras {len(catalogo.HABILIDADES) - len(con_tipo)} no declaran "
          "tipo de sujeto y NO caducan nada.")

    with get_pool().connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT habilidad, sujeto, regla, estado, id "
                "  FROM agente.hallazgos "
                " WHERE estado = ANY(%s) AND habilidad = ANY(%s) "
                " ORDER BY habilidad, sujeto",
                (list(tipos.ABIERTOS), sorted(con_tipo)))
            abiertos = cur.fetchall()

        por_hab: dict[str, list] = defaultdict(list)
        for hab, suj, reg, est, hid in abiertos:
            por_hab[hab].append((suj, reg, est, hid))

        _titulo("2. QUÉ CADUCARÍA — sujetos verificados como MUERTOS")
        total_muertos = pasadas_de_tope = 0
        for hab, filas in sorted(por_hab.items()):
            # ⚠️ Se usa la MISMA función que el motor. Reimplementar el criterio
            # acá sería tener dos definiciones de «muerto» (REGLA #9): el diag
            # diría una cosa y el agente haría otra, y nadie se enteraría.
            muertos = vigencia.muertos(conn, con_tipo[hab], [f[0] for f in filas])
            n = sum(1 for f in filas if f[0] in muertos)
            total_muertos += n
            marca = ""
            if n > vigencia.TOPE_POR_CORRIDA:
                marca = f"  ⚠ SE PASA DEL TOPE ({vigencia.TOPE_POR_CORRIDA})"
                pasadas_de_tope += 1
            print(f"  {hab:<24} {n:>4} de {len(filas):>4} abiertos{marca}")
            if args.detalle:
                for suj, reg, est, hid in filas:
                    if (v := muertos.get(suj)):
                        print(f"      #{hid} {suj:<12} {reg:<22} [{est}] "
                              f"→ {v.motivo}  ({v.fuente})")

        print(f"\n  TOTAL que caducaría: {total_muertos}")
        if pasadas_de_tope:
            print(f"  ⚠ {pasadas_de_tope} habilidad(es) se pasan del tope. Eso NO "
                  "frena la corrida:\n    los que sobran cierran por AUSENCIA, "
                  "que dice menos pero no miente.\n    Si se repite, mirá la "
                  "FUENTE antes que al agente.")

        _titulo("3. LAS REINCIDENCIAS — cuántas quedan activas")
        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) FILTER (WHERE h.estado = ANY(%s)) AS activas, "
                "       count(*) AS totales "
                "  FROM agente.reincidencias r "
                "  JOIN agente.hallazgos h ON h.id = r.hallazgo_id",
                (list(tipos.ABIERTOS),))
            activas, totales = cur.fetchone()
        print(f"  activas (su hallazgo sigue abierto): {activas}")
        print(f"  en la tabla, históricas incluidas:   {totales}")
        print("\n  La fila nunca se borra: «el arreglo X aguantó N días» es la "
              "evidencia\n  con la que después se decide qué arreglo es confiable. "
              "Lo que se apaga\n  es la ALARMA, no el hecho.")

    print("\n✅ Read-only: no se escribió nada.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
