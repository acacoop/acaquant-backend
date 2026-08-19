"""jobs/db_tamano.py — LA FOTO DIARIA DE LA BASE + EL PERFIL DE CADA TABLA.

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
    return 0


if __name__ == "__main__":
    sys.exit(main())
