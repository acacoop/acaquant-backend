"""scripts/migrar_duales.py — un dual deja de ser una familia y pasa a tener DOS patas.

## Qué arregla

Hoy un dual se guarda como `ajuste='dual'`. Eso está mal por dos motivos, y el
segundo es el grave:

  1. Lo saca de las tablas donde el trader lo busca. Un dual NO es una tercera
     categoría al lado de CER y TAMAR: es un bono con dos patas de rendimiento, y
     por eso se lo mira en las DOS tablas. `dual` lo esconde de ambas y lo manda a
     una pill propia que nadie pidió.
  2. **Destruye el dato.** `dual` no dice contra qué ajusta. Guardando eso se
     perdió que uno es CER+TAMAR y otro CER+devaluación: son bonos distintos
     escritos igual.

El modelo nuevo es `ajuste` (pata principal) + `ajuste_alt` (la segunda), y con eso
"es dual" deja de cargarse a mano — se deduce de que `ajuste_alt` no esté vacío.

## De dónde sale cada pata

Dos fuentes, las dos ya en la base (medido con `scripts.diag_migrar_curva`):

  · **`curva`** — dónde lo archivó la mesa (`cer` o `tamar`).
  · **`data->>'tasa_referencia'`** — la tasa variable contra la que ajusta. Dice
    `TAMAR` en los 5 duales que la tienen cargada.

**1816 NO sirve para esto y está medido, no supuesto**: su ficha trae 9 campos, los
9 ya los guardamos, y la denominación de los ocho es `GOB ARS ARG DUAL (<ticker>)`
— dice "dual" y nada más.

## La trampa que encontró la medición

Parecía que `curva` era siempre una de las dos patas. **No lo es.** En TMVE8, TTD26
y TTS26 `curva='tamar'` Y `tasa_referencia='TAMAR'`: las dos fuentes dicen LO MISMO.
Escribirlas igual daría un bono "dual consigo mismo" — que no significa nada y, peor,
**suma bien y se ve prolijo en pantalla**. Para esos tres la segunda pata no está en
ningún lado: no tienen `cupon_anual`, ni `cer_emision`, y sus flujos traen solo
`amortizacion_pct` y `fecha`.

Por eso el script **se niega a escribir cuando las dos patas coinciden** y lo reporta
como conflicto. Es el invariante central: un dual es dual porque sus patas son
DISTINTAS.

## Qué escribe y qué no

  · las dos patas conocidas y distintas  → escribe las dos
  · solo la primaria                     → escribe la primaria, `ajuste_alt` NULL
  · las dos iguales, o `tasa_referencia` desconocida → NO TOCA, reporta

En los tres casos el bono **sigue apareciendo donde aparece hoy**: nunca se cae de
la pantalla, que es el modo de fallar que importa acá porque es silencioso.
Inventar una pata a partir del prefijo del ticker sería una heurística sin medir
sobre 8 filas: no se hace.

## Orden (esto no es opcional)

Este script va ANTES del cambio de la vista. Si primero se saca la pill DUALES y
los bonos siguen con `ajuste='dual'`, se quedan sin ninguna pill y se caen de la
pantalla sin error y sin log.

DRY-RUN por default (REGLA #4). Idempotente: re-correrlo no cambia nada.

Uso:
    python -m scripts.migrar_duales             # qué haría
    python -m scripts.migrar_duales --aplicar   # lo hace
"""
from __future__ import annotations

import argparse

from core.curvas_ejes import AJUSTES
from core.postgres import get_pool

# Las patas VÁLIDAS son los ajustes menos `dual`: `dual` deja de ser un ajuste y
# pasa a ser una CONSECUENCIA de tener dos. Se deriva de `AJUSTES` en vez de
# escribirse a mano para que no haya dos listas que se puedan contradecir — el día
# que se sume un ajuste nuevo, entra acá solo.
_PATAS = tuple(a for a in AJUSTES if a != "dual")

# `data->>'tasa_referencia'` → eje. Es un campo de texto libre que carga la mesa;
# se traduce con una TABLA y no normalizando a lo bruto, para que un valor nuevo
# se REPORTE en vez de colarse mal escrito. Hoy los 5 que lo tienen dicen `TAMAR`.
_TASA_A_EJE = {
    "TAMAR": "tamar", "CER": "cer", "BADLAR": "badlar",
    "TPM": "tpm", "FIJA": "fija", "DOLAR LINKED": "dolar_linked",
}


def eje_de_tasa(txt: str | None) -> str | None:
    """`'TAMAR'` → `'tamar'`. `None` si está vacío o no está en la tabla. PURA."""
    return _TASA_A_EJE.get((txt or "").strip().upper())

_SEP = "=" * 92


def _q(sql: str, params: tuple = ()) -> list[dict]:
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r, strict=False)) for r in cur.fetchall()]


def planificar(filas: list[dict]) -> tuple[list[dict], list[dict]]:
    """`(migrables, bloqueados)`. PURA — el universo entra por parámetro.

    Cada fila sale con un `estado` que dice exactamente qué se sabe:

      · `completo`   — las dos patas, y son DISTINTAS. Se escriben las dos.
      · `parcial`    — solo la primaria. Se escribe esa; `ajuste_alt` queda NULL.
      · `degenerado` — las dos fuentes dicen lo mismo (pasa en TMVE8/TTD26/TTS26).
                       NO se toca: un dual consigo mismo no significa nada.
      · `sin_pata`   — la `curva` no nombra un ajuste válido.
      · `tasa_rara`  — `tasa_referencia` tiene un valor que no está en la tabla.

    Los dos últimos y `degenerado` se REPORTAN y no se escriben. Elegir una pata a
    dedo es justo lo que este script no hace.
    """
    migrables, bloqueados = [], []
    for f in filas:
        base = {"ticker": f["ticker"], "curva": f.get("curva"),
                "tasa_referencia": f.get("tasa_referencia")}
        pata1 = (f.get("curva") or "").strip().lower()
        if pata1 not in _PATAS:
            bloqueados.append({**base, "estado": "sin_pata"})
            continue

        cruda = (f.get("tasa_referencia") or "").strip()
        pata2 = eje_de_tasa(cruda)
        if cruda and pata2 is None:
            bloqueados.append({**base, "estado": "tasa_rara"})
            continue
        if pata2 == pata1:
            # LA TRAMPA: las dos fuentes coinciden → no hay par. Escribirlo daría
            # un dual consigo mismo, que suma bien y no significa nada.
            bloqueados.append({**base, "estado": "degenerado", "ajuste": pata1})
            continue

        migrables.append({**base, "ajuste": pata1, "ajuste_alt": pata2,
                          "estado": "completo" if pata2 else "parcial"})
    return migrables, bloqueados


def _duales() -> list[dict]:
    return _q("""
        SELECT ticker, curva, emisor, emisor_tipo, moneda_eje, ajuste, ajuste_alt,
               data->>'tasa_referencia' AS tasa_referencia
        FROM mercado.curvas
        WHERE lower(COALESCE(ajuste, '')) = 'dual'
        ORDER BY curva, ticker
    """)


def aplicar(migrables: list[dict]) -> int:
    """Escribe las patas. El `WHERE ... = 'dual'` hace la escritura idempotente y
    la protege de pisar un bono que alguien ya corrigió a mano."""
    if not migrables:
        return 0
    with get_pool().connection() as conn, conn.cursor() as cur:
        for m in migrables:
            cur.execute("UPDATE mercado.curvas "
                        "SET ajuste = %(ajuste)s, ajuste_alt = %(ajuste_alt)s "
                        "WHERE ticker = %(ticker)s "
                        "AND lower(COALESCE(ajuste, '')) = 'dual'", m)
        conn.commit()
    return len(migrables)


def _falta_columna() -> bool:
    return not _q("""
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'mercado' AND table_name = 'curvas'
          AND column_name = 'ajuste_alt'
    """)


def main() -> None:
    ap = argparse.ArgumentParser(description="Duales: de una familia a dos patas")
    ap.add_argument("--aplicar", action="store_true", help="escribe (default: dry-run)")
    args = ap.parse_args()

    print(_SEP)
    print("DUALES — de `ajuste='dual'` a dos patas (`ajuste` + `ajuste_alt`)")
    print(_SEP)

    if _falta_columna():
        print("\n🛑 falta la columna `ajuste_alt` en mercado.curvas.")
        print("   Corré primero:  python -m scripts.apply_schema")
        return

    filas = _duales()
    if not filas:
        ya = _q("SELECT count(*) n FROM mercado.curvas WHERE ajuste_alt IS NOT NULL")[0]["n"]
        print(f"\n✅ no queda ningún `ajuste='dual'`. Duales con las dos patas: {ya}.")
        print("   (idempotente: esto es lo que se espera al re-correrlo)")
        return

    migrables, bloqueados = planificar(filas)
    completos = [m for m in migrables if m["estado"] == "completo"]
    parciales = [m for m in migrables if m["estado"] == "parcial"]
    print(f"\n  duales: {len(filas)} · con las DOS patas: {len(completos)} · "
          f"solo una: {len(parciales)} · bloqueados: {len(bloqueados)}")

    if migrables:
        print(f"\n  {'TICKER':<10}{'curva':<10}{'tasa_referencia':<18}"
              f"{'PATA 1':<12}PATA 2")
        print("  " + "-" * 76)
        for m in migrables:
            p2 = m["ajuste_alt"] or "⏳ pendiente"
            print(f"  {m['ticker'][:9]:<10}{str(m['curva'])[:9]:<10}"
                  f"{str(m['tasa_referencia'] or '—')[:17]:<18}"
                  f"{m['ajuste']:<12}{p2}")
        print("  " + "-" * 76)
        print("  Ninguna pata se inventa: la 1 es dónde la mesa lo archivó y la 2")
        print("  es `tasa_referencia` del blob. Con la PATA 2 vacía el bono se")
        print("  comporta igual que hoy — sigue en su tabla de siempre, no se cae")
        print("  de la pantalla. Al completarla aparece también en la segunda.")

    for estado, titulo, expl in (
        ("degenerado", "LAS DOS PATAS DAN LO MISMO",
         "`curva` y `tasa_referencia` dicen el mismo ajuste, así que no hay par. "
         "Un dual\n    consigo mismo no significa nada — y el peligro es que "
         "sumaría bien igual.\n    La otra pata no está en la base: sin "
         "`cupon_anual`, sin `cer_emision` y con\n    flujos que solo traen "
         "`amortizacion_pct` y `fecha`. Hay que cargarla."),
        ("sin_pata", "SIN PATA PRIMARIA",
         "Su `curva` no nombra un ajuste válido, así que elegirla sería adivinar."),
        ("tasa_rara", "`tasa_referencia` DESCONOCIDA",
         "El valor no está en la tabla de traducción. Se suma a mano en "
         "`_TASA_A_EJE`\n    (a propósito: así un valor nuevo se reporta en vez de "
         "colarse mal escrito)."),
    ):
        grupo = [b for b in bloqueados if b["estado"] == estado]
        if not grupo:
            continue
        print(f"\n  🛑 {titulo} ({len(grupo)}) — NO se tocan.")
        print(f"    {expl}")
        for b in grupo:
            print(f"      {b['ticker']:<10} curva={str(b['curva'])!r:<10} "
                  f"tasa_referencia={str(b['tasa_referencia'] or '—')!r}")

    if not args.aplicar:
        print("\n  [DRY-RUN] no se escribió nada. Con --aplicar se hace.")
        return

    n = aplicar(migrables)
    print(f"\n  ✅ {n} dual(es) escritos ({len(completos)} con las dos patas, "
          f"{len(parciales)} con una).")
    print("  La vista NO cambia todavía: eso es el paso siguiente (pills por")
    print("  `ajuste` OR `ajuste_alt` y sacar la tab DUALES) y va DESPUÉS de que")
    print("  no quede ningún bloqueado, nunca antes.")


if __name__ == "__main__":
    main()
