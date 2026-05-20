"""Backfill rápido de cuentas autorizadas — diagnóstico inline.

Reutiliza la lógica de jobs.descubrir_cuentas pero con:
  - Rango por default 0..2500 (en vez de 1..12000).
  - sleep 0.05s entre llamadas (~2 min total para 2500 cuentas).
  - Log INMEDIATO por cada cuenta autorizada (no solo cada batch).
  - Batch persist cada 50 cuentas (vs 200 del cron diario).

El job de cron (jobs.descubrir_cuentas) queda intacto: corre 11:30 UTC
L-V con sleep más conservador. Este script es para mirar a ojo qué
cuentas devuelve el broker en tiempo real y descartar problemas de
config rápidamente.

Uso (en el Droplet):
    cd /root/TradingAV && /root/TradingAV/venv/bin/python -m scripts.backfill_cuentas_rapido
    # con rango distinto:
    cd /root/TradingAV && /root/TradingAV/venv/bin/python -m scripts.backfill_cuentas_rapido --desde 100 --hasta 5000
    # más agresivo (si el broker aguanta):
    cd /root/TradingAV && /root/TradingAV/venv/bin/python -m scripts.backfill_cuentas_rapido --sleep 0.02
"""
from __future__ import annotations

import argparse
import logging
import sys
import time

from jobs.descubrir_cuentas import (
    _ensure_indexes,
    _login,
    _persist,
    _probe_account,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger("backfill_cuentas_rapido")


def _fmt_money(n: float | None) -> str:
    return f"{n:>15,.2f}" if n is not None else "             — "


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--desde", type=int, default=0)
    parser.add_argument("--hasta", type=int, default=2500)
    parser.add_argument("--sleep", type=float, default=0.05)
    parser.add_argument("--batch", type=int, default=50)
    args = parser.parse_args()

    if args.hasta < args.desde:
        parser.error("--hasta debe ser >= --desde")

    _ensure_indexes()
    _login()

    total = args.hasta - args.desde + 1
    logger.info(
        "Rango %d..%d (%d cuentas, sleep=%ss, batch=%d) — log por hit",
        args.desde, args.hasta, total, args.sleep, args.batch,
    )

    n_autorizadas = 0
    n_activas = 0
    pendientes: list[tuple[str, dict]] = []
    t0 = time.monotonic()

    try:
        for acc_int in range(args.desde, args.hasta + 1):
            acc = str(acc_int)
            snap = _probe_account(acc)
            if snap is not None:
                n_autorizadas += 1
                if snap["activa"]:
                    n_activas += 1
                pendientes.append((acc, snap))
                marca = "★" if snap["activa"] else " "
                logger.info(
                    "%s acc=%-6s  ARS=%s  USD=%s  pos=%s",
                    marca, acc,
                    _fmt_money(snap.get("ars_disponible")),
                    _fmt_money(snap.get("usd_d_disponible")),
                    snap.get("n_posiciones"),
                )

            if len(pendientes) >= args.batch:
                _persist(pendientes)
                pendientes.clear()

            # Heartbeat cada 250 cuentas para ver que el loop sigue vivo
            # incluso si no hay hits.
            if (acc_int - args.desde + 1) % 250 == 0:
                elapsed = time.monotonic() - t0
                hechos = acc_int - args.desde + 1
                logger.info(
                    "  …%d/%d (%.0f%%) — autorizadas=%d, activas=%d — %.0fs",
                    hechos, total, 100 * hechos / total,
                    n_autorizadas, n_activas, elapsed,
                )

            time.sleep(args.sleep)

        if pendientes:
            _persist(pendientes)

    except KeyboardInterrupt:
        logger.warning("Interrumpido. Persistiendo lo pendiente…")
        if pendientes:
            _persist(pendientes)
        return 130

    elapsed = time.monotonic() - t0
    logger.info(
        "=" * 60,
    )
    logger.info(
        "LISTO. Rango %d..%d en %.0fs. Autorizadas=%d (activas=%d).",
        args.desde, args.hasta, elapsed, n_autorizadas, n_activas,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
