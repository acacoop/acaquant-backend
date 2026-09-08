"""Diag read-only: ¿con QUÉ columna el agente juzga la frescura de cada tabla?

Contesta la pregunta que abrió el aviso falso de `bancos.mayor_movimientos`
(08/09): *«no escribe hace 3,5 días»* sobre una tabla que se estaba escribiendo
**cada 15 minutos**. El detector no medía la escritura: medía `fecha_conciliacion`,
que es **de qué día son los datos**, y ese job trae SIEMPRE el día hábil anterior.
Dos preguntas distintas con el mismo nombre.

Este script separa el universo en tres, que es lo que hace falta para decidir:

  A) SELLO DE ESCRITURA  → la columna elegida es un `timestamptz`. «Hace cuánto
     que no escribe» es literal y el detector actual sirve tal cual.
  B) FECHA DE NEGOCIO    → la tabla NO tiene ningún sello: la única columna
     temporal es un `date`. Ahí «hace cuánto» **no se puede contestar**, y la
     pregunta correcta es otra: ¿el último día cargado es el que corresponde?
     Para esas imprime el DESFASE EN DÍAS HÁBILES contra hoy — que es el número
     que hoy no existe en ninguna parte y sin el cual no se puede declarar el
     T-N de cada tabla (REGLA #2: se mide antes de codear).
  C) CAMBIÓ LA ELECCIÓN  → tablas donde la regla vieja (primera columna temporal
     del DDL) y la nueva (nunca un `date` si hay un sello) NO coinciden. Medido
     sobre `sql/schema.sql` son 26; esto dice cuántas son en la base REAL.

NO escribe nada. NO pega a ninguna API. Una query al catálogo + una `max()` por
tabla agrupadas en un solo viaje (la misma que el agente ya corre en cada pasada).

Uso (desde la raíz, en el Droplet):
    python -m scripts.diag_frescura_columna
    python -m scripts.diag_frescura_columna --solo-negocio   # solo el grupo B
"""
from __future__ import annotations

import argparse
from datetime import UTC, date, datetime

from agente import tablas
from core.calendario import es_habil, habiles_entre

# La regla VIEJA, copiada acá a propósito y solo para comparar: es lo único que
# permite decir «esta tabla cambia de columna» sin correr dos versiones del repo.
_VIEJA = ("updated_at", "ingestado_en", "generado_at", "creado_at", "created_at",
          "ts", "fecha", "concertacion", "hora")


def _eleccion_vieja(cands: list[str]) -> str | None:
    return next((c for c in _VIEJA if c in cands), cands[0] if cands else None)


def _titulo(t: str) -> None:
    print(f"\n{'=' * 78}\n{t}\n{'=' * 78}")


def _desfase_habiles(d: date, hoy: date) -> str:
    """Cuántos días HÁBILES atrás está `d`. Es la unidad correcta: un lunes, el
    día hábil anterior es el viernes, y tres días de calendario son UNO hábil."""
    if d >= hoy:
        return "T-0 (hoy o adelante)"
    habiles = habiles_entre(d, hoy)
    # `habiles_entre` es inclusivo en los dos bordes: los saltos son uno menos.
    n = max(len(habiles) - 1, 0)
    if not es_habil(hoy):
        return f"T-{n} hábiles (⚠️ hoy NO es hábil)"
    return f"T-{n} hábiles"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--solo-negocio", action="store_true",
                    help="solo las tablas sin sello de escritura (grupo B)")
    args = ap.parse_args()

    inv = tablas.inventario()
    con_col = [t for t in inv if t.get("col_fecha")]
    negocio = [t for t in con_col if t.get("es_fecha_negocio")]
    cambian = [t for t in con_col
               if _eleccion_vieja(list(t.get("cols_fecha") or [])) != t["col_fecha"]]

    _titulo("RESUMEN")
    print(f"  tablas en la base ................ {len(inv)}")
    print(f"  con alguna columna temporal ..... {len(con_col)}")
    print(f"  A) juzgadas por SELLO ........... {len(con_col) - len(negocio)}")
    print(f"  B) juzgadas por FECHA DE NEGOCIO  {len(negocio)}   ← el punto ciego que queda")
    print(f"  C) cambian de columna ........... {len(cambian)}   (estimado en repo: 26)")

    if not args.solo_negocio:
        _titulo("C) TABLAS QUE CAMBIAN DE COLUMNA — antes se medía el dato, ahora la escritura")
        if not cambian:
            print("  ninguna. La base real no tiene el desfase que muestra sql/schema.sql.")
        for t in sorted(cambian, key=lambda x: (x["schema"], x["tabla"])):
            viejo = _eleccion_vieja(list(t.get("cols_fecha") or []))
            print(f"  {t['schema']}.{t['tabla']:<34} {viejo}  →  {t['col_fecha']}")

    _titulo("B) SIN SELLO DE ESCRITURA — acá «hace cuánto no escribe» NO se puede contestar")
    if not negocio:
        print("  ninguna: todas las tablas tienen un timestamp de escritura.")
        return
    print("  Para cada una: el último día cargado y a cuántos días HÁBILES está de hoy.")
    print("  Ese número es el T-N natural de la tabla. Si es estable, la tabla está sana")
    print("  aunque el reloj diga que hace tres días que no escribe.\n")

    hoy = datetime.now(UTC).date()
    ult = tablas._ultimo_dato_vivo(negocio)
    # ⚠️ **NO SE ORDENA.** `_ultimo_dato_vivo` devuelve `{índice: valor}` con el
    # índice de la lista que RECIBIÓ: reordenar acá alinearía cada tabla con la
    # fecha de otra, y saldría un informe entero de números plausibles y falsos.
    for i, t in enumerate(negocio):
        v = ult.get(i)
        d = v.date() if isinstance(v, datetime) else v
        nombre = f"{t['schema']}.{t['tabla']}"
        if d is None:
            print(f"  {nombre:<46} {t['col_fecha']:<20} VACÍA")
            continue
        print(f"  {nombre:<46} {t['col_fecha']:<20} {d}  {_desfase_habiles(d, hoy)}")

    print(f"\n  (hoy = {hoy}, {'hábil' if es_habil(hoy) else 'NO hábil'})")
    print("\n  Qué hacer con esto: la columna DESFASE es lo que hay que declarar por tabla")
    print("  para que el detector deje de comparar una fecha de negocio contra el reloj.")


if __name__ == "__main__":
    main()
