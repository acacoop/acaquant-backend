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

## De dónde sale cada pata (y por qué esto es seguro)

**La primaria NO se adivina: ya está escrita.** Los duales están archivados hoy en
`curva='cer'` o `curva='tamar'`, y esa es la clasificación que hizo la mesa. O sea
que una de las dos patas es un dato existente y verificado, no una inferencia.

**La segunda NO la sabe la base.** 1816 tampoco: su curva se llama "Soberanos
Duales" y no dice el par. Así que este script **deja `ajuste_alt` en NULL** y
reporta cuáles quedan pendientes. Completarla es carga humana (o de otra fuente),
y hasta que pase el bono se comporta EXACTAMENTE como hoy: sigue apareciendo en su
tabla de siempre. Nada desaparece de la pantalla — que es el modo de fallar que
importa acá, porque es silencioso.

Inventar la segunda pata a partir del prefijo del ticker sería una heurística sin
medir sobre 8 filas: no se hace.

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

_SEP = "=" * 92


def _q(sql: str, params: tuple = ()) -> list[dict]:
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r, strict=False)) for r in cur.fetchall()]


def planificar(filas: list[dict]) -> tuple[list[dict], list[dict]]:
    """`(migrables, sin_pata)`. PURA — el universo entra por parámetro.

    Migrable = su `curva` actual nombra un ajuste válido, o sea que la mesa ya
    dijo cuál es una de las dos patas. Si la curva no lo dice (un dual archivado
    en `on_otros`, por ejemplo), NO se toca: elegir la pata a dedo es justo lo que
    este script no hace.
    """
    migrables, sin_pata = [], []
    for f in filas:
        pata = (f.get("curva") or "").strip().lower()
        if pata in _PATAS:
            migrables.append({"ticker": f["ticker"], "ajuste": pata,
                              "curva": f.get("curva")})
        else:
            sin_pata.append(f)
    return migrables, sin_pata


def _duales() -> list[dict]:
    return _q("""
        SELECT ticker, curva, emisor, emisor_tipo, moneda_eje, ajuste, ajuste_alt
        FROM mercado.curvas
        WHERE lower(COALESCE(ajuste, '')) = 'dual'
        ORDER BY curva, ticker
    """)


def aplicar(migrables: list[dict]) -> int:
    """Escribe la pata primaria. `ajuste_alt` queda como está (NULL) — este script
    NO la completa: no tiene de dónde sacarla."""
    if not migrables:
        return 0
    with get_pool().connection() as conn, conn.cursor() as cur:
        for m in migrables:
            cur.execute("UPDATE mercado.curvas SET ajuste = %(ajuste)s "
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

    migrables, sin_pata = planificar(filas)
    print(f"\n  duales encontrados: {len(filas)} · con pata identificable: "
          f"{len(migrables)} · sin identificar: {len(sin_pata)}")

    if migrables:
        print(f"\n  {'TICKER':<12}{'ARCHIVADO EN':<16}{'PATA 1 (queda)':<18}PATA 2")
        print("  " + "-" * 74)
        for m in migrables:
            print(f"  {m['ticker'][:11]:<12}curva={str(m['curva'])[:9]:<10}"
                  f"{m['ajuste']:<18}⏳ pendiente (carga manual)")
        print("  " + "-" * 74)
        print("  La PATA 1 no se inventa: es la curva donde la mesa ya lo tenía")
        print("  archivado. Mientras la PATA 2 esté vacía el bono se comporta igual")
        print("  que hoy — sigue apareciendo en su tabla de siempre, no se cae de")
        print("  la pantalla. Al completarla, aparece también en la segunda.")

    if sin_pata:
        print(f"\n  ⚠ SIN PATA IDENTIFICABLE ({len(sin_pata)}) — no se tocan. Su `curva`")
        print("    no nombra un ajuste válido, así que elegirla sería adivinar:")
        for f in sin_pata:
            print(f"      {f['ticker']:<12} curva={f['curva']!r}  emisor={f['emisor']}")

    if not args.aplicar:
        print("\n  [DRY-RUN] no se escribió nada. Con --aplicar se hace.")
        return

    n = aplicar(migrables)
    print(f"\n  ✅ {n} dual(es) con su pata primaria escrita.")
    print("  Falta la SEGUNDA de cada uno — hasta entonces la vista no cambia.")
    print("  El paso siguiente (pills por `ajuste` OR `ajuste_alt`, y sacar la tab")
    print("  DUALES) va DESPUÉS de completarlas, nunca antes.")


if __name__ == "__main__":
    main()
