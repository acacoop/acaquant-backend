"""`scripts/diag_redaccion.py` — **¿EL TEXTO DE LOS AVISOS SALE BIEN O SALE
BERRETA?** Read-only.

    python -m scripts.diag_redaccion

La pregunta que este subsistema no puede contestar solo. Un test congela que el
mecanismo no pueda hacer daño (no decide, no pisa el piso, valida cada número);
si el texto sirve o no lo tiene que decir una persona leyéndolo — y para eso
hay que poder verlo AL LADO del que reemplaza, que es lo que hace esta pantalla.

Cuatro secciones:

  1. CUÁNTO — cuántos avisos tienen texto del modelo y cuántos siguen con el piso
  2. EL ANTES Y EL DESPUÉS — el texto fijo y el del modelo, uno debajo del otro
  3. LO QUE SE RECHAZÓ — qué escribió mal y qué validador lo frenó. **Es la
     sección importante**: si acá se repite siempre el mismo motivo, o el prompt
     está mal o la evidencia de esa habilidad no alcanza para escribir nada.
  4. LO QUE NUNCA SE INTENTÓ — avisos que llegaron al tope de intentos

⚠️ NO llama al modelo: sólo lee lo que el daemon ya escribió.
"""
from __future__ import annotations

from agente import redactar
from core.postgres import get_pool

_LINEA = "─" * 78


def _p(t=""):
    print(t)


def main() -> int:
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FILTER (WHERE ia_texto <> ''), "
            "       count(*) FILTER (WHERE ia_texto = '' AND ia_rechazo <> ''), "
            "       count(*) FILTER (WHERE ia_texto = '' AND ia_rechazo = ''), "
            "       count(*) "
            "  FROM agente.hallazgos "
            " WHERE estado IN ('nuevo','en_curso','reincidio') AND arreglo = ''")
        con, rech, virgen, total = cur.fetchone()

        _p(_LINEA)
        _p("1) CUÁNTO — avisos abiertos (los que NO tienen botón)")
        _p(_LINEA)
        _p(f"  con texto del modelo : {con}")
        _p(f"  rechazado, con piso  : {rech}")
        _p(f"  todavía sin intentar : {virgen}")
        _p(f"  TOTAL                : {total}")
        if not total:
            _p("\n  No hay avisos abiertos. Nada que redactar (y nada que juzgar).")
            return 0
        if not con and not rech:
            _p("\n  ⚠ Ninguno se intentó todavía. O el daemon no dio la vuelta, o "
               "AGENTE_REDACTA=0, o el proveedor no está configurado.")

        _p("")
        _p(_LINEA)
        _p("2) EL ANTES Y EL DESPUÉS — juzgalo vos, línea contra línea")
        _p(_LINEA)
        cur.execute(
            "SELECT habilidad, regla, sujeto, problema, que_hacer, ia_texto, ia_at "
            "  FROM agente.hallazgos "
            " WHERE estado IN ('nuevo','en_curso','reincidio') AND arreglo = '' "
            "   AND ia_texto <> '' ORDER BY ia_at DESC LIMIT 25")
        filas = cur.fetchall()
        if not filas:
            _p("  (todavía no hay ninguno)")
        for hab, regla, suj, prob, piso, ia, at in filas:
            _p(f"\n  ▸ {hab}/{regla} · {suj}   [{at:%d/%m %H:%M}]")
            _p(f"      qué pasó : {prob[:150]}")
            _p(f"      PISO     : {piso[:200]}")
            _p(f"      MODELO   : {ia}")

        _p("")
        _p(_LINEA)
        _p("3) LO QUE SE RECHAZÓ — el motivo es el que dice si hay que cambiar algo")
        _p(_LINEA)
        cur.execute(
            "SELECT ia_rechazo, count(*), min(habilidad) || '/' || min(regla) "
            "  FROM agente.hallazgos "
            " WHERE arreglo = '' AND ia_texto = '' AND ia_rechazo <> '' "
            " GROUP BY ia_rechazo ORDER BY count(*) DESC LIMIT 20")
        rech_filas = cur.fetchall()
        if not rech_filas:
            _p("  (ninguno — o no se intentó todavía)")
        for motivo, n, ejemplo in rech_filas:
            _p(f"  {n:>3}×  {motivo[:90]}")
            _p(f"        ej. {ejemplo}")
        if rech_filas and rech_filas[0][1] >= 5:
            _p("\n  ⚠ Un motivo que se repite mucho NO es ruido: o el prompt le "
               "\n    pide algo que la evidencia de esa habilidad no tiene, o esa "
               "\n    regla necesita más evidencia antes de poder explicarse.")

        _p("")
        _p(_LINEA)
        _p(f"4) LO QUE YA NO SE INTENTA MÁS (tope: {redactar.MAX_INTENTOS} intentos)")
        _p(_LINEA)
        cur.execute(
            "SELECT habilidad, regla, count(*) FROM agente.hallazgos "
            " WHERE estado IN ('nuevo','en_curso','reincidio') AND arreglo = '' "
            "   AND ia_texto = '' AND ia_intentos >= %s "
            " GROUP BY 1,2 ORDER BY 3 DESC LIMIT 15", (redactar.MAX_INTENTOS,))
        agotados = cur.fetchall()
        if not agotados:
            _p("  (ninguno)")
        for hab, regla, n in agotados:
            _p(f"  {n:>3}×  {hab}/{regla} — se quedó con el texto fijo")
    _p("")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
