"""backfill_negocio_range.py — corre `jobs.negocio_movimientos` en
secuencia para un rango de fechas, sólo días hábiles (L-V).

Idempotente — el job persiste con upsert por `(fecha, comprobante)`.
Re-correr el mismo rango no duplica.

Por defecto pausa 1s entre días para no martillar a Aunesa.

Uso:
    # Primer semestre 2024
    python -m scripts.backfill_negocio_range --desde 2024-01-01 --hasta 2024-06-30

    # Background (sobrevive al SSH):
    nohup python -m scripts.backfill_negocio_range \\
        --desde 2024-01-01 --hasta 2024-06-30 \\
        > /var/log/backfill_2024_h1.log 2>&1 &

    # Cortar antes de tiempo: --max-dias N (útil para testear).
    python -m scripts.backfill_negocio_range --desde 2024-01-01 --hasta 2024-12-31 --max-dias 3

    # Solo reportar el plan, no escribir:
    python -m scripts.backfill_negocio_range --desde 2024-01-01 --hasta 2024-06-30 --dry
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import datetime, timedelta

from jobs.negocio_movimientos import run as run_dia


def _parse_fecha(s: str) -> datetime.date:
    return datetime.strptime(s, "%Y-%m-%d").date()


def _es_habil(d) -> bool:
    """Lunes=0 ... Domingo=6. Mon-Fri = días hábiles. No filtra
    feriados — Aunesa devuelve [] esos días y el job los maneja."""
    return d.weekday() < 5


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--desde", required=True, help="YYYY-MM-DD inclusive")
    parser.add_argument("--hasta", required=True, help="YYYY-MM-DD inclusive")
    parser.add_argument("--sleep", type=float, default=1.0,
                        help="Segundos entre días (default 1.0).")
    parser.add_argument("--max-dias", type=int, default=None,
                        help="Limitar a N días hábiles desde --desde "
                             "(testing/abort temprano).")
    parser.add_argument("--dry", action="store_true",
                        help="Modo dry — no escribe a Mongo, sólo cuenta.")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    logger = logging.getLogger("backfill_range")

    desde = _parse_fecha(args.desde)
    hasta = _parse_fecha(args.hasta)
    if hasta < desde:
        logger.error("--hasta < --desde — nada que hacer.")
        return 1

    # Lista de hábiles en el rango.
    dias: list = []
    cur = desde
    while cur <= hasta:
        if _es_habil(cur):
            dias.append(cur)
        cur += timedelta(days=1)
    if args.max_dias is not None:
        dias = dias[: args.max_dias]

    n_total = len(dias)
    logger.info(
        "Plan: %d días hábiles entre %s y %s. Sleep entre días: %.1fs. "
        "ETA aprox. %d min (asumiendo ~30s/día).",
        n_total, desde.isoformat(), hasta.isoformat(),
        args.sleep,
        round(n_total * (30 + args.sleep) / 60),
    )

    if args.dry:
        for d in dias[:5]:
            logger.info("[DRY] correría: %s", d.isoformat())
        if n_total > 5:
            logger.info("[DRY] ... +%d más", n_total - 5)
        return 0

    n_ok = 0
    n_err = 0
    n_boletos = 0
    n_upsert = 0
    n_match = 0
    t_start = time.time()

    for i, d in enumerate(dias, start=1):
        try:
            res = run_dia(fecha_d=d, dry=False)
            n_ok += 1
            n_boletos += int(res.get("boletos") or 0)
            n_upsert += int(res.get("upsertados") or 0)
            n_match += int(res.get("matched") or 0)
        except Exception as e:
            n_err += 1
            logger.exception("ERROR en %s: %s", d.isoformat(), e)

        # Progreso + ETA.
        elapsed = time.time() - t_start
        avg = elapsed / i
        rest = n_total - i
        eta_min = round(rest * avg / 60)
        logger.info(
            "[%d/%d] %s → ok=%d err=%d · boletos_acum=%d (upsert=%d match=%d) · "
            "ETA %d min",
            i, n_total, d.isoformat(),
            n_ok, n_err, n_boletos, n_upsert, n_match, eta_min,
        )

        if i < n_total and args.sleep > 0:
            time.sleep(args.sleep)

    elapsed_min = round((time.time() - t_start) / 60, 1)
    logger.info(
        "DONE. %d días procesados · ok=%d err=%d · boletos=%d "
        "(upsert=%d match=%d) · tiempo %.1f min.",
        n_total, n_ok, n_err, n_boletos, n_upsert, n_match, elapsed_min,
    )
    return 0 if n_err == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
