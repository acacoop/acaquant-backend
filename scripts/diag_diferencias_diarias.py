"""diag_diferencias_diarias.py — RADIOGRAFÍA (read-only) de las filas
"Diferencias diarias" en operaciones.negocio_movimientos.

Objetivo (REGLA #2 — verificar antes de codear): confirmar la forma REAL de
estas filas antes de construir la vista OPERACIONES → DIFERENCIAS DIARIAS.
Responde:
  1. ¿Cuántas filas hay? ¿Qué estado / categoria / moneda tienen?
  2. ¿El `importe` es la métrica? ¿signo (+/-)? ¿hay nulos?
  3. ¿cantidad / precio / ticker / op vienen nulos (no hay tipo ni instrumento)?
  4. ¿Cómo luce EXACTO el campo `informacion`? (muestra cruda)
  5. El "instrumento" hay que sacarlo del texto: parseo del token entre [ ] →
     top instrumentos por cantidad y por Σ importe.
  6. ¿Cuántas diferencias por día? (serie reciente + rango de fechas)

NO escribe nada. Un solo pase de lectura. Correr:
    python -m scripts.diag_diferencias_diarias
"""
from __future__ import annotations

from api.services._sql import _q

# Filtro canónico de la vista: el texto arranca con "Diferencias diarias".
FILTRO = "informacion ILIKE 'Diferencias diarias%%'"


def _sep(t: str) -> None:
    print("\n" + "=" * 70)
    print(t)
    print("=" * 70)


def main() -> None:
    _sep("1) VOLUMEN + DIMENSIONES (estado / categoria / moneda)")
    tot = _q(f"SELECT COUNT(*) AS n FROM negocio_movimientos WHERE {FILTRO}")
    print(f"Total filas 'Diferencias diarias': {tot[0]['n']}")

    for dim in ("estado", "categoria", "moneda"):
        print(f"\n  por {dim}:")
        for r in _q(
            f"SELECT COALESCE({dim}::text,'(null)') AS k, COUNT(*) AS n "
            f"FROM negocio_movimientos WHERE {FILTRO} GROUP BY 1 ORDER BY n DESC"
        ):
            print(f"    {r['k']:<24} {r['n']}")

    _sep("2) IMPORTE — ¿es la métrica? signo y nulos")
    r = _q(
        "SELECT COUNT(*) AS n, "
        "COUNT(*) FILTER (WHERE importe IS NULL) AS nulos, "
        "COUNT(*) FILTER (WHERE importe > 0) AS pos, "
        "COUNT(*) FILTER (WHERE importe < 0) AS neg, "
        "COUNT(*) FILTER (WHERE importe = 0) AS cero, "
        "ROUND(SUM(importe)::numeric, 2) AS suma, "
        "ROUND(MIN(importe)::numeric, 2) AS minimo, "
        "ROUND(MAX(importe)::numeric, 2) AS maximo "
        f"FROM negocio_movimientos WHERE {FILTRO}"
    )[0]
    print(f"  filas={r['n']}  nulos={r['nulos']}  >0={r['pos']}  <0={r['neg']}  =0={r['cero']}")
    print(f"  Σ importe={r['suma']}  min={r['minimo']}  max={r['maximo']}")

    _sep("3) ¿Hay tipo/instrumento nativos? (cantidad/precio/ticker/op null)")
    r = _q(
        "SELECT "
        "COUNT(*) FILTER (WHERE cantidad IS NOT NULL) AS con_cant, "
        "COUNT(*) FILTER (WHERE precio  IS NOT NULL) AS con_prec, "
        "COUNT(*) FILTER (WHERE ticker  IS NOT NULL AND ticker  <> '') AS con_ticker, "
        "COUNT(*) FILTER (WHERE op      IS NOT NULL AND op      <> '') AS con_op "
        f"FROM negocio_movimientos WHERE {FILTRO}"
    )[0]
    print(f"  con cantidad={r['con_cant']}  con precio={r['con_prec']}  "
          f"con ticker={r['con_ticker']}  con op={r['con_op']}")
    print("  (si todo ~0 → confirma: NO hay tipo de operación ni instrumento nativo)")

    _sep("4) MUESTRA CRUDA de `informacion` (30 filas variadas)")
    for r in _q(
        "SELECT fecha, moneda, ROUND(importe::numeric,2) AS importe, informacion "
        f"FROM negocio_movimientos WHERE {FILTRO} "
        "ORDER BY fecha DESC LIMIT 30"
    ):
        print(f"  {r['fecha']}  {str(r['moneda']):<5} {str(r['importe']):>14}  | {r['informacion']}")

    _sep("5) INSTRUMENTO parseado del texto (token entre corchetes)")
    print("  regex SQL: substring(informacion from '\\[([^\\]]+)\\]')  → top 30")
    for r in _q(
        "SELECT COALESCE(substring(informacion from '\\[([^\\]]+)\\]'), '(sin corchete)') AS instr, "
        "COUNT(*) AS n, ROUND(SUM(importe)::numeric,2) AS suma "
        f"FROM negocio_movimientos WHERE {FILTRO} "
        "GROUP BY 1 ORDER BY n DESC LIMIT 30"
    ):
        print(f"    {str(r['instr']):<22} n={str(r['n']):>5}  Σ importe={r['suma']}")

    sin = _q(
        "SELECT COUNT(*) AS n FROM negocio_movimientos "
        f"WHERE {FILTRO} AND substring(informacion from '\\[([^\\]]+)\\]') IS NULL"
    )[0]["n"]
    print(f"\n  filas SIN token entre corchetes: {sin} (si >0, hay que ver ese formato)")

    _sep("6) DIFERENCIAS POR DÍA (rango + últimos 20 días con datos)")
    rng = _q(f"SELECT MIN(fecha) AS mn, MAX(fecha) AS mx FROM negocio_movimientos WHERE {FILTRO}")[0]
    print(f"  rango de fechas: {rng['mn']} → {rng['mx']}")
    print("\n  últimos 20 días:")
    for r in _q(
        "SELECT fecha, COUNT(*) AS n, ROUND(SUM(importe)::numeric,2) AS suma "
        f"FROM negocio_movimientos WHERE {FILTRO} "
        "GROUP BY fecha ORDER BY fecha DESC LIMIT 20"
    ):
        print(f"    {r['fecha']}  n={str(r['n']):>4}  Σ importe={r['suma']}")


if __name__ == "__main__":
    main()
