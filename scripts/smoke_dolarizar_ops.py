"""scripts/smoke_dolarizar_ops.py — smoke read-only de la dolarización de OPERACIONES.

Antes de cablear el frontend, confirma (REGLA #2) que la opción nueva `USD_DOL`
del volumen da números sanos. Para una ventana reciente llama al MISMO servicio
SQL que usará el endpoint (api.services.operaciones_sql) y muestra el total de:

  - ARS      (volumen nativo en pesos)
  - USD      (volumen nativo en dólares)
  - USD_DOL  (volumen DOLARIZADO: ARS÷mep + USD, sin mep → 0)

Sanity esperado: USD_DOL ≳ USD (le suma el ARS convertido a USD) y de magnitud
razonable. Si USD_DOL == USD, algo no está convirtiendo el ARS.

Read-only. Correr en el Droplet:
    python -m scripts.smoke_dolarizar_ops
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from api.services import operaciones_sql as ops


def main() -> int:
    hoy = (datetime.now(UTC) - timedelta(hours=3)).date()  # ART
    desde = (hoy - timedelta(days=60)).isoformat()
    hasta = hoy.isoformat()
    print(f"Ventana: {desde} → {hasta}\n")
    print(f"  {'moneda':<10}{'total':>20}")
    print("  " + "-" * 30)
    totales = {}
    for moneda in ("ARS", "USD", "USD_DOL"):
        r = ops.ops_resumen(moneda=moneda, desde=desde, hasta=hasta)
        totales[moneda] = r["total"]
        print(f"  {moneda:<10}{r['total']:>20,.2f}")
    print()
    if totales["USD_DOL"] <= totales["USD"]:
        print("⚠ USD_DOL <= USD: el ARS no se está sumando convertido. Revisar.")
    else:
        ars_en_usd = totales["USD_DOL"] - totales["USD"]
        print(f"✓ USD_DOL = USD nativo + ARS convertido (~{ars_en_usd:,.0f} USD de ARS).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
