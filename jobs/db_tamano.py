"""jobs/db_tamano.py — LA FOTO DIARIA DEL SISTEMA: base, perfil de tablas y superficie HTTP.

Congela cuánto pesa cada tabla para que el agente pueda decir MAÑANA qué cambió.
Sin esta foto no hay delta, y sin delta el tamaño de la base es un número que no
significa nada.

**Guarda solo DOS fechas** (hoy y ayer) y purga en la misma transacción — pedido
del user: *«tendrá que persistir para tener contexto, pero a su vez no crecer todo
el tiempo»*. Una tabla que vigila el tamaño de la base y crece sin techo es un
chiste que se cuenta solo.

Correrlo dos veces el mismo día no duplica ni rompe el delta: la PK es por fecha.

    python -m jobs.db_tamano
"""
from __future__ import annotations

import sys

from core.job_runs import JobRunLogger


def main() -> int:
    with JobRunLogger("db_tamano") as jr:
        from api.services import av_agent_db as db

        r = db.sacar_foto()
        jr.set_stat("tablas", r["tablas"])
        jr.set_stat("bytes_total", r["bytes_total"])

        # EL PERFIL DE CADA TABLA — qué es y cada cuánto escribe. Va en el MISMO
        # job que la foto porque las dos preguntas son la misma («¿cómo está la
        # base?») y separarlas daría dos horarios y dos cosas que puede fallar.
        from api.services import av_agent_contexto as ctx

        b = ctx.barrer()
        jr.set_stat("perfiladas", b["tablas"])
        jr.set_stat("con_ritmo", b["con_ritmo"])
        print(f"perfil: {b['tablas']} tablas, {b['con_ritmo']} con un ritmo medible")

        # LA SUPERFICIE HTTP — que los permisos sean reales y no "de los
        # papeles". Va acá y no en el monitor de rueda porque la prueba activa
        # hace tráfico contra producción: 400 requests cada 5 minutos molestan,
        # y una superficie mal gateada no se arregla sola en ese rato.
        from api.services import av_agent_seguridad as seg

        d = seg.declarado()
        jr.set_stat("rutas", d["total"])
        jr.set_stat("sin_gate", len(d["abiertas_inesperadas"]))

        # LA FOTO DE LA SUPERFICIE, **antes** de detectar: es lo que le da
        # memoria al chequeo. Sin ella solo se puede decir cuántos endpoints
        # están abiertos hoy; con ella, cuál apareció y cuál PERDIÓ su gate.
        cambios = {}
        try:
            f = seg.sacar_foto()
            jr.set_stat("superficie_rutas", f["rutas"])
            cambios = seg.comparar()
        except Exception as e:      # la foto no puede tumbar el resto del job
            print(f"  ⚠ superficie: no pude sacar la foto — {e}")

        hall = seg.detectar_seguridad()
        for h in hall:
            print(f"  ⚠ SEGURIDAD  {h['ticker']} — {h['motivo']}")

        if cambios.get("ok") and not cambios.get("primera"):
            jr.set_stat("endpoints_nuevos", len(cambios["nuevos"]))
            jr.set_stat("endpoints_de_baja", len(cambios["desaparecidos"]))
            print(f"  superficie: {cambios['total']} endpoints "
                  f"({len(cambios['nuevos'])} nuevos, "
                  f"{len(cambios['desaparecidos'])} dados de baja) vs "
                  f"{cambios['fecha_previa']}")

        # **Decir SIEMPRE qué se verificó.** Un log que solo habla cuando hay
        # problema deja al que lo lee sin saber si no hubo hallazgos o si el
        # chequeo no corrió — y en seguridad esas dos cosas se leen igual.
        efectivo = next((h for h in hall if h["regla"] == "prueba_no_corrio"),
                        None)
        jr.set_stat("borde_probado", 0 if efectivo else 1)
        if efectivo:
            print(f"  seguridad: {d['total']} rutas leídas, "
                  f"{len(d['abiertas_inesperadas'])} sin gate sin declarar. "
                  f"**El borde NO se probó** — {efectivo['evidencia']['texto']}")
        else:
            print(f"  seguridad: {d['total']} rutas leídas + el borde probado "
                  f"sin credenciales (no cubre Vercel)")

        # El delta se calcula acá también para que quede en el LOG del job: si
        # algo creció de golpe, se ve sin abrir nada. El agente lo levanta igual
        # desde la misma función, así que no hay dos verdades.
        c = db.comparar()
        if c.get("ok") and not c.get("primera"):
            jr.set_stat("nuevas", len(c["nuevas"]))
            jr.set_stat("crecieron", len(c["crecieron"]))
            jr.set_stat("desaparecidas", len(c["desaparecidas"]))
            print(f"base: {db.mb(c['bytes_total'])} "
                  f"({'+' if c['delta_total'] >= 0 else ''}{db.mb(c['delta_total'])})")
            for t in c["nuevas"]:
                print(f"  NUEVA        {t['tabla']} — {db.mb(t['bytes'])}")
            for t in c["crecieron"]:
                print(f"  CRECIÓ       {t['tabla']} — +{db.mb(t['delta'])} (+{t['pct']}%)")
            for t in c["desaparecidas"]:
                print(f"  DESAPARECIÓ  {t['tabla']} — pesaba {db.mb(t['bytes'])}")
        else:
            print(f"primera foto: {r['tablas']} tablas, "
                  f"{db.mb(r['bytes_total'])}. Mañana hay delta.")

        # ⚠️ **Y ACÁ SE PERSISTE.** Hasta el 2026-08-19 estos tres detectores
        # existían, corrían y solo IMPRIMÍAN: el hallazgo moría en el log del
        # job. La tab SKILLS decía «se ve en AV Agent → ENCONTRÓ» y no se veía
        # en ningún lado — el catálogo prometía algo que la pantalla no daba.
        #
        # Van con `alcance='sistema'`, que es de REEMPLAZO y no una corrida: la
        # foto de anoche se pisa entera, así lo que se arregló desaparece solo
        # sin que nadie tenga que marcarlo.
        from api.services import av_agent

        # DATO PARTIDO: dos copias del mismo dato que dejaron de coincidir. Va
        # acá y no en el monitor de rueda porque **no depende del mercado** — es
        # un hecho sobre nuestros datos y a las 23:30 es tan cierto como a las 11.
        partido = av_agent.detectar_dato_partido()
        if partido:
            # **El log tiene que decir CUÁLES**, no cuántos. La primera versión
            # cortaba el motivo a 120 caracteres y quedaba «…dice cosas distintas
            # según dónde se lea. m» — el dato accionable (qué bono y qué dos
            # valores) es justo lo que se perdía en el corte.
            print(f"\n⚠ {len(partido)} dato(s) partido(s) — dos copias que no "
                  f"coinciden:")
            for h in partido:
                ev = h.get("evidencia") or {}
                print(f"   · {h['ticker']}  ({ev.get('n', '?')} caso/s)")
                print(f"       {ev.get('a', '?')}")
                print(f"       {ev.get('b', '?')}   → manda: {ev.get('arbitro', '?')}")
                for x in (ev.get("ejemplos") or [])[:5]:
                    print(f"       {x['sujeto']:<10} «{x['valor_a']}»  ≠  "
                          f"«{x['valor_b']}»")
        else:
            print("\n✔ los datos duplicados coinciden en todos sus lugares")

        sistema = hall + ctx.detectar_tablas() + db.detectar_db() + partido
        n = av_agent.reemplazar_hallazgos("sistema", sistema)
        jr.set_stat("hallazgos", n)
        print(f"\n✔ {n} hallazgos del sistema en mercado.av_agent_hallazgos "
              f"(alcance 'sistema') → se ven en AV Agent → ENCONTRÓ")
    return 0


if __name__ == "__main__":
    sys.exit(main())
