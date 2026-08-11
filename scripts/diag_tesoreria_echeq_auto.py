"""Por qué los cheques emitidos AUTO (espejo de Aunesa) no impactan en BANCOS.

EL CONTRATO. Un cheque emitido `origen='aunesa'` nace del espejo de un movimiento
e-cheq de EGRESO de Aunesa (`sincronizar_echeq_emitidos`). Por eso NO vuelve a restar
del saldo: `_cheques_emitidos_vencidos_rows` filtra `origen='manual'`, y el consolidado
por banco de la tab CHEQUES lo saca de "impacta en bancos". La promesa es
"esa plata ya la puso su propio movimiento".

Esa promesa se apoya en DOS supuestos que pueden fallar en silencio, y este diag mide
cuál de los dos está pasando:

  A) TEMPORAL — el movimiento restó el día del movimiento, NO hoy. El tablero EMITIDOS
     no filtra por fecha, así que un auto de la semana pasada sigue a la vista aunque su
     plata ya se haya descontado en la grilla de AQUEL día. Mirando el BANCOS de hoy,
     ese monto no está — y está bien que no esté.

  B) EL MOVIMIENTO NUNCA CONTÓ — en la grilla BANCOS, un movimiento de Aunesa cuyo `id`
     no trae hora (`_hora` no matchea YYYYMMDDHHMMSS) arranca DESTILDADO por duplicidad
     (`OBS_SIN_HORA`). Si los e-cheq vienen así, el día del movimiento tampoco restaron.
     Ahí la plata no impacta NUNCA: el cheque dice "ya lo hizo el movimiento" y el
     movimiento está deseleccionado. Eso sí es un agujero.

Qué hace: por cada cheque AUTO abierto busca su movimiento en Aunesa del día de su
`fecha_pago` y dictamina si ese movimiento efectivamente restó del saldo de ese día.

Es READ-ONLY (no escribe nada; sí pega a Aunesa, una llamada por día distinto). Uso:
    python -m scripts.diag_tesoreria_echeq_auto            # los últimos 10 días con autos
    python -m scripts.diag_tesoreria_echeq_auto --dias 30
    python -m scripts.diag_tesoreria_echeq_auto --todos    # OJO: 1 llamada a Aunesa por día
"""
from __future__ import annotations

import sys
from collections import defaultdict
from datetime import date

from api.services import tesoreria as t

DIAS_DEFAULT = 10

# Veredicto de cada cheque auto: ¿su movimiento restó del saldo del día que le toca?
OK = "RESTO EN SU DIA"
SIN_HORA = "NUNCA RESTO (movimiento destildado por defecto: sin hora)"
DESTILDADO = "NUNCA RESTO (movimiento destildado a mano)"
NO_PROCESADO = "NUNCA RESTO (movimiento no esta en estado Procesado)"
SIN_MOV = "NUNCA RESTO (Aunesa ya no devuelve ese movimiento en ese dia)"
SIN_FECHA = "SIN fecha_pago (no se puede ubicar el dia del movimiento)"


def autos_abiertos() -> list[dict]:
    """Los mismos cheques AUTO que el consolidado cuenta en la columna 'auto': lado
    emitido, origen aunesa y todavía no cerrados."""
    return t._items_sql(
        f"SELECT id, mov_id, banco, unidad, importe, estado, fecha_pago, "
        f"       comitente_denominacion "
        f"  FROM {t._TABLA_CHEQUES} "
        f" WHERE lado = 'emitido' AND origen = '{t.ORIGEN_AUNESA}' "
        f"   AND estado <> %(cierre)s "
        f" ORDER BY fecha_pago DESC NULLS LAST, id DESC",
        {"cierre": t.ESTADO_CIERRE["emitido"]},
    )


def movimientos_echeq(dia: date) -> dict[str, dict]:
    """{mov_id: fila cruda de Aunesa} de los e-cheq de EGRESO de ese día."""
    crudas = t.traer_crudas(dia, t.TODOS_ESTADOS)
    yyyymmdd = dia.strftime("%Y%m%d")
    out: dict[str, dict] = {}
    for r in crudas:
        m = t.aplanar(r, yyyymmdd)
        if m["_tipo"] != "egreso" or not m["_echeq"]:
            continue
        mid = str(r.get("id") or "").strip()
        if mid:
            out[mid] = {"cruda": r, "mov": m}
    return out


def veredicto(ch: dict, movs: dict[str, dict], exc: dict) -> tuple[str, str]:
    """(veredicto, nota) de un cheque auto. Replica EXACTAMENTE lo que hace la grilla
    BANCOS con ese movimiento: filtro de estado + tilde/destilde (con su default)."""
    mid = str(ch.get("mov_id") or "").strip()
    hit = movs.get(mid)
    if hit is None:
        return SIN_MOV, f"mov_id={mid or '(vacio)'}"
    cruda, mov = hit["cruda"], hit["mov"]
    estado = str(cruda.get("estado") or "").strip()
    if estado != t.ESTADO_EFECTIVO:
        return NO_PROCESADO, f"estado={estado or '(vacio)'}"
    # El MISMO cálculo que `ingresos_egresos_dia`: sin hora → destildado por default.
    fuera, obs = t._estado_excl(exc, "aunesa", mid, default_excluido=not mov["_hora"],
                                obs_default=t.OBS_SIN_HORA)
    if not fuera:
        return OK, f"hora={mov['_hora'] or '(sin hora)'}"
    if not mov["_hora"] and not exc.get(("aunesa", mid)):
        return SIN_HORA, f"id={mid} no matchea YYYYMMDDHHMMSS del dia"
    return DESTILDADO, obs or "override en tesoreria_exclusiones"


def main(argv: list[str]) -> int:
    limite = DIAS_DEFAULT
    if "--todos" in argv:
        limite = 10**6
    elif "--dias" in argv:
        limite = int(argv[argv.index("--dias") + 1])

    filas = autos_abiertos()
    if not filas:
        print("No hay cheques emitidos AUTO abiertos. Nada que revisar.")
        return 0

    hoy = t._hoy_art().date()
    dias = sorted({f["fecha_pago"] for f in filas if f["fecha_pago"]}, reverse=True)
    mirar = set(dias[:limite])
    print(f"Cheques emitidos AUTO abiertos: {len(filas)} | dias distintos: {len(dias)} "
          f"| se revisan {len(mirar)} (1 llamada a Aunesa c/u)")
    print(f"HOY (ART) = {hoy.isoformat()}\n")

    por_veredicto: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    total_auto: dict[str, float] = defaultdict(float)
    de_dias_pasados: dict[str, float] = defaultdict(float)

    for dia in sorted(mirar):
        del_dia = [f for f in filas if f["fecha_pago"] == dia]
        try:
            movs = movimientos_echeq(dia)
        except Exception as e:
            print(f"  {dia}  !! no pude traer Aunesa: {type(e).__name__}: {e}")
            continue
        exc = t._exclusiones_dia(dia)
        print(f"── {dia.isoformat()}{'  (dia anterior a hoy)' if dia < hoy else ''} "
              f"· {len(del_dia)} cheque(s) auto · {len(movs)} mov e-cheq de egreso en Aunesa")
        for ch in del_dia:
            uni = str(ch["unidad"] or "ARS").upper()
            imp = float(ch["importe"] or 0)
            v, nota = veredicto(ch, movs, exc)
            por_veredicto[v][uni] += imp
            total_auto[uni] += imp
            if dia < hoy:
                de_dias_pasados[uni] += imp
            print(f"     #{ch['id']:<6} {ch['banco'][:34]:<34} {uni} {imp:>14,.2f}  "
                  f"{v}  [{nota}]")
        print()

    sin_fecha = [f for f in filas if not f["fecha_pago"]]
    for ch in sin_fecha:
        uni = str(ch["unidad"] or "ARS").upper()
        por_veredicto[SIN_FECHA][uni] += float(ch["importe"] or 0)
        total_auto[uni] += float(ch["importe"] or 0)

    print("=" * 78)
    print("RESUMEN — de todo lo que el consolidado muestra en la columna AUTO:")
    for v in (OK, SIN_HORA, DESTILDADO, NO_PROCESADO, SIN_MOV, SIN_FECHA):
        if v in por_veredicto:
            montos = "  ".join(f"{u} {m:,.2f}" for u, m in sorted(por_veredicto[v].items()))
            print(f"  {v:<58} {montos}")
    print("-" * 78)
    print("  TOTAL auto revisado                                        "
          + "  ".join(f"{u} {m:,.2f}" for u, m in sorted(total_auto.items())))
    if de_dias_pasados:
        print("  ...de DIAS ANTERIORES a hoy (causa A: restaron en SU dia,      "
              + "  ".join(f"{u} {m:,.2f}" for u, m in sorted(de_dias_pasados.items())))
        print("     no en el BANCOS de hoy — es correcto, pero el tablero los sigue mostrando)")
    print()
    print("COMO LEERLO:")
    print(f"  · Todo en '{OK}' → causa A pura: la plata SI se descontó, pero el dia del")
    print("    movimiento. El tablero EMITIDOS no filtra por fecha y los arrastra para")
    print("    siempre (nadie los marca 'completado'), por eso parece que no impactan.")
    print(f"  · Algo en '{SIN_HORA}' → causa B, agujero real: ese monto NO se")
    print("    descontó NUNCA, ni ese dia ni hoy. El cheque delega en el movimiento y el")
    print("    movimiento arranca deseleccionado por duplicidad.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
