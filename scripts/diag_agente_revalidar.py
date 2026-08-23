"""scripts/diag_agente_revalidar.py — EL SEGUNDO TEST: ¿la propuesta se sostiene?

Doc: `docs/AV_AGENT.md` (propuesta «la regla como objeto», 2026-08-23).

POR QUÉ EXISTE
==============

El user, 2026-08-23: *«ya estoy harto de "hice esto" pero mi diagnóstico estaba
mal, lo exageré. Es un segundo test para re-chequear lo que proponés»*.

Tiene razón, y al re-leer mi propio análisis encontré **dos afirmaciones que hice
sin poder sostenerlas**. Este diag las mide. No agrega ninguna teoría nueva:
solo va a buscar los números que faltaban para decidir si cada fase sirve.

    1. ¿QUÉ SIGNIFICA «sin_tea_con_precio acierta 25%»?
       Dije que el diagnóstico acierta 1 de 4. Pero ese 25% mezcla TRES cosas
       distintas: el voto humano (una opinión), el `derivado` (que escribe
       `acierta=True` FIJO, así que siempre suma) y el `verificado` (que dice
       «el problema volvió», que no es lo mismo que «el diagnóstico estuvo
       mal»). Sin separarlos, el número no dice lo que dije que decía.

    2. ¿CUÁNTO CRECE DE VERDAD `aviso_fila`?
       Dije «142 objetos nuevos por día → 52.000 al año». Lo extrapolé de UNA
       observación. El cron corre todos los hábiles (`45 19 * * 1-5`), así que
       DEBERÍA haber una regla por día — pero en la foto había una sola fecha.
       Acá se cuentan las fechas distintas y sus filas: si hay una sola, mi
       número era una proyección disfrazada de medición.

    3. ¿SE PUEDE ADIVINAR LA REGLA DESDE EL SUJETO?
       La fase F3 (pasar `regla=` al libro) necesita saber qué regla originó el
       arreglo. Si cada bono tuviera UNA sola regla abierta, se podría deducir
       del ticker y F3 no dependería de F2. Acá se mide cuántos sujetos tienen
       más de una: si son muchos, deducirla es exactamente el anti-patrón de la
       REGLA #9 y F3 NO puede ir antes que F2.

SEGURIDAD (REGLA #4)
====================

**100% SOLO LECTURA.** Cero INSERT/UPDATE/DELETE. Cinco queries agregadas sobre
el schema `agente`, que es chico (8,9 MB medidos). No toca `mercado.*` ni
ninguna tabla de rueda: se puede correr con el mercado abierto.

USO
===

    python -m scripts.diag_agente_revalidar

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
        cur.execute(sql, args)
        return cur.fetchall()
    except Exception as e:                      # es un diag: nunca aborta
        cur.connection.rollback()
        print(f"   (no se pudo leer: {str(e).strip()[:120]})")
        return []


def main() -> int:
    with get_pool().connection() as conn, conn.cursor() as cur:

        # ── 1. EL VOTO, DESARMADO ───────────────────────────────────────────
        #
        # ⚠️ LA PREGUNTA QUE INVALIDA O SOSTIENE MI CONCLUSIÓN MÁS FUERTE.
        # `derivado` vota SIEMPRE `acierta=True` (av_agent_acciones.py:166), así
        # que un porcentaje que lo incluya está inflado por construcción. Y
        # `verificado` con acierta=False significa «el problema VOLVIÓ»
        # (av_agent_items.cerrar_hitos), que es evidencia fuerte pero de otra
        # cosa. Solo el humano es una opinión sobre el DIAGNÓSTICO.
        _titulo(1, "EL VOTO DESARMADO — la misma causa, por ORIGEN")
        filas = _filas(cur, """
            SELECT causa,
                   count(*) FILTER (WHERE origen = 'humano')                    AS hum,
                   count(*) FILTER (WHERE origen = 'humano' AND acierta)        AS hum_ok,
                   count(*) FILTER (WHERE origen = 'verificado')                AS ver,
                   count(*) FILTER (WHERE origen = 'verificado' AND acierta)    AS ver_ok,
                   count(*) FILTER (WHERE origen = 'derivado')                  AS der,
                   count(*) FILTER (WHERE origen = 'utilidad')                  AS uti,
                   count(*)                                                     AS total
              FROM agente.av_agent_evals
             GROUP BY causa
             HAVING count(*) FILTER (WHERE origen IN ('humano','verificado')) > 0
             ORDER BY count(*) FILTER (WHERE origen IN ('humano','verificado')) DESC
        """)
        if filas:
            print(f"{'CAUSA':<30}{'HUM':>5}{'✔':>4}{'VERIF':>7}{'✔':>4}"
                  f"{'DERIV':>7}{'UTIL':>6}{'TOTAL':>7}   PRECISIÓN REAL")
            for c, hum, hok, ver, vok, der, uti, tot in filas:
                base = hum + ver
                ok = hok + vok
                pct = f"{100.0 * ok / base:.0f}%  ({ok}/{base})" if base else "—"
                print(f"{(c or '—'):<30}{hum:>5}{hok:>4}{ver:>7}{vok:>4}"
                      f"{der:>7}{uti:>6}{tot:>7}   {pct}")
            print("\n   PRECISIÓN REAL = solo humano + verificado, que es lo que")
            print("   mira la compuerta de autonomía. Los `derivado` NO entran:")
            print("   se escriben con acierta=True fijo y siempre dicen que sí.")

        # ── 2. ¿EL `verificado` NEGATIVO ES «VOLVIÓ»? ───────────────────────
        # La nota lo dice con todas las letras. Verlo separa «el humano opinó
        # que el diagnóstico estuvo mal» de «el arreglo no aguantó», que son
        # dos hechos distintos y yo los conté juntos.
        _titulo(2, "LOS VOTOS NEGATIVOS: ¿opinión humana o el problema volvió?")
        filas = _filas(cur, """
            SELECT origen, coalesce(nullif(left(nota, 60), ''), '(sin nota)') AS nota,
                   count(*)
              FROM agente.av_agent_evals
             WHERE NOT acierta
             GROUP BY origen, left(nota, 60)
             ORDER BY count(*) DESC LIMIT 20
        """)
        for o, nota, n in filas:
            print(f"   {(o or '—'):<12}{n:>4}  {nota}")
        if not filas:
            print("   (no hay votos negativos)")

        # ── 3. ¿CUÁNTAS FECHAS DISTINTAS HAY? ───────────────────────────────
        #
        # ⚠️ ACÁ SE CAE O SE SOSTIENE MI «142 POR DÍA». El cron corre todos los
        # hábiles, así que debería haber una regla por día hábil desde que el
        # espejo existe. Si hay UNA sola fecha, mi extrapolación no tenía base.
        _titulo(3, "LA CAUSA CON FECHA — ¿cuántas fechas distintas hay?")
        filas = _filas(cur, """
            SELECT regla, count(*) AS filas,
                   min(abierto_at)::date AS desde,
                   count(*) FILTER (WHERE estado = 'resuelto') AS cerradas
              FROM agente.av_agent_items
             WHERE tipo IN ('aviso_fila', 'aviso')
             GROUP BY regla ORDER BY min(abierto_at) DESC LIMIT 40
        """)
        if filas:
            print(f"{'REGLA (con fecha adentro)':<44}{'FILAS':>7}{'DESDE':>12}"
                  f"{'CERRADAS':>10}")
            for r, n, desde, cer in filas:
                print(f"{(r or '—'):<44}{n:>7}{desde!s:>12}{cer:>10}")
            print(f"\n   fechas/temas distintos: {len(filas)}")
            print("   → si es 1, el «142 por día» era una proyección, no un dato")
        else:
            print("   (no hay avisos espejados como objeto)")

        # ── 4. ¿SE PUEDE DEDUCIR LA REGLA DESDE EL SUJETO? ──────────────────
        #
        # De esto depende que F3 pueda ir ANTES o DESPUÉS de F2. Si un bono
        # tiene dos reglas abiertas, deducir «la» regla desde el ticker es
        # elegir una de dos sin criterio — REGLA #9 en estado puro.
        _titulo(4, "¿UN SUJETO, UNA REGLA? — de esto depende el orden de F2/F3")
        filas = _filas(cur, """
            WITH x AS (
              SELECT sujeto, count(DISTINCT regla) AS reglas
                FROM agente.av_agent_items
               WHERE estado NOT IN ('resuelto', 'ignorado')
                 AND tipo NOT IN ('aviso', 'aviso_fila', 'pregunta')
               GROUP BY sujeto
            )
            SELECT reglas, count(*) FROM x GROUP BY reglas ORDER BY reglas
        """)
        tot = sum(n for _r, n in filas)
        amb = sum(n for r, n in filas if r > 1)
        for r, n in filas:
            marca = "  ← ambiguo" if r > 1 else ""
            print(f"   sujetos con {r} regla(s) abierta(s): {n:>4}{marca}")
        if tot:
            print(f"\n   {amb} de {tot} sujetos ({100.0*amb/tot:.0f}%) tienen MÁS DE UNA")
            print("   → si es >0, la regla NO se puede deducir del ticker y F3")
            print("     necesita que F2 la haga viajar primero")

        # Y los casos concretos, para poder mirarlos.
        filas = _filas(cur, """
            SELECT sujeto, string_agg(DISTINCT regla, ' + ' ORDER BY regla)
              FROM agente.av_agent_items
             WHERE estado NOT IN ('resuelto', 'ignorado')
               AND tipo NOT IN ('aviso', 'aviso_fila', 'pregunta')
             GROUP BY sujeto HAVING count(DISTINCT regla) > 1
             ORDER BY count(DISTINCT regla) DESC LIMIT 15
        """)
        if filas:
            print("\n   ejemplos:")
            for s, rr in filas:
                print(f"     {s:<16} {rr}")

        # ── 5. ¿CUÁNTO AGREGARÍA F3 AL EVAL SET, Y DE QUÉ CALIDAD? ──────────
        #
        # Si las 44 acciones de `arreglar_bono` empiezan a votar `derivado`
        # (que es acierta=True fijo), el porcentaje de esas causas SUBE sin que
        # nada haya mejorado. Este número dice cuánto se inflaría.
        _titulo(5, "SI F3 SE HACE MAL: cuántos votos automáticos se sumarían")
        filas = _filas(cur, """
            SELECT accion, count(*) AS sin_regla
              FROM agente.av_agent_acciones
             WHERE ok AND (regla IS NULL OR regla = '')
               AND por IS NOT NULL AND por <> ''
             GROUP BY accion ORDER BY count(*) DESC
        """)
        n_tot = sum(n for _a, n in filas)
        for a, n in filas:
            print(f"   {(a or '—'):<28}{n:>5} acciones sin regla, con autor")
        print(f"\n   {n_tot} votos `derivado` nuevos si F3 solo agrega `regla=`")
        print("   ⚠️ TODOS con acierta=True fijo → subirían la precisión sin")
        print("      que nada haya mejorado. F3 tiene que ir con el arreglo del")
        print("      voto derivado, o no ir.")

    print(f"\n{LINEA}\nFIN — copiá la salida entera.\n{LINEA}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
