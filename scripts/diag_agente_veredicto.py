"""scripts/diag_agente_veredicto.py — ¿EL VEREDICTO DE UN ARREGLO DICE LA VERDAD?

Doc: `docs/AV_AGENT.md`.

QUÉ CONTESTA
============

El eval set tiene 9 votos ✖ sobre `sin_tea_con_precio` con la nota *«el arreglo
no aguantó: el problema volvió a aparecer»*. Ese voto entra como `verificado`,
que la compuerta de autonomía cuenta **a la par de un voto humano**
(`av_agent_evals.py:284`). O sea: es el voto más caro del sistema.

Leyendo el código quedaron TRES sospechas que solo la base puede confirmar o
tirar abajo. Ninguna se puede decidir sin estos números.

    1. ¿EL ARREGLO TUVO CHANCE DE FUNCIONAR?
       `cerrar_hitos` vota ✖ apenas el objeto pasa a estado `volvio`
       (`av_agent_items.py:648`), y un objeto vuelve cuando el detector lo ve
       otra vez esa misma noche. Pero `motor_curvas` **arma su universo al
       arrancar** y `deploy.sh` **no reinicia motores** (CLAUDE.md): un arreglo
       de bono puede no verse hasta el próximo reinicio fuera de rueda.
       Si el tiempo entre «lo arreglé» y «volvió» es ~1 día en casi todos, el
       arreglo nunca llegó a aplicarse y el ✖ está midiendo otra cosa.

    2. ¿PUEDE EXISTIR SIQUIERA UN ✔?
       El ✔ `verificado` solo se emite al pasar el ÚLTIMO hito: **30 días**
       (`ciclo.HITOS_DIAS`). Si el objeto más viejo tiene menos que eso, el
       mecanismo es HOY incapaz de emitir un voto positivo — y entonces el
       100% de lo que produce es negativo por construcción, no por calidad.

    3. ¿LOS DOS MECANISMOS SE CONTRADICEN?
       Hay DOS escritores de `verificado`, declarados y conviviendo a propósito
       (`jobs/seguimiento.py:100`: *«los dos miden lo mismo por caminos
       distintos y conviven hasta que el viejo se apague»*):
         · `av_agent_items.cerrar_hitos`   → ventana 30 d, ref `volvio:`/`aguanto:`
         · `av_agent_seguimiento.cerrar`   → ventana  5 d, ref `seguimiento:`
       Ninguno de los dos está declarado en `core/duplicados.DUPLICADOS` (hay 5
       duplicados declarados y este no es ninguno). REGLA #9(B) en estado puro.

SEGURIDAD (REGLA #4)
====================

**100% SOLO LECTURA.** Cero INSERT/UPDATE/DELETE. Seis queries agregadas sobre
el schema `agente`, que es chico. No toca `mercado.*` ni ninguna tabla de rueda:
se puede correr con el mercado abierto.

USO
===

    python -m scripts.diag_agente_veredicto

Sin argumentos. Copiar la salida entera y pegarla.
"""
from __future__ import annotations

from core.postgres import get_pool

LINEA = "─" * 78


def _titulo(n: int, t: str) -> None:
    print(f"\n{LINEA}\n{n}. {t}\n{LINEA}")


def _filas(cur, sql: str, args: tuple = ()) -> list[tuple]:
    """Nunca tumba el diag: si una tabla no está, se dice y se sigue."""
    try:
        # ⚠️ `args or None` y NO `args`: con una tupla VACÍA psycopg igual parsea
        # placeholders, y entonces el `%` de un `LIKE 'volvio:%'` revienta con
        # «only '%s', '%b', '%t' are allowed». Sin args, el SQL va literal.
        cur.execute(sql, args or None)
        return cur.fetchall()
    except Exception as e:                      # es un diag: nunca aborta
        cur.connection.rollback()
        print(f"   (no se pudo leer: {str(e).strip()[:140]})")
        return []


def main() -> int:
    with get_pool().connection() as conn, conn.cursor() as cur:

        # ── 1. LOS `verificado`, SEPARADOS POR MECANISMO ────────────────────
        # El prefijo del `ref` dice cuál de los dos lo escribió. Sin separarlos
        # se lee un solo porcentaje que en realidad son dos poblaciones.
        _titulo(1, "LOS VOTOS `verificado`, POR MECANISMO (lo dice el `ref`)")
        filas = _filas(cur, """
            SELECT CASE
                     WHEN ref LIKE 'volvio:%'      THEN 'hitos · volvió'
                     WHEN ref LIKE 'aguanto:%'     THEN 'hitos · aguantó'
                     WHEN ref LIKE 'seguimiento:%' THEN 'tabla vieja (5 días)'
                     ELSE coalesce(nullif(ref, ''), '(sin ref)')
                   END AS mecanismo,
                   count(*)                                AS votos,
                   count(*) FILTER (WHERE acierta)         AS ok,
                   count(*) FILTER (WHERE NOT acierta)     AS mal,
                   min(creado_at)::date                    AS desde,
                   max(creado_at)::date                    AS hasta
              FROM agente.av_agent_evals
             WHERE origen = 'verificado'
             GROUP BY 1 ORDER BY count(*) DESC
        """)
        if filas:
            print(f"{'MECANISMO':<26}{'VOTOS':>7}{'✔':>5}{'✖':>5}"
                  f"{'DESDE':>12}{'HASTA':>12}")
            for m, n, ok, mal, d, h in filas:
                print(f"{m:<26}{n:>7}{ok:>5}{mal:>5}{d!s:>12}{h!s:>12}")
            print("\n   → si TODO el ✖ viene de un solo mecanismo, el problema")
            print("     es de ESE mecanismo y no del diagnóstico del agente")
        else:
            print("   (no hay votos `verificado`)")

        # ── 2. ¿CUÁNTO AGUANTÓ ANTES DE «VOLVER»? ───────────────────────────
        #
        # ⚠️ LA PREGUNTA QUE DECIDE TODO. Si la mayoría vuelve en menos de ~36 h,
        # volvió en la PRIMERA corrida del detector posterior al arreglo: nunca
        # llegó a estar arreglado en el mundo, y el ✖ no mide el diagnóstico.
        _titulo(2, "¿CUÁNTO PASÓ ENTRE «LO ARREGLÉ» Y «VOLVIÓ»?")
        filas = _filas(cur, """
            WITH v AS (
              SELECT regla, sujeto,
                     extract(epoch FROM (vuelto_at - resuelto_at)) / 3600.0 AS horas
                FROM agente.av_agent_items
               WHERE estado = 'volvio'
                 AND resuelto_at IS NOT NULL AND vuelto_at IS NOT NULL
            )
            SELECT CASE
                     WHEN horas <  36 THEN 'a) menos de 36 h  (la corrida siguiente)'
                     WHEN horas < 24*3 THEN 'b) 1 a 3 días'
                     WHEN horas < 24*7 THEN 'c) 3 a 7 días'
                     ELSE                   'd) más de 7 días'
                   END AS ventana,
                   count(*), round(min(horas)::numeric, 1),
                   round(max(horas)::numeric, 1)
              FROM v GROUP BY 1 ORDER BY 1
        """)
        tot = sum(f[1] for f in filas)
        for w, n, mn, mx in filas:
            print(f"   {w:<42}{n:>5}   ({mn}h – {mx}h)")
        if tot:
            corto = sum(n for w, n, _a, _b in filas if w.startswith("a)"))
            print(f"\n   {corto} de {tot} volvieron en la corrida SIGUIENTE "
                  f"({100.0*corto/tot:.0f}%)")
            print("   → si es alto, el arreglo nunca se aplicó de verdad "
                  "(el motor\n     arma su universo al arrancar y el deploy no "
                  "lo reinicia)")
        else:
            print("   (ningún objeto en estado `volvio` con las dos fechas)")

        # ── 3. EL DETALLE DE LOS QUE MÁS PESAN ──────────────────────────────
        _titulo(3, "CASO POR CASO — los que volvieron, con sus fechas")
        filas = _filas(cur, """
            SELECT regla, sujeto, resuelto_at, vuelto_at, veces,
                   round((extract(epoch FROM (vuelto_at - resuelto_at))
                          / 3600.0)::numeric, 1) AS horas
              FROM agente.av_agent_items
             WHERE estado = 'volvio' AND resuelto_at IS NOT NULL
             ORDER BY regla, vuelto_at DESC LIMIT 40
        """)
        if filas:
            print(f"{'REGLA':<26}{'SUJETO':<14}{'ARREGLADO':<18}"
                  f"{'VOLVIÓ':<18}{'VECES':>6}{'HORAS':>8}")
            for r, s, ra, va, vc, h in filas:
                print(f"{(r or '—'):<26}{(s or '—'):<14}"
                      f"{ra:%d/%m %H:%M    }{va:%d/%m %H:%M    }{vc:>6}{h:>8}")
        else:
            print("   (nada en estado `volvio`)")

        # ── 4. ¿PUEDE EXISTIR UN ✔ TODAVÍA? ─────────────────────────────────
        #
        # El ✔ pide 30 días sin volver. Si el objeto resuelto más viejo tiene
        # menos, el mecanismo NO PUEDE emitir un positivo y su 0% de aciertos
        # es aritmética, no calidad.
        _titulo(4, "¿EL MECANISMO PUEDE EMITIR UN ✔ HOY? (pide 30 días)")
        filas = _filas(cur, """
            SELECT count(*) AS resueltos,
                   round(max(extract(epoch FROM (now() - resuelto_at))
                             / 86400.0)::numeric, 1) AS mas_viejo_dias,
                   count(*) FILTER (
                     WHERE resuelto_at < now() - interval '30 days') AS ya_pasaron_30,
                   min(abierto_at)::date AS primer_objeto
              FROM agente.av_agent_items
             WHERE estado = 'resuelto' AND resuelto_at IS NOT NULL
        """)
        if filas and filas[0][0] is not None:
            n, viejo, pasaron, primero = filas[0]
            print(f"   objetos resueltos                     {n:>6}")
            print(f"   el más viejo lleva                    {viejo or 0:>6} días")
            print(f"   ya pasaron los 30 días (pueden votar ✔){pasaron:>5}")
            print(f"   primer objeto de la tabla             {primero!s:>12}")
            if not pasaron:
                print("\n   ⚠️ NINGUNO llegó a los 30 días → el mecanismo solo")
                print("      puede emitir ✖ por ahora. El 0% no mide calidad.")

        # ── 5. ¿LOS DOS MECANISMOS DICEN LO MISMO? ──────────────────────────
        _titulo(5, "LOS DOS MECANISMOS SOBRE EL MISMO CASO — ¿coinciden?")
        filas = _filas(cur, """
            SELECT caso, causa,
                   count(*) FILTER (WHERE acierta)     AS dice_si,
                   count(*) FILTER (WHERE NOT acierta) AS dice_no,
                   string_agg(DISTINCT split_part(ref, ':', 1), ' + ') AS refs
              FROM agente.av_agent_evals
             WHERE origen = 'verificado'
             GROUP BY caso, causa
            HAVING count(*) FILTER (WHERE acierta) > 0
               AND count(*) FILTER (WHERE NOT acierta) > 0
             ORDER BY count(*) DESC LIMIT 20
        """)
        if filas:
            print(f"{'CASO':<16}{'CAUSA':<26}{'✔':>4}{'✖':>4}   MECANISMOS")
            for c, ca, si, no, refs in filas:
                print(f"{(c or '—'):<16}{(ca or '—'):<26}{si:>4}{no:>4}   {refs}")
            print("\n   → cada fila es el sistema contradiciéndose sobre el "
                  "MISMO arreglo")
        else:
            print("   (ningún caso tiene ✔ y ✖ a la vez — no se contradicen hoy)")

        # ── 6. EL VOLUMEN DE CADA MECANISMO ─────────────────────────────────
        _titulo(6, "CUÁNTO MIRA CADA UNO (para saber cuál apagar)")
        filas = _filas(cur, """
            SELECT veredicto, count(*), min(arreglado_at)::date,
                   count(*) FILTER (WHERE votado) AS votados
              FROM agente.av_agent_seguimiento
             GROUP BY veredicto ORDER BY count(*) DESC
        """)
        n_seg = sum(f[1] for f in filas)
        for v, n, d, vo in filas:
            print(f"   tabla vieja · {(v or '—'):<10}{n:>6}   desde {d}"
                  f"   votados {vo}")
        print(f"   tabla vieja · TOTAL          {n_seg:>6}")

        filas = _filas(cur, """
            SELECT estado, count(*)
              FROM agente.av_agent_items
             WHERE tipo <> ALL(ARRAY['aviso','aviso_fila','pregunta'])
               AND estado IN ('resuelto', 'volvio')
             GROUP BY estado ORDER BY count(*) DESC
        """)
        n_it = sum(f[1] for f in filas)
        for e, n in filas:
            print(f"   hitos       · {(e or '—'):<10}{n:>6}")
        print(f"   hitos       · TOTAL          {n_it:>6}")
        print(f"\n   → {n_it} arreglos con reloj de hitos, {n_seg} en la tabla vieja.")
        print("     Los dos escriben `verificado` al MISMO eval set y ninguno")
        print("     está declarado en `core/duplicados.DUPLICADOS`.")

        # ── 7. EL ✖ CRUDO, FILA POR FILA ────────────────────────────────────
        #
        # ⚠️ Los objetos en estado `volvio` son 2, pero los ✖ son muchos más: un
        # item que volvió, votó ✖ y después se re-resolvió vuelve a `resuelto` —
        # y el ✖ queda para siempre en el eval set. Acá se ve cuándo se emitió
        # cada uno y con qué `ref`, que es lo que dice QUIÉN lo escribió.
        _titulo(7, "EL ✖ CRUDO — cuándo se emitió cada uno y quién lo escribió")
        filas = _filas(cur, """
            SELECT causa, caso, ref, creado_at,
                   coalesce(nullif(left(nota, 46), ''), '(sin nota)')
              FROM agente.av_agent_evals
             WHERE origen = 'verificado' AND NOT acierta
             ORDER BY creado_at DESC LIMIT 30
        """)
        if filas:
            print(f"{'CAUSA':<26}{'CASO':<13}{'REF':<26}{'CUÁNDO':<14}NOTA")
            for ca, cs, rf, at, nt in filas:
                print(f"{(ca or '—'):<26}{(cs or '—'):<13}{(rf or '—'):<26}"
                      f"{at:%d/%m %H:%M}  {nt}")
        else:
            print("   (no hay ✖ de origen `verificado`)")

    print(f"\n{LINEA}\nFIN — copiá la salida entera.\n{LINEA}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
