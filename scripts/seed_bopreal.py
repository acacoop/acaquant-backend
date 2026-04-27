"""seed_bopreal.py — seed de Bopreales en Trading.Curvas (curva soberanos).

Los Bopreales son títulos del BCRA en USD pago en USD, cuponados, con
calendario propio. Encajan en la curva `soberanos` (mismo shape de
flujos que globales/bonares: porcentual sobre residual). Distinguidos
por `tipo: "bopreal"`.

⚠️ FLUJOS CALCULADOS TEÓRICAMENTE — VERIFICAR CONTRA PROSPECTO BCRA.

Las fechas y montos de cupones se calculan asumiendo:
  - Cupón semestral fijo (5% anual nominal → 2.5% por cupón completo).
  - Convención 180/360 US: cada cupón completo paga sobre 180 días.
  - Cupón final parcial = 5% × días_restantes / 360 cuando el vto no
    cae en aniversario semestral.
  - Bullet al vto (amortización 100% en el último flujo, sin
    amortizaciones intermedias).
  - Calendario semestral cae el día del mes de la emisión.
  - Sin manejo de feriados — fechas teóricas pueden diferir del CSV
    oficial del BCBA por 1-2 días hábiles.

Cuando consigas el CSV oficial del BCBA / prospecto BCRA, reemplazá
los flujos manualmente y dejá un comentario diciendo "verificado vs
CSV oficial DD/MM".

Uso (desde /root/TradingAV):
    python -m scripts.seed_bopreal --dry      # diff
    python -m scripts.seed_bopreal            # aplica

Doc shape: ver el patrón de soberanos en `Trading.Curvas` (AL30D,
AO28D, etc.) — mismo shape pero con `tipo: "bopreal"` en lugar de
"globales" o "bonares".
"""
from __future__ import annotations

import argparse
import logging

from core.mongo import get_mongo_client

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
logger = logging.getLogger("seed_bopreal")


# ─────────────────────────────────────────────────────────────────────
# Bonos a seedear
# ─────────────────────────────────────────────────────────────────────
# BPOC7 — Bopreal Serie 4 Letra C (BCRA, USD/USD, vto 2027-10-31).
# Cupón semestral 5% nominal anual. Calendario teórico desde emisión:
#   2024-07-05, 2025-01-05, 2025-07-05, 2026-01-05, 2026-07-05,
#   2027-01-05, 2027-07-05  →  cada uno: cupon_sobre_residual=2.5
#   2027-10-31 (vto, parcial 118 días) →  cupon=1.6389, amort=100
# Total: 8 flujos.

BONOS: list[dict] = [
    {
        "ticker": "MERV - XMEV - BPOC7 - 24hs",
        "ticker_corto": "BPOC7",
        "tipo": "bopreal",
        "curva": "soberanos",
        "cer_emision": None,
        "cupon_anual": 0,
        "fecha_emision": "2024-01-05",
        "fecha_vencimiento": "2027-10-31",
        "valor_nominal": 100,
        "flujos": [
            {"fecha": "2024-07-05", "amortizacion_pct": 0,   "cupon_sobre_residual": 2.5,    "residual_previo_pct": 100},
            {"fecha": "2025-01-05", "amortizacion_pct": 0,   "cupon_sobre_residual": 2.5,    "residual_previo_pct": 100},
            {"fecha": "2025-07-05", "amortizacion_pct": 0,   "cupon_sobre_residual": 2.5,    "residual_previo_pct": 100},
            {"fecha": "2026-01-05", "amortizacion_pct": 0,   "cupon_sobre_residual": 2.5,    "residual_previo_pct": 100},
            {"fecha": "2026-07-05", "amortizacion_pct": 0,   "cupon_sobre_residual": 2.5,    "residual_previo_pct": 100},
            {"fecha": "2027-01-05", "amortizacion_pct": 0,   "cupon_sobre_residual": 2.5,    "residual_previo_pct": 100},
            {"fecha": "2027-07-05", "amortizacion_pct": 0,   "cupon_sobre_residual": 2.5,    "residual_previo_pct": 100},
            {"fecha": "2027-10-31", "amortizacion_pct": 100, "cupon_sobre_residual": 1.6389, "residual_previo_pct": 100},
        ],
    },
]


def run(dry: bool) -> None:
    client = get_mongo_client()
    col = client["Trading"]["Curvas"]

    n_insert = 0
    n_update = 0
    n_noop = 0

    for doc in BONOS:
        ticker = doc["ticker"]
        existing = col.find_one({"ticker": ticker}, {"_id": 0})

        if existing is None:
            logger.info("INSERT %s", ticker)
            n_insert += 1
        else:
            diff_keys = sorted(
                k for k in doc
                if existing.get(k) != doc[k]
            )
            if diff_keys:
                logger.info(
                    "UPDATE %s — campos a sobrescribir: %s",
                    ticker, ", ".join(diff_keys),
                )
                n_update += 1
            else:
                logger.info("NOOP   %s — ya está al día", ticker)
                n_noop += 1

        if not dry:
            col.update_one({"ticker": ticker}, {"$set": doc}, upsert=True)

    logger.info(
        "Resumen: insert=%d update=%d noop=%d (total=%d) — %s",
        n_insert, n_update, n_noop, len(BONOS),
        "DRY (sin escribir)" if dry else "aplicado",
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--dry", action="store_true",
        help="No escribe; muestra qué upsert haría",
    )
    args = parser.parse_args()
    run(dry=args.dry)


if __name__ == "__main__":
    main()
