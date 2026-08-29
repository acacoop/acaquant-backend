"""READ-ONLY. ¿Cuántos clientes cruzan a ENFRIÁNDOSE, y cada cuánto?

Herramienta: diag · Mide ANTES de diseñar el detector (REGLA #2).

EL PUNTO
========

La idea: que el agente avise el día que una cuenta **cruza** de ACTIVA a
ENFRIÁNDOSE (45 días sin operar), en vez de que el operador la descubra cuando
ya está DORMIDA mirando el tablero.

El cálculo ya existe entero —`comercial_sql.analisis_comercial`— y este diag lo
**usa tal cual** en vez de reescribir la query: el predicado de «qué boleto
cuenta como operación» vive UNA sola vez (`_ULT_OP_WHERE`) y ya lo comparten la
tabla y su modal. Una tercera copia acá sería exactamente la REGLA #9.

LO QUE HAY QUE SABER ANTES DE ESCRIBIR NADA
===========================================

**Cuántas cruzan por día.** Si cruzan 3, un aviso por cuenta es oro. Si cruzan
300, el mismo aviso es ruido y el operador lo apaga la primera semana — y ahí no
perdiste un aviso, perdiste el canal.

Y tres preguntas que deciden el DISEÑO, no el volumen:

· **¿Cuántas tienen AuM > 0?** Una cuenta sin un peso que deja de operar no es
  noticia comercial: es una cuenta vacía. Si la mitad del ruido es eso, el
  detector arranca filtrando.
· **¿Cuántas tienen operador asignado?** El aviso va dirigido a una persona
  (`agente/mensajes.py`). Sin operador no hay a quién avisarle, y esa cuenta es
  OTRO problema — el de `sin_operador`, no el de enfriamiento.
· **¿Cómo se reparten por operador?** Si un operador se lleva 40 de 50 avisos,
  el aviso por cuenta está mal y lo que corresponde es un resumen por persona.

Uso:
    python -m scripts.diag_clientes_enfriandose
"""
from __future__ import annotations

from collections import Counter

from api.services.comercial_sql import analisis_comercial
from core.postgres import get_job_pool

# La ventana de "recién cruzó". Una semana: el agente corre todos los días, pero
# si el operador no mira el lunes tiene que seguir viendo lo del lunes.
VENTANA_CRUCE_D = 7


def _operadores() -> dict[str, str]:
    """{id_cuenta: nombre del operador}. Se pide aparte y NO se toca
    `analisis_comercial`: sus filas no traen el operador y agregárselo para un
    diag sería cambiar un service que usa la vista, por comodidad de un script.

    Lo que NO se duplica es el predicado de «qué boleto cuenta como operación»
    (`_ULT_OP_WHERE`): eso viene del service. Esto es un join de ficha, que no
    tiene criterio adentro.
    """
    with get_job_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT c.id_cuenta, COALESCE(o.nombre, c.operador_email) AS quien "
                    "  FROM clientes.comitentes c "
                    "  LEFT JOIN clientes.operadores o ON o.email = c.operador_email "
                    " WHERE c.operador_email IS NOT NULL AND c.operador_email <> ''")
        return {r[0]: r[1] for r in cur.fetchall()}


def main() -> int:
    r = analisis_comercial(operador="__todos__")
    quien = _operadores()
    cli = r["clientes"]
    d_act, d_dor = r["dias_activa"], r["dias_dormida"]

    print(f"\n{'=' * 74}\n CLIENTES QUE SE ENFRÍAN — {len(cli)} cuentas en el scope"
          f"\n{'=' * 74}")
    print(f"\n  Umbrales de hoy: ACTIVA ≤{d_act} d · ENFRIANDOSE {d_act}-{d_dor} d"
          f" · DORMIDA >{d_dor} d\n")

    print(f"  {'ESTADO':16} {'CUENTAS':>8} {'con AuM>0':>10} {'AuM total':>16}")
    for est in ("ACTIVA", "ENFRIANDOSE", "DORMIDA", "NUEVA"):
        d = [c for c in cli if c["estado"] == est]
        con = [c for c in d if (c["aum"] or 0) > 0]
        print(f"  {est:16} {len(d):>8} {len(con):>10} {sum(c['aum'] or 0 for c in con):>16,.0f}")

    # ── LO QUE DECIDE TODO: cuántas cruzan por día ───────────────────────────
    enfri = [c for c in cli if c["estado"] == "ENFRIANDOSE" and c["dias_sin_operar"]]
    print("\n  ── CUÁNTAS CRUZAN EL UMBRAL, por antigüedad del cruce ──\n")
    print(f"      {'DÍAS SIN OPERAR':22} {'CUENTAS':>8} {'con AuM>0':>10}")
    tramos = [(d_act, d_act, "justo hoy"), (d_act + 1, d_act + VENTANA_CRUCE_D,
              "esta semana"), (d_act + VENTANA_CRUCE_D + 1, d_act + 30, "el último mes"),
              (d_act + 31, d_dor, "hace más de un mes")]
    for lo, hi, etiqueta in tramos:
        d = [c for c in enfri if lo <= c["dias_sin_operar"] <= hi]
        con = sum(1 for c in d if (c["aum"] or 0) > 0)
        print(f"      {etiqueta + f' ({lo}-{hi} d)':22} {len(d):>8} {con:>10}")

    nuevas = [c for c in enfri if d_act <= c["dias_sin_operar"] <= d_act + VENTANA_CRUCE_D]
    print(f"\n      → el aviso de una semana serían {len(nuevas)} cuentas"
          f"  ({sum(1 for c in nuevas if (c['aum'] or 0) > 0)} con plata)")
    if nuevas:
        print(f"      → o sea ~{len(nuevas) / (VENTANA_CRUCE_D + 1):.1f} por día\n")

    # ── ¿Hay a quién avisarle, y se reparte parejo? ──────────────────────────
    sin_op = [c for c in enfri if not quien.get(c["id_cuenta"])]
    print("  ── ¿A QUIÉN SE LE AVISA? ──\n")
    print(f"      enfriándose SIN operador asignado: {len(sin_op)} de {len(enfri)}"
          f"   (a esas no hay a quién avisarles)")
    por_op = Counter(quien.get(c["id_cuenta"], "—")
                     for c in enfri if quien.get(c["id_cuenta"]))
    if por_op:
        print(f"\n      {'OPERADOR':32} {'CUENTAS':>8}")
        for nombre, n in por_op.most_common(12):
            print(f"      {str(nombre)[:32]:32} {n:>8}")
        top = por_op.most_common(1)[0]
        print(f"\n      → el que más tiene se lleva {top[1]} de "
              f"{sum(por_op.values())}. Si es la mayoría, el aviso por cuenta\n"
              f"        está mal y va un resumen por persona.")

    # ── Las 10 que más plata tienen, para mirarlas con ojo ───────────────────
    print("\n  ── LAS 10 ENFRIÁNDOSE CON MÁS AuM ──\n")
    print(f"      {'CUENTA':10} {'DÍAS':>5} {'AuM':>16}  DENOMINACIÓN")
    for c in sorted(enfri, key=lambda x: -(x["aum"] or 0))[:10]:
        print(f"      {c['id_cuenta']:10} {c['dias_sin_operar']:>5} "
              f"{c['aum'] or 0:>16,.0f}  {str(c['denominacion'])[:34]}")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
