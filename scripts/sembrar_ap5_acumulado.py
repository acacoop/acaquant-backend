"""scripts/sembrar_ap5_acumulado.py — crear la grilla de `ap5.acumulado`.

Una fila por CUENTA, con las DOS monedas en cero, para poder ir cargando el
arrastre encima en vez de crear las filas de a una.

## Lo que hace y lo que NO cambia

`ap5.acumulado` tiene una fila por cuenta y una columna por moneda
(`acumulado_pesos` / `acumulado_mtr`), así que **cada cuenta tiene sus dos
monedas por construcción** — no hace falta que la cuenta haya operado en las dos.

Se siembra con los dos importes en **0**, y eso es matemáticamente idéntico a no
tener fila: el acumulado que muestra la vista no se mueve ni un peso. Está
medido contra Postgres real.

## ⚠️ `fecha` es EXCLUSIVA, y acá está la trampa

El acumulado suma los días **POSTERIORES** a `fecha` (los importes ya contienen
todo hasta ese día inclusive). Si se sembrara con el primer día de nuestra serie,
**ese día dejaría de contar** y el acumulado bajaría sin que nada falle.

Por eso se siembra con `min(business_date) - 1 día`: así entran TODOS los días
guardados.

## ⚠️ `actualizado` queda en NULL a propósito

Es lo único que distingue "el arrastre es cero" de "todavía no lo cargué", y hace
falta porque esta vista se imprime como PDF para gerencia: un cero que nadie
escribió se lee igual que uno verificado. Se sella solo cuando una persona
guarda desde la vista.

**Nunca pisa una fila existente** (`ON CONFLICT DO NOTHING`): mismo invariante
que `ap5.cuentas.name` y el `grupo` — lo automático completa, lo humano manda.

Uso:
    python -m scripts.sembrar_ap5_acumulado --dry    # muestra y NO escribe
    python -m scripts.sembrar_ap5_acumulado
"""
from __future__ import annotations

import argparse

from core.postgres import get_pool

# Todas las cuentas del padrón, no solo las que tienen posición hoy: la fila es
# por CUENTA y no depende de la moneda, así que no hace falta que haya operado.
_SQL_INSERT = """
    INSERT INTO ap5.acumulado (account, acumulado_pesos, acumulado_mtr, fecha)
    SELECT c.account, 0, 0, (SELECT min(business_date) - 1 FROM ap5.portfolio)
    FROM ap5.cuentas c
    ON CONFLICT (account) DO NOTHING
"""

_SQL_FALTAN = """
    SELECT c.account, COALESCE(NULLIF(c.name, ''), c.denominacion, c.account) AS nombre
    FROM ap5.cuentas c
    LEFT JOIN ap5.acumulado a ON a.account = c.account
    WHERE a.account IS NULL
    ORDER BY 1
"""


def _q(sql: str, params: dict | None = None) -> list[tuple]:
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params or {})
        return cur.fetchall()


def main() -> None:
    ap = argparse.ArgumentParser(description="Crea la grilla de ap5.acumulado.")
    ap.add_argument("--dry", action="store_true", help="muestra qué haría, sin escribir")
    args = ap.parse_args()

    rango = _q("SELECT min(business_date), max(business_date), count(*) FROM ap5.portfolio")
    if not rango or rango[0][0] is None:
        print("ap5.portfolio está vacía: no hay serie de la que sacar la fecha. "
              "Corré `python -m jobs.ap5_portfolio` primero.")
        return
    primero, ultimo, filas = rango[0]

    ya = _q("SELECT count(*) FROM ap5.acumulado")[0][0]
    padron = _q("SELECT count(*) FROM ap5.cuentas")[0][0]
    faltan = _q(_SQL_FALTAN)

    print(f"ap5.portfolio: {filas:,} filas, del {primero} al {ultimo}")
    print(f"ap5.cuentas:   {padron} cuentas")
    print(f"ap5.acumulado: {ya} filas hoy\n")
    print(f"Filas a crear: {len(faltan)}  (una por cuenta, con SUS DOS monedas en 0)")
    print(f"  fecha       = {primero} − 1 día  (EXCLUSIVA: así el {primero} sigue sumando)")
    print("  actualizado = NULL  → marca que todavía no lo cargó nadie")
    print("  → el acumulado que muestra la vista NO se mueve ni un peso\n")

    for cuenta, nombre in faltan[:15]:
        print(f"    {cuenta:<10} {nombre[:50]}")
    if len(faltan) > 15:
        print(f"    … y {len(faltan) - 15} más")

    if args.dry:
        print("\n--dry: no se escribió nada.")
        return
    if not faltan:
        print("\nNada para sembrar: ya están todas.")
        return

    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(_SQL_INSERT)
        escritas = cur.rowcount

    print(f"\n✓ {escritas} filas creadas en ap5.acumulado.")
    print("  Cargá el arrastre en `acumulado_pesos` y `acumulado_mtr`.")
    print("  Las filas que ya existían NO se tocaron.")


if __name__ == "__main__":
    main()
