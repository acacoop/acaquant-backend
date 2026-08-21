"""LA MEMORIA QUE YA ESTABA EN LA BASE, RECUPERADA.

    python -m scripts.backfill_items            # solo MIRA (dry-run)
    python -m scripts.backfill_items --aplicar

Los hallazgos vivían como FOTO: una fila por corrida, sin identidad. Al pasarlos
a objetos (`mercado.av_agent_items`, §0.bd) la tentación es sellar todo con
`abierto_at = ahora` — y sería **mentira**: un problema que lleva tres semanas
aparecería como recién nacido, que es exactamente el defecto que la migración
vino a arreglar.

**Pero la antigüedad ya está en la base.** La tabla conserva las últimas 60
corridas, así que la PRIMERA corrida en que aparece cada clave ES desde cuándo
está abierto, y cuántas corridas lo repiten ES el `veces`. La memoria no hay que
inventarla: hay que leerla.

QUÉ HACE, EN ORDEN
==================

  1. completa `clave` en las filas viejas que no la tienen (la columna es nueva);
  2. crea el item de cada clave **de la ÚLTIMA corrida**, con:
       · `abierto_at` = la primera vez que se vio esa clave en el histórico
       · `veces`      = en cuántas corridas distintas apareció
  3. **NO cierra nada.** Una clave que está en el histórico y no en la última
     corrida puede haberse arreglado o puede no haberse evaluado, y desde acá no
     hay forma de distinguirlo. Cerrar sería inventar «se arreglaron N» — la
     mentira más cara de una herramienta de integridad. Los cierra la próxima
     corrida real, que sí sabe qué miró.

REGLA #4: idempotente (`ON CONFLICT DO NOTHING` — no pisa lo que ya vive), sin
escanear a ciegas (trabaja sobre las corridas guardadas, ~60) y sin `--aplicar`
no escribe una sola fila.
"""
from __future__ import annotations

import sys


def main() -> int:
    aplicar = "--aplicar" in sys.argv
    from core.postgres import get_pool

    print("═" * 74)
    print("  BACKFILL DE ITEMS — la antigüedad real, leída del histórico")
    print(f"  modo: {'APLICAR (escribe)' if aplicar else 'solo mira'}")
    print("═" * 74)

    with get_pool().connection() as conn, conn.cursor() as cur:
        # 1) la clave que falta en las filas viejas
        cur.execute("SELECT count(*) FROM mercado.av_agent_hallazgos "
                    "WHERE clave IS NULL")
        sin_clave = cur.fetchone()[0]
        print(f"\n  filas sin clave       {sin_clave}")
        if sin_clave and aplicar:
            # Se calcula en SQL SOLO acá y para lo viejo: es un one-shot y tiene
            # que dar lo mismo que `ciclo.clave_de` (lower + '|' + sin vacíos).
            cur.execute("""
                UPDATE mercado.av_agent_hallazgos
                   SET clave = array_to_string(array_remove(ARRAY[
                           nullif(lower(btrim(tipo)), ''),
                           nullif(lower(btrim(coalesce(alcance, ''))), ''),
                           nullif(lower(btrim(coalesce(ticker, ''))), ''),
                           nullif(lower(btrim(coalesce(regla, ''))), '')
                       ], NULL), '|')
                 WHERE clave IS NULL""")
            print(f"  → completadas         {cur.rowcount}")

        # La corrida vigente (misma regla que la vista: sin los de reemplazo)
        # Los alcances de REEMPLAZO salen de la constante del agente, no de una
        # copia acá: si divergieran, el backfill elegiría otra corrida que la
        # pantalla.
        from api.services.av_agent import ALCANCES_VIVOS
        vivos = list(ALCANCES_VIVOS)
        cur.execute("SELECT max(corrida_at) FROM mercado.av_agent_hallazgos "
                    "WHERE alcance <> ALL(%s)", (vivos,))
        corrida = (cur.fetchone() or [None])[0]
        if corrida is None:
            print("\n  No hay ninguna corrida guardada: nada que recuperar.\n")
            return 0

        # 2) la antigüedad REAL de cada clave de la última corrida
        cur.execute("""
            WITH ultima AS (
                SELECT DISTINCT clave, tipo, alcance, ticker, regla, severidad,
                       motivo, evidencia
                  FROM mercado.av_agent_hallazgos
                 WHERE corrida_at = %s AND clave IS NOT NULL
            ), historia AS (
                SELECT clave, min(corrida_at) AS desde,
                       count(DISTINCT corrida_at) AS corridas
                  FROM mercado.av_agent_hallazgos
                 WHERE clave IS NOT NULL
                 GROUP BY clave
            )
            SELECT u.clave, u.tipo, u.alcance, u.ticker, u.regla, u.severidad,
                   u.motivo, u.evidencia, h.desde, h.corridas
              FROM ultima u JOIN historia h USING (clave)
             ORDER BY h.desde
        """, (corrida,))
        filas = cur.fetchall()

    print(f"\n  corrida vigente       {corrida:%d/%m %H:%M}")
    print(f"  hallazgos a migrar    {len(filas)}")
    if not filas:
        print()
        return 0

    viejos = [f for f in filas if _dias(f[8]) >= 7]
    print(f"  …de esos, con MÁS DE 7 DÍAS abiertos: {len(viejos)}")
    print("\n  los 10 más antiguos (esta es la memoria que hoy no se ve):")
    for f in filas[:10]:
        print(f"    {f[3][:22]:<22} {f[4][:24]:<24} "
              f"{_dias(f[8]):>5.0f}d · {f[9]} corridas")

    if not aplicar:
        print("\n  → nada escrito. Para aplicarlo:"
              "  python -m scripts.backfill_items --aplicar\n")
        return 0

    import json
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO mercado.av_agent_items "
            "(clave, tipo, origen, sujeto, regla, estado, severidad, veces, "
            " abierto_at, ultimo_at, titulo, datos) "
            "VALUES (%s,%s,%s,%s,%s,'nuevo',%s,%s,%s,%s,%s,%s::jsonb) "
            # No pisa lo que ya vive: si la corrida real ya creó el item, ese
            # tiene la verdad más fresca.
            "ON CONFLICT (clave) DO NOTHING",
            [(f[0], f[1], f[2] or "censo", f[3], f[4], f[5], f[9], f[8],
              corrida, f[6] or "",
              json.dumps(f[7] or {}, ensure_ascii=False, default=str))
             for f in filas])
        n = cur.rowcount
    print(f"\n  ✔ {len(filas)} procesados · {n} items creados "
          f"(el resto ya existía y NO se pisó)\n")
    return 0


def _dias(ts) -> float:
    from core.ciclo import _dias as d
    return d(ts)


if __name__ == "__main__":
    raise SystemExit(main())
