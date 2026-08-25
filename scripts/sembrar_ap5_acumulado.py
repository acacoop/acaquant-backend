"""scripts/sembrar_ap5_acumulado.py — crear la grilla de `ap5.acumulado`.

Pedido del user: tener una fila por cuenta en `ap5.acumulado` para poder ir
cargando el arrastre a mano, en vez de crearlas de a una.

## Por qué NO se puede copiar solo `account`

`ap5.acumulado` tiene PK **(account, currency)** y las dos columnas son NOT
NULL. `ap5.cuentas` **no tiene moneda** — la moneda solo existe en
`ap5.portfolio`, y la PK lleva moneda por una razón medida: cuatro cuentas
(221369, 222812, 229540, 229664) tienen agro en Dólar MtR y dólar futuro en
Pesos **a la vez**. Con una fila por cuenta, el acumulado sumaría pesos con
dólares: daría un número, no fallaría nada, y estaría mal.

Así que la grilla se arma con los pares (cuenta, moneda) que REALMENTE
aparecieron en la posición. Una cuenta de `ap5.cuentas` que nunca tuvo posición
no se puede sembrar —no hay de dónde sacarle la moneda— y se REPORTA en vez de
inventarle una.

## ⚠️ `desde_fecha` es EXCLUSIVA, y acá está la trampa

El acumulado suma los días **POSTERIORES** a `desde_fecha` (la semilla ya
contiene el arrastre hasta ese día inclusive). Si se sembrara con el primer día
de nuestra serie, **ese día dejaría de contar** y el acumulado bajaría sin que
nada falle.

Por eso se siembra con `min(business_date) - 1 día`: así entran TODOS los días
guardados y el número no se mueve ni un peso. Con `semilla = 0`, sembrar es
matemáticamente idéntico a no tener fila.

## ⚠️ Lo que SÍ cambia: la vista deja de poder avisar

Sin fila, la vista sabe que el arrastre no está cargado (`semilla_cargada =
false`). Con fila y `semilla = 0` no puede distinguir "el arrastre es cero" de
"todavía no lo cargué" — y esto se imprime como PDF para gerencia.

Por eso cada fila sembrada nace con `nota = 'pendiente: arrastre sin cargar'`.
Es la marca que permite seguir contándolas. Cuando cargues la semilla de
verdad, borrá la nota (o escribí la tuya) y esa cuenta sale del pendiente.

**Nunca pisa una fila existente** (`ON CONFLICT DO NOTHING`): mismo invariante
que `ap5.cuentas.name` y el `grupo` — lo automático completa, lo humano manda.

Uso:
    python -m scripts.sembrar_ap5_acumulado --dry    # muestra y NO escribe
    python -m scripts.sembrar_ap5_acumulado
"""
from __future__ import annotations

import argparse

from core.postgres import get_pool

NOTA_PENDIENTE = "pendiente: arrastre sin cargar"

# Una fila por (cuenta, moneda) REALMENTE observada en la posición.
#
# `desde_fecha` = el primer día de la serie MENOS UNO, para que todos los días
# guardados sigan sumando (la fecha es exclusiva). Con semilla 0, sembrar no
# mueve ningún número: solo crea la fila para poder cargar el arrastre encima.
_SQL_INSERT = """
    INSERT INTO ap5.acumulado (account, currency, semilla, desde_fecha, nota, cargado_por)
    SELECT DISTINCT p.account,
           p.settlement_currency,
           0,
           (SELECT min(business_date) - 1 FROM ap5.portfolio),
           %(nota)s,
           'sembrar_ap5_acumulado'
    FROM ap5.portfolio p
    WHERE p.settlement_currency IS NOT NULL
    ON CONFLICT (account, currency) DO NOTHING
"""

_SQL_PREVIEW = """
    SELECT DISTINCT p.account, p.settlement_currency AS moneda
    FROM ap5.portfolio p
    LEFT JOIN ap5.acumulado a
           ON a.account = p.account AND a.currency = p.settlement_currency
    WHERE p.settlement_currency IS NOT NULL AND a.account IS NULL
    ORDER BY 1, 2
"""

# Cuentas del padrón que NUNCA tuvieron posición: no tienen moneda, así que no
# se pueden sembrar. Se reportan en vez de inventarles una.
_SQL_SIN_POSICION = """
    SELECT c.account FROM ap5.cuentas c
    WHERE NOT EXISTS (SELECT 1 FROM ap5.portfolio p WHERE p.account = c.account)
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
        print("ap5.portfolio está vacía: no hay de dónde sacar las monedas. Corré el job primero.")
        return
    primero, ultimo, filas = rango[0]

    ya = _q("SELECT count(*) FROM ap5.acumulado")[0][0]
    faltan = _q(_SQL_PREVIEW)
    sin_pos = [r[0] for r in _q(_SQL_SIN_POSICION)]

    print(f"ap5.portfolio: {filas:,} filas, del {primero} al {ultimo}")
    print(f"ap5.acumulado: {ya} filas hoy\n")
    print(f"Pares (cuenta, moneda) a sembrar: {len(faltan)}")
    print(f"  desde_fecha = {primero} − 1 día  (EXCLUSIVA: así el {primero} sigue sumando)")
    print("  semilla     = 0  → el acumulado NO se mueve ni un peso\n")

    if faltan:
        for cuenta, moneda in faltan[:15]:
            print(f"    {cuenta:<10} {moneda}")
        if len(faltan) > 15:
            print(f"    … y {len(faltan) - 15} más")

    if sin_pos:
        print(f"\n⚠️  {len(sin_pos)} cuentas de ap5.cuentas NUNCA tuvieron posición: no se "
              "pueden sembrar porque no tienen moneda, y la moneda es parte de la clave.")
        print("    " + ", ".join(sin_pos[:20]) + (" …" if len(sin_pos) > 20 else ""))

    if args.dry:
        print("\n--dry: no se escribió nada.")
        return

    if not faltan:
        print("\nNada para sembrar: ya están todas.")
        return

    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(_SQL_INSERT, {"nota": NOTA_PENDIENTE})
        escritas = cur.rowcount

    print(f"\n✓ {escritas} filas creadas en ap5.acumulado (semilla 0, nota "
          f"'{NOTA_PENDIENTE}').")
    print("  Cargá el arrastre en la columna `semilla` y borrá la nota cuando esté.")
    print("  Las filas que ya existían NO se tocaron.")


if __name__ == "__main__":
    main()
