"""scripts/cargar_volumen_agro.py — carga manual del volumen TOTAL del mercado agro en SQL.

SQL-NATIVE (decomiso Mongo): reemplaza la carga manual en `CashFlow.VolumenMercadoAgro`.
Es el DENOMINADOR del share AGRO (/ops/agro `serie_share`: nuestro / mercado). Lo lee
`api/services/cashflow_sql.volumen_mercado_agro` (flag `VOLUMEN_AGRO_SQL=1`, on en prod).

Tabla: `mercado.volumen_mercado_agro` (PK periodo+commodity). `commodity` en MAYÚSCULA
(SOJA | TRIGO | MAIZ — así joinea el reader). `toneladas` materializada (lo único que
consume el share) + `data` jsonb passthrough.

Uso:
    python -m scripts.cargar_volumen_agro --periodo 2026-06 --commodity SOJA --toneladas 1234567
"""
from __future__ import annotations

import argparse
import json

from core.postgres import get_pool

_COMMODITIES = ("SOJA", "TRIGO", "MAIZ")

_UPSERT = (
    "INSERT INTO volumen_mercado_agro (periodo, commodity, toneladas, data) "
    "VALUES (%s, %s, %s, %s::jsonb) "
    "ON CONFLICT (periodo, commodity) DO UPDATE SET "
    "toneladas = EXCLUDED.toneladas, data = EXCLUDED.data"
)


def main() -> int:
    ap = argparse.ArgumentParser(description="Carga manual del volumen del mercado agro en SQL.")
    ap.add_argument("--periodo", required=True, help="período 'YYYY-MM'.")
    ap.add_argument("--commodity", required=True, help="SOJA | TRIGO | MAIZ.")
    ap.add_argument("--toneladas", type=float, required=True, help="toneladas totales del mercado.")
    args = ap.parse_args()

    periodo = args.periodo.strip()
    commodity = args.commodity.strip().upper()
    if commodity not in _COMMODITIES:
        print(f"AVISO: commodity {commodity!r} no está en {_COMMODITIES} — el share sólo "
              f"agrega esos tres. Se carga igual.")

    data = json.dumps({"periodo": periodo, "commodity": commodity, "toneladas": args.toneladas})
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(_UPSERT, (periodo, commodity, args.toneladas, data))
    print(f"Volumen agro cargado: periodo={periodo} commodity={commodity} "
          f"toneladas={args.toneladas}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
