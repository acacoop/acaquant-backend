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
# ⚠️ **(SUJETO, CAUSA)** — ni el tipo ni el alcance entran: son QUIÉN lo vio, no
# qué problema es (§0.bj). Si el ticker viene vacío, el alcance vuelve como
# respaldo para que los sin-sujeto no colapsen todos en uno.
#
# La normalización de la causa (`patas_equivocadas` → `pata_equivocada`) NO se
# hace acá: se compara contra Python fila por fila y las que difieren abortan la
# corrida. Un backfill no puede tener su propia opinión sobre la identidad.
_CLAVE_SQL = """array_to_string(array_remove(ARRAY[
    coalesce(nullif(lower(btrim(coalesce(ticker, ''))), ''),
             nullif(lower(btrim(coalesce(alcance, ''))), '')),
    nullif(lower(btrim(coalesce(regla, ''))), '')
], NULL), '|')"""


def main() -> int:
    aplicar = "--aplicar" in sys.argv
    from core.postgres import get_pool

    print("═" * 74)
    print("  BACKFILL DE ITEMS — la antigüedad real, leída del histórico")
    print(f"  modo: {'APLICAR (escribe)' if aplicar else 'solo mira'}")
    print("═" * 74)

    with get_pool().connection() as conn, conn.cursor() as cur:
        # 0) ⚠️ **LOS ITEMS QUE YA EXISTEN, RE-IDENTIFICADOS.**
        #
        # La primera corrida de este backfill creó los items con la identidad
        # VIEJA (`tipo|origen|sujeto|regla`). Al pasar a `(sujeto, causa)`
        # (§0.bj) esas filas quedan con una clave **que nadie va a buscar
        # nunca**: la memoria sigue en la base y es inalcanzable — el modo de
        # falla que este mismo script se cuida de no provocar.
        #
        # No se BORRAN (ahí sí se perdería la antigüedad real, que es lo único
        # que este script vino a rescatar): se les recalcula la clave desde sus
        # PROPIAS columnas. Si al hacerlo dos filas caen en la misma identidad
        # —que es justo lo que la migración busca: el del detector y el del
        # control eran el mismo problema— se FUSIONAN quedando la fecha más
        # vieja y la suma de las veces.
        _reidentificar(cur, aplicar)

        # 1) la clave de los hallazgos, recalculada TAMBIÉN cuando está vieja
        # (no solo cuando falta): las filas que escribió el detector antes del
        # cambio tienen la clave anterior y el `LEFT JOIN` de la pantalla no
        # las encontraría.
        cur.execute(f"SELECT count(*) FROM mercado.av_agent_hallazgos "
                    f"WHERE clave IS NULL OR clave <> {_CLAVE_SQL}")
        sin_clave = cur.fetchone()[0]
        print(f"\n  hallazgos con clave vieja o sin clave   {sin_clave}")
        if sin_clave and aplicar:
            cur.execute(f"UPDATE mercado.av_agent_hallazgos SET clave = {_CLAVE_SQL} "
                        f" WHERE clave IS NULL OR clave <> {_CLAVE_SQL}")
            print(f"  → reescritas          {cur.rowcount}")

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
    from api.services.av_agent_items import clave_de_problema
    distintas = [(f[0], clave_de_problema(f[3], f[4], f[2] or ""))
                 for f in filas
                 if clave_de_problema(f[3], f[4], f[2] or "") != f[0]]
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
            [(clave_de_problema(f[3], f[4], f[2] or ""), f[1], f[2] or "censo",
              f[3], f[4], f[5], f[9], f[8],
              corrida, f[6] or "",
              json.dumps(f[7] or {}, ensure_ascii=False, default=str))
             for f in filas])
        n = cur.rowcount
    print(f"\n  ✔ {len(filas)} procesados · {n} items creados "
          f"(el resto ya existía y NO se pisó)\n")
    return 0


def _reidentificar(cur, aplicar: bool) -> None:
    """Los items existentes, a la identidad canónica — fusionando los repetidos.

    Es idempotente: una fila que ya tiene la clave buena no aparece en el
    conteo y no se toca.
    """
    from api.services.av_agent_items import clave_de_problema

    cur.execute("SELECT clave, sujeto, regla, origen FROM mercado.av_agent_items")
    filas = cur.fetchall()
    cambios = [(vieja, clave_de_problema(suj, reg, org or ""))
               for vieja, suj, reg, org in filas]
    cambios = [(v, n) for v, n in cambios if n and n != v]
    print(f"\n  items con identidad vieja   {len(cambios)} de {len(filas)}")
    if not cambios:
        return
    for v, n in cambios[:5]:
        print(f"      {v}\n         →  {n}")
    if not aplicar:
        return

    # El UPDATE puede chocar con una fila que YA tiene la clave nueva (los dos
    # objetos del mismo problema). Ese choque es el ÉXITO de la migración, no
    # un error: se fusiona a mano quedándose con lo más conservador de cada uno
    # —la fecha de apertura más VIEJA (la antigüedad real) y la SUMA de veces— y
    # recién ahí se borra el duplicado.
    movidos = fusionados = 0
    for vieja, nueva in cambios:
        cur.execute("SELECT 1 FROM mercado.av_agent_items WHERE clave = %s",
                    (nueva,))
        if cur.fetchone():
            cur.execute(
                "UPDATE mercado.av_agent_items d SET "
                "  abierto_at = LEAST(d.abierto_at, o.abierto_at), "
                "  ultimo_at  = GREATEST(d.ultimo_at, o.ultimo_at), "
                "  veces      = d.veces + o.veces, "
                "  visto_at   = LEAST(d.visto_at, o.visto_at), "
                # ⚠️ **GANA EL MÁS ABIERTO.** Si uno de los dos lo daba por
                # resuelto y el otro no, el problema NO está resuelto: cerrarlo
                # acá sería hacer desaparecer de la pantalla algo que sigue
                # pasando, en silencio y por una migración.
                "  estado = CASE WHEN o.estado NOT IN ('resuelto','ignorado') "
                "                 AND d.estado IN ('resuelto','ignorado') "
                "                THEN o.estado ELSE d.estado END, "
                "  resuelto_at = CASE WHEN o.estado NOT IN ('resuelto','ignorado') "
                "                THEN NULL ELSE d.resuelto_at END "
                "FROM mercado.av_agent_items o "
                "WHERE d.clave = %s AND o.clave = %s", (nueva, vieja))
            cur.execute("DELETE FROM mercado.av_agent_items WHERE clave = %s",
                        (vieja,))
            fusionados += 1
        else:
            cur.execute("UPDATE mercado.av_agent_items SET clave = %s "
                        " WHERE clave = %s", (nueva, vieja))
            movidos += 1
    print(f"  → {movidos} re-identificados · {fusionados} FUSIONADOS "
          f"(eran dos objetos del mismo problema)")


def _dias(ts) -> float:
    from core.ciclo import _dias as d
    return d(ts)


if __name__ == "__main__":
    raise SystemExit(main())
