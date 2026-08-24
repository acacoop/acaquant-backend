"""scripts/diag_agente_conexion.py — ¿QUÉ TAN CONECTADO ESTÁ EL AGENTE?

Doc: `docs/AV_AGENT.md` (propuesta «la regla como objeto», 2026-08-23).

QUÉ CONTESTA
============

El mapa de la arquitectura salió del CÓDIGO y es verificable sin la base. Estos
tres números no: necesitan prod, y son los que deciden el ORDEN del trabajo (no
la arquitectura, que no depende de ellos).

    1. HALLAZGOS ABIERTOS POR REGLA
       Qué regla hace más ruido en la pantalla. Es la que conviene atacar
       primero cuando se rediseñe la fila.

    2. VOTOS POR REGLA, separando humano de derivado
       El corte medido en el código dice que **1 de 21** llamadas al libro pasa
       `regla`, así que los arreglos del modal no generan voto derivado. Acá se
       ve si eso se nota en los datos.

    3. EL SEGUIMIENTO, AHORA CON UN SOLO MEDIDOR
       Hasta el 2026-08-24 había DOS: `av_agent_seguimiento` (tabla propia,
       ventana de 5 días corridos) y los HITOS de `av_agent_items`. El viejo se
       borró en la Fase 2 — armaba la clave `accion:objetivo:regla` y la
       comparaba contra `tipo:sujeto:regla`, así que solo podía decir «aguantó».
       Acá se mira que el que quedó tenga casos de verdad: un medidor sin
       entradas es lo mismo que no tenerlo.

SEGURIDAD (REGLA #4)
====================

**100% SOLO LECTURA.** No hay un solo INSERT/UPDATE/DELETE en este archivo.
Son 8 queries agregadas (`GROUP BY` + `count`) sobre tablas del schema `agente`,
que son chicas por diseño. No escanea `mercado.*` ni ninguna tabla de rueda, así
que se puede correr en cualquier momento — también con el mercado abierto.

USO
===

    python -m scripts.diag_agente_conexion

Sin argumentos. Imprime y no deja nada. Copiar la salida entera y pegarla.
"""
from __future__ import annotations

from core.postgres import get_pool

LINEA = "─" * 78


def _titulo(n: int, t: str) -> None:
    print(f"\n{LINEA}\n{n}. {t}\n{LINEA}")


def _filas(cur, sql: str, args: tuple = ()) -> list[tuple]:
    """Una query que NUNCA tumba el diag: si la tabla no existe todavía en prod
    (el schema del repo no siempre está 100% aplicado — ver CLAUDE.md «Capa
    SQL»), se dice y se sigue. Un diag que muere en la query 3 no sirve para
    nada, y las 5 que faltan eran las que importaban."""
    try:
        cur.execute(sql, args)
        return cur.fetchall()
    except Exception as e:                      # es un diag: nunca aborta
        cur.connection.rollback()
        print(f"   (no se pudo leer: {str(e).strip()[:120]})")
        return []


def main() -> int:
    with get_pool().connection() as conn, conn.cursor() as cur:

        # ── 1. LA LISTA, POR REGLA ──────────────────────────────────────────
        # Se cuenta sobre `av_agent_items` (el objeto canónico) y NO sobre
        # `av_agent_hallazgos`: la foto tiene una fila por corrida, así que
        # contar ahí mide cuántas veces se vio y no cuántos problemas hay.
        _titulo(1, "HALLAZGOS ABIERTOS POR REGLA (agente.av_agent_items)")
        filas = _filas(cur, """
            SELECT regla, tipo, count(*) AS n,
                   count(*) FILTER (WHERE estado = 'nuevo')    AS nuevos,
                   count(*) FILTER (WHERE estado = 'volvio')   AS volvieron,
                   round(avg(extract(epoch FROM (now() - abierto_at)) / 86400)::numeric, 1)
                       AS dias_prom
              FROM agente.av_agent_items
             WHERE estado NOT IN ('resuelto', 'ignorado')
             GROUP BY regla, tipo
             ORDER BY n DESC
        """)
        if filas:
            print(f"{'REGLA':<30}{'TIPO':<20}{'N':>6}{'NUEVOS':>8}"
                  f"{'VOLVIÓ':>8}{'DÍAS':>7}")
            for r, t, n, nu, vo, d in filas:
                print(f"{(r or '—'):<30}{(t or '—'):<20}{n:>6}{nu:>8}{vo:>8}"
                      f"{(d if d is not None else 0):>7}")
            print(f"\n   TOTAL abiertos: {sum(f[2] for f in filas)}")

        # ── 2. LOS MISMOS, POR ESTADO ───────────────────────────────────────
        _titulo(2, "TODOS LOS OBJETOS, POR ESTADO Y TIPO")
        filas = _filas(cur, """
            SELECT tipo, estado, count(*)
              FROM agente.av_agent_items
             GROUP BY tipo, estado ORDER BY tipo, estado
        """)
        for t, e, n in filas:
            print(f"   {(t or '—'):<22}{(e or '—'):<14}{n:>6}")

        # ── 3. EL EVAL SET, POR ORIGEN ──────────────────────────────────────
        # `humano` = alguien apretó ✔/✖. `derivado` = se dedujo de una acción
        # aprobada — y ÉSE es el que el corte 2 predice casi vacío.
        _titulo(3, "EVAL SET: votos por ORIGEN (agente.av_agent_evals)")
        filas = _filas(cur, """
            SELECT origen, dominio, count(*),
                   count(*) FILTER (WHERE acierta) AS aciertos
              FROM agente.av_agent_evals
             GROUP BY origen, dominio ORDER BY origen, dominio
        """)
        for o, d, n, a in filas:
            pct = f"{100.0 * a / n:.0f}%" if n else "—"
            print(f"   {(o or '—'):<12}{(d or '—'):<12}{n:>6} votos   "
                  f"{a:>4} ✔ ({pct})")
        if not filas:
            print("   (sin votos)")

        _titulo(4, "EVAL SET: las causas más votadas")
        filas = _filas(cur, """
            SELECT causa, count(*),
                   count(*) FILTER (WHERE acierta) AS ok,
                   count(*) FILTER (WHERE origen = 'humano') AS humanos
              FROM agente.av_agent_evals
             GROUP BY causa ORDER BY count(*) DESC LIMIT 30
        """)
        if filas:
            print(f"{'CAUSA VOTADA':<34}{'VOTOS':>7}{'✔':>6}{'HUMANOS':>9}")
            for c, n, ok, hu in filas:
                print(f"{(c or '—'):<34}{n:>7}{ok:>6}{hu:>9}")

        # ── 5. EL LIBRO: cuántas acciones NO llevan regla ───────────────────
        # ⚠️ ESTA ES LA MEDICIÓN CLAVE. En el código son 20 de 21 llamadas sin
        # `regla`; acá se ve qué proporción de las acciones REALES quedó fuera
        # del voto derivado y del seguimiento.
        _titulo(5, "EL LIBRO: acciones CON y SIN regla (agente.av_agent_acciones)")
        filas = _filas(cur, """
            SELECT accion,
                   count(*) AS total,
                   count(*) FILTER (WHERE regla IS NOT NULL AND regla <> '') AS con_regla,
                   count(*) FILTER (WHERE ok) AS salieron_ok
              FROM agente.av_agent_acciones
             GROUP BY accion ORDER BY count(*) DESC
        """)
        if filas:
            print(f"{'ACCIÓN':<28}{'TOTAL':>7}{'CON REGLA':>11}{'OK':>6}"
                  f"{'→ VOTAN':>9}")
            tot = con = 0
            for a, n, cr, ok in filas:
                tot += n
                con += cr
                print(f"{(a or '—'):<28}{n:>7}{cr:>11}{ok:>6}"
                      f"{('sí' if cr else 'NO'):>9}")
            print(f"\n   {con} de {tot} acciones llevan regla → es lo que "
                  f"permite\n   cruzar el libro con la causa que se juzgó")

        # ── 6. EL SEGUIMIENTO, CON UN SOLO MEDIDOR ──────────────────────────
        _titulo(6, "SEGUIMIENTO: ¿el único medidor que quedó tiene casos?")
        print("   `av_agent_seguimiento` se borró el 2026-08-24 (Fase 2): su\n"
              "   veredicto era siempre «aguantó» por una clave que no cruzaba.\n")
        # Lo que la sub-tab ¿AGUANTAN? cuenta de verdad: items resueltos que no
        # son comunicaciones (la MISMA condición de `items.en_seguimiento`).
        filas = _filas(cur, """
            SELECT count(*) FROM agente.av_agent_items
             WHERE estado = 'resuelto' AND resuelto_at IS NOT NULL
               AND tipo NOT IN ('aviso', 'aviso_fila', 'pregunta')
        """)
        n_items = filas[0][0] if filas else 0
        print(f"   items resueltos = arreglos con el reloj corriendo  {n_items:>6}")
        if not n_items:
            print("   ⚠ CERO. El medidor está vivo y no recibe nada: revisar que\n"
                  "     los detectores estén CERRANDO por ausencia (`sincronizar`).")

        # ── 7. LA FOTO vs EL OBJETO ─────────────────────────────────────────
        # Sirve para saber si la migración a `items` está completa: un hallazgo
        # de la última corrida sin objeto es memoria que existe y no se alcanza.
        _titulo(7, "LA FOTO vs EL OBJETO (¿todo hallazgo tiene DNI?)")
        filas = _filas(cur, """
            WITH ult AS (
              SELECT max(corrida_at) AS c FROM agente.av_agent_hallazgos
               WHERE alcance NOT IN ('live', 'sistema')
            )
            SELECT count(*) AS hallazgos,
                   count(*) FILTER (WHERE h.clave IS NULL OR h.clave = '') AS sin_clave,
                   count(*) FILTER (WHERE i.clave IS NULL) AS sin_objeto
              FROM agente.av_agent_hallazgos h
              LEFT JOIN agente.av_agent_items i ON i.clave = h.clave
             WHERE h.corrida_at = (SELECT c FROM ult)
        """)
        if filas:
            n, sc, so = filas[0]
            print(f"   hallazgos en la última corrida    {n:>6}")
            print(f"   sin `clave` escrita               {sc:>6}")
            print(f"   con clave pero SIN objeto espejado{so:>6}")

        # ── 8. TAMAÑO de la memoria (el pendiente del primer análisis) ───────
        _titulo(8, "PESO DEL SCHEMA agente (las 4 sin techo, de paso)")
        filas = _filas(cur, """
            SELECT c.relname,
                   pg_size_pretty(pg_total_relation_size(c.oid)) AS peso,
                   pg_total_relation_size(c.oid) AS bytes,
                   c.reltuples::bigint AS filas_aprox
              FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
             WHERE n.nspname = 'agente' AND c.relkind = 'r'
             ORDER BY pg_total_relation_size(c.oid) DESC
        """)
        if filas:
            print(f"{'TABLA':<30}{'PESO':>12}{'FILAS ~':>12}")
            for t, peso, _b, fa in filas:
                print(f"{t:<30}{peso:>12}{fa:>12}")

    print(f"\n{LINEA}\nFIN — copiá la salida entera.\n{LINEA}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
