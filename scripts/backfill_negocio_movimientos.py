"""backfill_negocio_movimientos.py — corre jobs/negocio_movimientos para
los últimos N días hábiles estrictamente anteriores a ayer (default 7).

Llama secuencialmente a `jobs.negocio_movimientos.run(fecha_d=...)` día por
día — espera a que termine uno antes de empezar el siguiente. El job ya es
idempotente por (fecha, comprobante), así que re-correrlo es seguro.

Días de fin de semana / feriados sin operación devuelven 0 boletos y siguen
sin error. Si un día falla por excepción, se loguea y se continúa con el
siguiente; al final hay un resumen con éxitos y fallas.

Uso:
    python -m scripts.backfill_negocio_movimientos              # 7 días default
    python -m scripts.backfill_negocio_movimientos --n 14       # más días
    python -m scripts.backfill_negocio_movimientos --desde 2026-04-15 --hasta 2026-04-30
    python -m scripts.backfill_negocio_movimientos --dry        # preview
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import UTC, date, datetime, timedelta

sys.path.insert(0, ".")

from jobs.negocio_movimientos import run as run_dia

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("backfill_negocio")


def _ultimos_n_habiles_antes_de(corte: date, n: int) -> list[date]:
    """N días hábiles (L-V) estrictamente anteriores a `corte`, en orden ascendente."""
    out: list[date] = []
    d = corte - timedelta(days=1)
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d -= timedelta(days=1)
    return sorted(out)


def _rango_habiles(desde: date, hasta: date) -> list[date]:
    """Días hábiles inclusive entre desde y hasta, ascendente."""
    out: list[date] = []
    d = desde
    while d <= hasta:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--n", type=int, default=7,
                        help="Cantidad de días hábiles a backfillear (default 7)")
    parser.add_argument("--desde", help="YYYY-MM-DD (override del rango — requiere --hasta)")
    parser.add_argument("--hasta", help="YYYY-MM-DD (override del rango — requiere --desde)")
    parser.add_argument("--dry", action="store_true",
                        help="No persiste, solo reporta cuántos boletos persistirían")
    args = parser.parse_args()

    if args.desde or args.hasta:
        if not (args.desde and args.hasta):
            print("--desde y --hasta deben ir juntos.", file=sys.stderr)
            return 1
        d_desde = date.fromisoformat(args.desde)
        d_hasta = date.fromisoformat(args.hasta)
        if d_desde > d_hasta:
            print("--desde debe ser <= --hasta.", file=sys.stderr)
            return 1
        fechas = _rango_habiles(d_desde, d_hasta)
    else:
        hoy_art = (datetime.now(UTC) - timedelta(hours=3)).date()
        ayer_art = hoy_art - timedelta(days=1)
        fechas = _ultimos_n_habiles_antes_de(ayer_art, args.n)

    if not fechas:
        logger.warning("Nada para procesar.")
        return 0

    logger.info("Backfill negocio_movimientos · %d días hábiles: %s → %s",
                len(fechas), fechas[0].isoformat(), fechas[-1].isoformat())
    for f in fechas:
        logger.info("  · %s (%s)", f.isoformat(), f.strftime("%a"))

    resumen: list[dict] = []
    for f in fechas:
        logger.info("───────────────────────────────────────────")
        logger.info("Procesando %s ...", f.isoformat())
        try:
            res = run_dia(fecha_d=f, dry=args.dry)
            resumen.append({"fecha": f.isoformat(), "ok": True, **res})
        except Exception as e:
            logger.exception("Falló %s", f.isoformat())
            resumen.append({"fecha": f.isoformat(), "ok": False, "error": str(e)})

    logger.info("═══════════════════════════════════════════")
    logger.info("Resumen del backfill (%d días):", len(resumen))
    total_upsert = total_match = total_boletos = 0
    fallas = 0
    for r in resumen:
        if r["ok"]:
            up = r.get("upsertados", 0)
            ma = r.get("matched", 0)
            bo = r.get("boletos", 0)
            total_upsert += up
            total_match += ma
            total_boletos += bo
            logger.info("  %s · boletos=%d upsert=%d match=%d",
                        r["fecha"], bo, up, ma)
        else:
            fallas += 1
            logger.error("  %s · ERROR: %s", r["fecha"], r["error"])
    logger.info("Totales: %d boletos · %d upserted + %d matched · %d días fallidos",
                total_boletos, total_upsert, total_match, fallas)
    if args.dry:
        logger.info("(--dry: nada escrito a Mongo)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
