"""scripts/reset_evals.py — BORRAR EL EVAL SET ENTERO Y EMPEZAR DE CERO.

Decisión del user, 2026-08-23: *«hay que borrar absolutamente todos los valores
de votos y eso, es algo que no tiene valor a este momento porque nunca funcionó
bien esto, hay que reemplazar eso por algún valor neutro, porque sinceramente no
es fiable nada absolutamente nada, está muy verde esto»*.

POR QUÉ ES LO CORRECTO Y NO UNA CIRUGÍA FINA
============================================

Se pensó primero en anular solo los 11 ✖ del 22/08 23:50. Es peor: deja una
tabla donde una parte se limpió y otra no, y nadie va a poder decir después qué
voto vale. Lo que hay medido es que **el mecanismo no distinguía «alguien lo
arregló» de «el detector dejó de verlo»**:

    resuelto  ← `_cerrar_ausentes` cierra lo que la corrida no vio
    volvio    ← el detector lo ve otra vez
    ✖         ← `cerrar_hitos` vota «el arreglo no aguantó»  ...sobre un
                arreglo que nadie hizo

Con esa confusión adentro, **ningún voto de esta tabla es interpretable** —ni el
✖ fabricado ni el ✔ que convivió con él— porque no se sabe cuál se emitió con la
máquina rota. Un eval set en el que hay que aclarar «estos sí y estos no» no es
un eval set: es una nota al pie.

QUÉ PASA DESPUÉS
================

El badge de confianza de cada fila (`3/12`) **desaparece solo**: el front lo
esconde cuando no hay votos humanos (`tab-hallazgos.tsx:1085`,
`if (!c || !c.humanos) return null`). Ése es el «valor neutro» pedido, y ya está
codeado — no hay nada que tocar del lado de la pantalla.

SEGURIDAD (REGLA #4)
====================

**Borrar datos no se revierte, borrar código sí** (CLAUDE.md). Por eso:

  · **DRY-RUN por default.** Sin `--aplicar` no escribe una sola fila.
  · **BACKUP A CSV ANTES DE BORRAR**, en el mismo comando y abortando si el
    archivo no se pudo escribir. El borrado no ocurre sin respaldo en disco.
  · **Idempotente**: correrlo dos veces borra 0 filas la segunda.
  · Es una tabla chica (cientos de filas) — no hay riesgo de scan largo.

USO
===

    python -m scripts.reset_evals              # muestra qué borraría
    python -m scripts.reset_evals --aplicar    # respalda a CSV y borra
"""
from __future__ import annotations

import csv
import sys
from datetime import UTC, datetime
from pathlib import Path

from core.postgres import get_pool

LINEA = "─" * 78
DESTINO = Path("/root/TradingAV/backups")


def main() -> int:
    aplicar = "--aplicar" in sys.argv
    print(f"{LINEA}\nRESET DEL EVAL SET  ·  "
          f"{'APLICA (borra)' if aplicar else 'DRY-RUN (no toca nada)'}\n{LINEA}")

    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT origen, count(*), count(*) FILTER (WHERE acierta) "
                    "FROM agente.av_agent_evals GROUP BY origen ORDER BY 2 DESC")
        filas = cur.fetchall()
        total = sum(f[1] for f in filas)
        if not total:
            print("\n  La tabla ya está vacía. No hay nada que hacer.")
            return 0

        print(f"\n{'ORIGEN':<16}{'VOTOS':>8}{'✔':>6}")
        for o, n, ok in filas:
            print(f"{(o or '—'):<16}{n:>8}{ok:>6}")
        print(f"{'TOTAL':<16}{total:>8}")

        if not aplicar:
            print("\n  DRY-RUN: no se borró nada.")
            print("  Para hacerlo de verdad:  python -m scripts.reset_evals "
                  "--aplicar")
            return 0

        # ── EL RESPALDO VA PRIMERO Y ES BLOQUEANTE ──────────────────────────
        # Si el CSV no se puede escribir, NO se borra. Un backup que falla en
        # silencio es peor que no tenerlo: da la confianza sin el respaldo.
        cur.execute("SELECT * FROM agente.av_agent_evals ORDER BY id")
        cols = [d.name for d in cur.description]
        datos = cur.fetchall()
        try:
            DESTINO.mkdir(parents=True, exist_ok=True)
            sello = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
            ruta = DESTINO / f"av_agent_evals_{sello}.csv"
            with ruta.open("w", newline="", encoding="utf-8") as fh:
                w = csv.writer(fh)
                w.writerow(cols)
                w.writerows(datos)
            print(f"\n  ✔ respaldo: {ruta}  ({len(datos)} filas)")
        except Exception as e:
            print(f"\n  ✖ NO se pudo respaldar ({e}). No se borra nada.")
            return 1

        cur.execute("DELETE FROM agente.av_agent_evals")
        borradas = cur.rowcount or 0
        conn.commit()
        print(f"  ✔ borradas: {borradas} filas")

    print(f"\n{LINEA}")
    print("  El badge de confianza de cada fila desaparece solo (el front lo")
    print("  esconde sin votos humanos). El eval set arranca de cero.")
    print(LINEA)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
