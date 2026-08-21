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

# ⚠️ **LA CLAVE, EN SQL, ESCRITA UNA SOLA VEZ.** La usan el UPDATE (que la
# persiste) y el PREVIEW (que la calcula al vuelo para poder mostrar algo sin
# escribir). Si fueran dos expresiones, el dry-run mostraría una migración y el
# `--aplicar` haría otra — REGLA #9 adentro de un mismo script.
#
# Tiene que dar EXACTAMENTE lo mismo que `core.ciclo.clave_de`: minúsculas,
# unidas por `|`, salteando las partes vacías.
_CLAVE_SQL = """array_to_string(array_remove(ARRAY[
    nullif(lower(btrim(tipo)), ''),
    nullif(lower(btrim(coalesce(alcance, ''))), ''),
    nullif(lower(btrim(coalesce(ticker, ''))), ''),
    nullif(lower(btrim(coalesce(regla, ''))), '')
], NULL), '|')"""


def main() -> int:
    aplicar = "--aplicar" in sys.argv
    from core import ciclo
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
            cur.execute(f"UPDATE mercado.av_agent_hallazgos SET clave = {_CLAVE_SQL} "
                        " WHERE clave IS NULL")
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
        # ⚠️ **LA CLAVE SE CALCULA AL VUELO, no se lee de la columna.**
        #
        # La primera versión filtraba por `clave IS NOT NULL` — y como la
        # columna recién se llena en el paso 1, que solo corre con `--aplicar`,
        # **el dry-run devolvía 0 hallazgos**. Eso no era «no hay nada que
        # migrar»: era «no pude mirar», mostrado como un cero.
        #
        # Es la misma regla que el agente aplica a sus detectores y que acá me
        # comí: *«no pude» no es «no existe»*. Un preview que no puede
        # previsualizar sin escribir primero no es un preview.
        cur.execute(f"""
            WITH todo AS (
                SELECT {_CLAVE_SQL} AS k, corrida_at, tipo, alcance, ticker,
                       regla, severidad, motivo, evidencia
                  FROM mercado.av_agent_hallazgos
            ), ultima AS (
                SELECT DISTINCT ON (k) k, tipo, alcance, ticker, regla,
                       severidad, motivo, evidencia
                  FROM todo WHERE corrida_at = %s
            ), historia AS (
                SELECT k, min(corrida_at) AS desde,
                       count(DISTINCT corrida_at) AS corridas
                  FROM todo GROUP BY k
            )
            SELECT u.k, u.tipo, u.alcance, u.ticker, u.regla, u.severidad,
                   u.motivo, u.evidencia, h.desde, h.corridas
              FROM ultima u JOIN historia h USING (k)
             ORDER BY h.desde
        """, (corrida,))
        filas = cur.fetchall()

    print(f"\n  corrida vigente       {corrida:%d/%m %H:%M}")
    print(f"  hallazgos a migrar    {len(filas)}")
    if not filas:
        print()
        return 0

    # ⚠️⚠️ **LAS DOS CLAVES TIENEN QUE DAR LO MISMO.** Acá conviven dos
    # implementaciones de la identidad: la de SQL (`_CLAVE_SQL`, que este script
    # necesita para poder mirar el histórico) y la de Python
    # (`ciclo.clave_de`, la que usan el detector y la pantalla).
    #
    # Si difirieran, el backfill crearía items con una clave que **nadie va a
    # buscar nunca**: la memoria quedaría escrita y para siempre inalcanzable,
    # sin un solo error. Es REGLA #9 en su forma más cara, y por eso no alcanza
    # con "tener cuidado": se comparan las dos, fila por fila, y si una sola no
    # coincide **no se escribe nada**.
    distintas = [(f[0], ciclo.clave_de(f[1], f[2] or "", f[3], f[4]))
                 for f in filas
                 if ciclo.clave_de(f[1], f[2] or "", f[3], f[4]) != f[0]]
    if distintas:
        print(f"\n  ✖ ABORTADO: {len(distintas)} claves no coinciden entre SQL y "
              f"Python. Escribirlas dejaría la memoria inalcanzable.")
        for sql_k, py_k in distintas[:5]:
            print(f"      SQL: {sql_k!r}\n      PY : {py_k!r}")
        return 1
    print(f"  ✔ las {len(filas)} claves coinciden entre SQL y Python")

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
