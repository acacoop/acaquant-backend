"""scripts/limpiar_ap5_portfolio.py — dejar en `ap5.portfolio` SOLO el último día.

⚠️⚠️ **LEER ESTO ANTES DE CORRERLO CON `--aplicar`.**

`ap5.portfolio` guarda la posición de CADA día hábil, y el acumulado de la vista
se calcula así::

    acumulado = arrastre cargado + Σ daily_settlement de los días POSTERIORES

o sea que **los días viejos NO son histórico decorativo: son los sumandos**.
Dejando un solo día, esa Σ pasa a valer un día, y el acumulado de mañana
reemplaza al de hoy en vez de sumarse. El número baja y **nada falla**.

Por eso este script hace tres cosas antes de borrar, y ninguna es opcional:

1. **Exporta a CSV todo lo que va a borrar.** Es lo que hace al borrado
   reversible sin depender de que la cámara siga sirviendo esas fechas.
2. **Muestra el impacto en el acumulado**: cuántas cuentas tienen el arrastre
   con fecha ANTERIOR al día que queda, o sea a cuáles se les pierden sumandos.
3. **DRY por defecto.** Sin `--aplicar` no borra nada.

⚠️ El borrado ES recuperable por dos vías: el CSV de este script, y volver a
pedirle el día a la cámara (`python -m jobs.ap5_portfolio --fecha AAAAMMDD`),
que re-escribe ese día exacto — la PK incluye `business_date`, así que no pisa
los demás.

Uso:
    python -m scripts.limpiar_ap5_portfolio                 # mira, no borra
    python -m scripts.limpiar_ap5_portfolio --aplicar       # deja SOLO el último día
    python -m scripts.limpiar_ap5_portfolio --dejar 5 --aplicar
"""
from __future__ import annotations

import argparse
import csv
from datetime import UTC, datetime
from pathlib import Path

from core.postgres import get_pool

COLUMNAS = (
    "business_date", "account", "symbol", "position_type", "side", "cfi_code",
    "unit_of_measure", "currency", "avg_px", "daily_settlement",
    "settlement_price", "settlement_currency", "long_qty", "short_qty",
    "actualizado_at",
)


def _q(sql: str, params: dict | None = None) -> list[dict]:
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params or {})
        if cur.description is None:
            return []
        cols = [c.name for c in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Dejar en ap5.portfolio sólo los últimos N días.")
    ap.add_argument("--dejar", type=int, default=1,
                    help="cuántos días hábiles conservar (default 1: sólo el último)")
    ap.add_argument("--aplicar", action="store_true", help="borrar de verdad")
    ap.add_argument("--salida", default="/root",
                    help="dónde dejar el CSV del respaldo (default /root)")
    args = ap.parse_args()

    dias = _q("SELECT to_char(business_date,'YYYY-MM-DD') AS dia, count(*) AS n, "
              "       max(actualizado_at) AS escrito "
              "FROM ap5.portfolio GROUP BY 1 ORDER BY 1 DESC")
    if not dias:
        print("ap5.portfolio está vacía. No hay nada que borrar.")
        return

    quedan = [d["dia"] for d in dias[: args.dejar]]
    borrar = [d["dia"] for d in dias[args.dejar:]]

    print("=" * 78)
    print(f"ap5.portfolio · {len(dias)} día(s) · "
          f"{sum(d['n'] for d in dias):,} filas")
    print("=" * 78)
    for d in dias:
        que = "QUEDA" if d["dia"] in quedan else "se BORRA"
        print(f"  {d['dia']}  {d['n']:>6} filas   {que}")

    if not borrar:
        print(f"\nNo hay nada que borrar: ya hay {len(dias)} día(s) o menos.")
        return

    # ── el impacto en el acumulado, ANTES de tocar nada ───────────────────
    corte = min(quedan)
    riesgo = _q(
        "SELECT count(*) AS n FROM ap5.acumulado "
        "WHERE actualizado IS NOT NULL AND fecha < %(c)s::date",
        {"c": corte},
    )[0]["n"]
    perdido = _q(
        "SELECT p.settlement_currency AS moneda, "
        "       round(sum(p.daily_settlement)::numeric, 2) AS suma "
        "FROM ap5.portfolio p "
        "WHERE p.business_date < %(c)s::date "
        "GROUP BY 1 ORDER BY 1",
        {"c": corte},
    )
    print("\n  ── IMPACTO EN EL ACUMULADO " + "─" * 46)
    print(f"  {riesgo} cuenta(s) tienen el arrastre con fecha ANTERIOR a {corte}.")
    print("  A ésas se les caen sumandos: su acumulado va a BAJAR y no va a")
    print("  fallar nada — el número sigue siendo plausible.")
    if perdido:
        print("\n  Lo que dejaría de sumarse, por moneda:")
        for r in perdido:
            print(f"    {r['moneda'] or '(sin moneda)'!s:<14} "
                  f"{float(r['suma'] or 0):>18,.2f}")
    print("\n  Se arregla re-cargando el arrastre con `--fecha` = el día que")
    print("  queda, ya conteniendo todo lo anterior. Si no, el número miente.")

    # ── el respaldo ───────────────────────────────────────────────────────
    sello = datetime.now(UTC).strftime("%Y%m%d_%H%M")
    ruta = Path(args.salida) / f"ap5_portfolio_respaldo_{sello}.csv"
    filas = _q(
        f"SELECT {', '.join(COLUMNAS)} FROM ap5.portfolio "
        "WHERE business_date < %(c)s::date "
        "ORDER BY business_date, account, symbol",
        {"c": corte},
    )
    if not args.aplicar:
        print(f"\n  DRY: no se borró nada. Se borrarían {len(filas):,} filas de "
              f"{len(borrar)} día(s).")
        print("  Agregá --aplicar cuando quieras hacerlo de verdad.")
        return

    with ruta.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(COLUMNAS), delimiter=";")
        w.writeheader()
        w.writerows(filas)
    print(f"\n  ✓ respaldo: {ruta}  ({len(filas):,} filas)")

    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM ap5.portfolio WHERE business_date < %(c)s::date",
                    {"c": corte})
        borradas = cur.rowcount
    print(f"  ✓ {borradas:,} filas borradas · quedan los días {quedan}")
    print(f"\n  Para revivir un día: python -m jobs.ap5_portfolio "
          f"--fecha {borrar[0].replace('-', '')}")


if __name__ == "__main__":
    main()
