"""seed_dolar_linked.py — seed de bonos dolar-linked en Trading.Curvas.

Idempotente: upsert por `ticker` completo. Volver a correr no duplica.

Doc shape: ver docs/curvas/dolar_linked.md.

Uso (desde /root/TradingAV):
    python -m scripts.seed_dolar_linked --dry      # solo cuenta + diff
    python -m scripts.seed_dolar_linked            # aplica upserts

Una vez seedeados los bonos:
    1. El motor engines/curvas.py los va a ver en su próximo loop pero
       NO los va a enriquecer hasta que se agregue la rama dolar_linked
       (tarea separada — sin esa rama, los bonos aparecen en
       /api/analitica/listar-curva?curva=dolar_linked con precio + vto
       pero sin TEA / duration / paridad).
    2. Sumar el doc en Valuaciones.Assets (con TICKER == ticker_corto)
       para que aparezca en AuM/Portfolios.
"""
from __future__ import annotations

import argparse
import logging

from core.mongo import get_mongo_client

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
logger = logging.getLogger("seed_dolar_linked")


# ─────────────────────────────────────────────────────────────────────
# Bonos a seedear
# ─────────────────────────────────────────────────────────────────────
# Cada doc respeta el shape canónico definido en docs/curvas/dolar_linked.md.

BONOS: list[dict] = [
    {
        "ticker": "MERV - XMEV - TZV26 - 24hs",
        "ticker_corto": "TZV26",
        "tipo": "dolar_linked",
        "curva": "dolar_linked",
        "tasa_referencia": "A3500",
        "tc_emision": 838.95,
        "fecha_emision": "2024-02-28",
        "fecha_vencimiento": "2026-06-30",
        "valor_nominal": 100,
        "cupon_anual": 0,
        "flujos": [
            {"fecha": "2026-06-30", "amortizacion_pct": 100},
        ],
    },
    {
        "ticker": "MERV - XMEV - D30S6 - 24hs",
        "ticker_corto": "D30S6",
        "tipo": "dolar_linked",
        "curva": "dolar_linked",
        "tasa_referencia": "A3500",
        "tc_emision": 1396.4528,
        "fecha_emision": "2026-03-16",
        "fecha_vencimiento": "2026-09-30",
        "valor_nominal": 100,
        "cupon_anual": 0,
        "flujos": [
            {"fecha": "2026-09-30", "amortizacion_pct": 100},
        ],
    },
    {
        "ticker": "MERV - XMEV - TZV27 - 24hs",
        "ticker_corto": "TZV27",
        "tipo": "dolar_linked",
        "curva": "dolar_linked",
        "tasa_referencia": "A3500",
        "tc_emision": 1377.0942,
        "fecha_emision": "2026-02-27",
        "fecha_vencimiento": "2027-06-30",
        "valor_nominal": 100,
        "cupon_anual": 0,
        "flujos": [
            {"fecha": "2027-06-30", "amortizacion_pct": 100},
        ],
    },
    {
        "ticker": "MERV - XMEV - TZV28 - 24hs",
        "ticker_corto": "TZV28",
        "tipo": "dolar_linked",
        "curva": "dolar_linked",
        "tasa_referencia": "A3500",
        "tc_emision": 1370.2909,
        "fecha_emision": "2026-03-31",
        "fecha_vencimiento": "2028-06-30",
        "valor_nominal": 100,
        "cupon_anual": 0,
        "flujos": [
            {"fecha": "2028-06-30", "amortizacion_pct": 100},
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
            # Diff de campos relevantes (ignoramos _id y otros internos).
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
