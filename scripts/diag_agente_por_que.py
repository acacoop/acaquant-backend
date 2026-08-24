"""¿Qué está pasando realmente en la pantalla del agente? — read-only, 5 segundos.

Contesta, sin interpretación, las cuatro preguntas que hoy no se pueden contestar
mirando la app:

  1. ¿EL DROPLET TIENE EL CÓDIGO NUEVO? (el front deploya solo en Vercel; el
     backend NO — y si están desparejos la pantalla muestra mitad y mitad)
  2. ¿QUEDAN DUPLICADOS en la foto? (y si quedan, por qué: clave nula o dedup
     que no corre)
  3. ¿CORRIÓ EL DIAGNÓSTICO AUTOMÁTICO? ¿cuántos objetos tienen conclusión?
  4. ¿CUÁNTOS PROBLEMAS DISTINTOS hay de verdad detrás de las N filas?

Uso:  python -m scripts.diag_agente_por_que
"""
from __future__ import annotations

import sys


def main() -> int:
    from core.postgres import get_pool

    print("=" * 72)
    print("AV AGENT — POR QUÉ LA PANTALLA MUESTRA LO QUE MUESTRA")
    print("=" * 72)

    # ── 1. ¿este proceso tiene el código nuevo? ──────────────────────────────
    print("\n[1] ¿EL CÓDIGO DE HOY ESTÁ EN ESTE SERVIDOR?")
    marcas = []
    try:
        from api.services import av_agent_vista as v
        marcas.append(("la dedup por clave existe",
                       hasattr(v, "_una_fila_por_problema")))
        marcas.append(("la clave viaja en el SELECT", "clave" in v._COLS_H))
        marcas.append(("el diagnóstico viaja en el SELECT",
                       "diagnostico" in v._COLS_MEM))
    except Exception as e:
        print(f"    ✖ no pude importar la vista: {e}")
    try:
        from api.services import av_agent
        marcas.append(("un job que terminó bien es noticia",
                       av_agent.es_noticia("salud", "salud_job_ok_con_avisos")))
    except Exception as e:
        print(f"    ✖ no pude importar av_agent: {e}")
    try:
        from api.services import av_agent_masivo
        marcas.append(("el diagnóstico automático existe",
                       hasattr(av_agent_masivo, "diagnosticar_pendientes")))
    except Exception as e:
        print(f"    ✖ no pude importar el masivo: {e}")
    for nombre, ok in marcas:
        print(f"    {'✔' if ok else '✖'} {nombre}")
    viejo = [n for n, ok in marcas if not ok]
    if viejo:
        print("\n    ⚠️  ESTE SERVIDOR CORRE CÓDIGO VIEJO. El frontend deploya\n"
              "        solo (Vercel) pero el backend NO: hay que correr\n"
              "        `cd /root/TradingAV && git pull && bash deploy/deploy.sh`.\n"
              "        Todo lo de abajo se lee con eso en mente.")
    else:
        print("\n    → el servidor tiene el código de hoy.")

    with get_pool().connection() as conn, conn.cursor() as cur:
        # ── 2. duplicados ────────────────────────────────────────────────────
        print("\n[2] ¿QUEDAN DUPLICADOS EN LA FOTO?")
        cur.execute("SELECT count(*), count(clave), count(DISTINCT clave) "
                    "FROM agente.av_agent_hallazgos")
        filas, con_clave, distintas = cur.fetchone()
        print(f"    filas en la tabla: {filas}")
        print(f"    con identidad:     {con_clave}"
              + ("" if con_clave == filas
                 else f"   ⚠️ {filas - con_clave} SIN clave — esas no se pueden "
                      "colapsar"))
        print(f"    problemas únicos:  {distintas}")
        cur.execute("SELECT clave, count(*) n, array_agg(DISTINCT alcance) "
                    "FROM agente.av_agent_hallazgos WHERE clave IS NOT NULL "
                    "GROUP BY clave HAVING count(*) > 1 ORDER BY n DESC LIMIT 10")
        dup = cur.fetchall()
        if not dup:
            print("    → no hay dos filas para el mismo problema.")
        else:
            print(f"    ⚠️ {len(dup)} problemas con más de una fila (top 10):")
            for clave, n, alcances in dup:
                print(f"       {clave}  ×{n}   alcances: {list(alcances)}")
            print("\n       Si el punto [1] dio ✔ en todo, estos NO se ven en la\n"
                  "       pantalla: la dedup corre al LEER, no al escribir.")

        # ── 3. el diagnóstico ────────────────────────────────────────────────
        print("\n[3] ¿CORRIÓ EL DIAGNÓSTICO AUTOMÁTICO?")
        try:
            cur.execute("SELECT count(*), count(diagnostico), max(diagnostico_at) "
                        "FROM agente.av_agent_items "
                        "WHERE estado NOT IN ('resuelto','ignorado')")
            tot, con_dx, ultimo = cur.fetchone()
            print(f"    problemas abiertos:       {tot}")
            print(f"    con diagnóstico:          {con_dx}")
            print(f"    el último se hizo:        {ultimo or 'NUNCA'}")
            if not con_dx:
                print("\n    → todavía no corrió. Lo corre `jobs.av_agent` a las\n"
                      "      13/15/17/19 UTC (10/12/14/16 ART). Para verlo YA:\n"
                      "      python -m jobs.av_agent")
        except Exception as e:
            print(f"    ✖ la columna no existe todavía: {e}")
            print("      → falta correr el schema (`bash deploy/deploy.sh` lo hace)")

        # ── 4. cuántos problemas DISTINTOS hay ───────────────────────────────
        print("\n[4] DETRÁS DE LAS FILAS, ¿CUÁNTOS PROBLEMAS DISTINTOS HAY?")
        cur.execute("SELECT regla, count(DISTINCT clave) n FROM agente.av_agent_hallazgos "
                    "WHERE clave IS NOT NULL GROUP BY regla ORDER BY n DESC")
        por_regla = cur.fetchall()
        total = sum(n for _, n in por_regla)
        print(f"    {total} problemas, {len(por_regla)} causas distintas:\n")
        for regla, n in por_regla:
            print(f"       {n:>4}  {regla}")
        if por_regla:
            top, n_top = por_regla[0]
            print(f"\n    → la causa más grande («{top}») explica {n_top} de {total}"
                  f" ({n_top * 100 // max(total, 1)}%).")

        # ── 5. lo que la pantalla llama «apareció hoy» ───────────────────────
        print("\n[5] LO QUE AHORA LLAMA «APARECIÓ HOY»")
        try:
            cur.execute(
                "SELECT count(*) FROM agente.av_agent_items "
                "WHERE abierto_at >= date_trunc('day', now() AT TIME ZONE "
                "'America/Argentina/Buenos_Aires') AT TIME ZONE "
                "'America/Argentina/Buenos_Aires'")
            hoy = cur.fetchone()[0]
            cur.execute("SELECT min(abierto_at), max(abierto_at) "
                        "FROM agente.av_agent_items")
            desde, hasta = cur.fetchone()
            print(f"    objetos con abierto_at de HOY: {hoy}")
            print(f"    el más viejo de la tabla es de: {desde}")
            print(f"    el más nuevo:                   {hasta}")
            print("\n    → si TODO es de hoy es por el borrado: la tabla nació hoy,\n"
                  "      así que «apareció hoy» es cierto del OBJETO aunque el\n"
                  "      HECHO sea del 21. Son dos fechas distintas.")
        except Exception as e:
            print(f"    ✖ {e}")

    print("\n" + "=" * 72)
    return 0


if __name__ == "__main__":
    sys.exit(main())
