"""negocio_movimientos.py — pega a Aunesa, consolida y persiste boletos
del día en CashFlow.NegocioMovimientos.

Idempotente por `(fecha, comprobante)`: si ya existe el boleto, se
actualiza con $set; si es nuevo, upsert. No duplica.

Cron: una corrida por hora de 12 ART a 22 ART (15-22 UTC) L-V.
Ver deploy/crontab.txt.

Uso:
    python -m jobs.negocio_movimientos              # día de hoy ART
    python -m jobs.negocio_movimientos --fecha 2026-05-04
    python -m jobs.negocio_movimientos --fecha 2026-05-04 --dry

Schema doc en CashFlow.NegocioMovimientos:
{
  fecha          : "2026-05-04",            # ISO YYYY-MM-DD ART
  comprobante    : "BOL 2026069919",        # ID único del boleto en Aunesa
  cuenta         : "[805] MOLLO ...",
  id_cuenta      : "805",                    # derivado de cuenta (índice; vista COMERCIAL)
  categoria      : "compra",                 # 16 categorías posibles
  op             : "Compra",                 # Compra/Venta/Susc FCI/...
  ticker         : "AL30",                   # short ticker o null
  cantidad       : -1.00,                    # cuotapartes / VN, signo cliente
  precio         : 91410.00,                 # precio o tasa% (cauciones)
  importe        : 91410.00,                 # plata movida, signo cliente
  moneda         : "ARS",                    # ARS / USD / USDC / etc
  plazo          : "Inm",                    # CI / 24hs / Contado Inmediato / N días
  lugar          : "Local",                  # Local / CV / A3 / etc
  estado         : "DIS",                    # DIS / DIF / etc
  informacion    : "Compra [AL30] 1,00@...",
  n_lineas       : 2,                        # cantidad de líneas raw que conforman
  ingestado_en   : ISODate("2026-05-04T15:30:00Z")
}

Índice único: (fecha, comprobante).
"""
from __future__ import annotations

import argparse
import logging
import re
import sys
from datetime import UTC, date, datetime, timedelta

from pymongo import UpdateOne

sys.path.insert(0, ".")

from api.services import aunesa_negocio as svc
from api.services._mep import get_mep_for_date
from core.mongo import get_mongo_client

DB_NAME = "CashFlow"
COL_NAME = "NegocioMovimientos"

# `cuenta` viene "[805] NOMBRE" → id de la cuenta comitente. Denormalizado en
# el doc para que las queries por cuenta usen índice (en vez de regex). Lo
# consume la vista COMERCIAL (api/services/comercial.py).
_RE_ID_CUENTA = re.compile(r"^\[(\d+)\]")


def _extract_id_cuenta(cuenta: str | None) -> str | None:
    m = _RE_ID_CUENTA.match(cuenta or "")
    return m.group(1) if m else None


def _ensure_indexes(coll) -> None:
    """Índice único (fecha, comprobante). Idempotente."""
    coll.create_index(
        [("fecha", 1), ("comprobante", 1)],
        unique=True,
        sparse=False,
        name="uq_fecha_comprobante",
    )
    # Útiles para queries del front:
    coll.create_index([("fecha", -1), ("categoria", 1)], name="fecha_categoria")
    coll.create_index([("fecha", -1), ("cuenta", 1)], name="fecha_cuenta")
    coll.create_index([("fecha", -1), ("ticker", 1)], name="fecha_ticker")
    # Vista COMERCIAL: queries por cuenta vía id_cuenta (sin regex).
    coll.create_index([("id_cuenta", 1), ("fecha", -1)], name="idcuenta_fecha")
    coll.create_index(
        [("id_cuenta", 1), ("categoria", 1), ("fecha", -1)],
        name="idcuenta_categoria_fecha",
    )


def _boleto_a_doc(b: dict, fecha_iso: str, ahora: datetime, mep: float | None) -> dict:
    """Convierte un boleto consolidado del service al doc de Mongo.
    Descarta `lineas` raw (audit puede agregarse después si hace falta).

    `mep` es el MEP del día (puede ser None si no hay cotización para esa
    fecha). Se guarda en cada boleto como snapshot inmutable — sirve para
    pesificar/dolarizar después sin volver a `Valuaciones.Dolar`.
    """
    return {
        "fecha":        fecha_iso,
        "comprobante":  b.get("comprobante"),
        "cuenta":       b.get("cuenta"),
        "id_cuenta":    _extract_id_cuenta(b.get("cuenta")),
        "categoria":    b.get("categoria"),
        "op":           b.get("op"),
        "ticker":       b.get("ticker"),
        "cantidad":     b.get("cantidad"),
        "precio":       b.get("precio"),
        "importe":      b.get("importe"),
        "moneda":       b.get("moneda"),
        "plazo":        b.get("plazo"),
        "lugar":        b.get("lugar"),
        "estado":       b.get("estado"),
        "informacion":  b.get("informacion"),
        "n_lineas":     b.get("n_lineas"),
        "mep":          mep,
        "ingestado_en": ahora,
    }


def run(fecha_d: date, dry: bool = False) -> dict:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    logger = logging.getLogger("negocio_movimientos")

    fecha_iso = fecha_d.isoformat()
    logger.info("Iniciando ingesta NegocioMovimientos para %s ART", fecha_iso)

    # Pegar a Aunesa.
    consolidado = svc.fetch_y_consolidar(fecha=fecha_d)
    boletos = consolidado["boletos"]
    logger.info("Aunesa devolvió %d movimientos → %d boletos consolidados",
                consolidado["meta"]["total"], len(boletos))

    if not boletos:
        logger.info("Sin boletos para persistir.")
        return {"upsertados": 0, "matched": 0, "boletos": 0, "fecha": fecha_iso}

    # Filtrar boletos sin comprobante (no se pueden upsertar de manera estable).
    persistibles = [b for b in boletos if b.get("comprobante")]
    skipped = len(boletos) - len(persistibles)
    if skipped:
        logger.warning("Skipeados %d boletos sin comprobante.", skipped)

    if dry:
        logger.info("[DRY] No persiste. %d boletos persistirían.", len(persistibles))
        return {
            "upsertados": 0, "matched": 0,
            "boletos": len(persistibles), "skipped": skipped,
            "fecha": fecha_iso, "dry": True,
        }

    client = get_mongo_client()
    coll = client[DB_NAME][COL_NAME]
    _ensure_indexes(coll)

    # MEP del día — una lookup, se reusa para todos los boletos de la fecha.
    mep = get_mep_for_date(fecha_iso)
    if mep is None:
        logger.warning("Sin MEP para %s — boletos quedarán con mep=null.", fecha_iso)

    ahora = datetime.now(UTC)
    ops = []
    for b in persistibles:
        doc = _boleto_a_doc(b, fecha_iso, ahora, mep)
        ops.append(UpdateOne(
            {"fecha": fecha_iso, "comprobante": b["comprobante"]},
            {"$set": doc},
            upsert=True,
        ))

    result = coll.bulk_write(ops, ordered=False)
    logger.info(
        "Bulk write OK: matched=%d modified=%d upserted=%d (de %d boletos)",
        result.matched_count, result.modified_count,
        result.upserted_count, len(persistibles),
    )

    return {
        "fecha":      fecha_iso,
        "boletos":    len(persistibles),
        "skipped":    skipped,
        "upsertados": result.upserted_count,
        "matched":    result.matched_count,
        "modified":   result.modified_count,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--fecha", help="YYYY-MM-DD; default: hoy ART")
    parser.add_argument("--dry", action="store_true",
                        help="No escribe a Mongo, solo reporta cuántos persistirían")
    args = parser.parse_args()

    if args.fecha:
        try:
            d = datetime.strptime(args.fecha, "%Y-%m-%d").date()
        except ValueError:
            print(f"--fecha mal formada: {args.fecha}")
            return 1
    else:
        d = (datetime.now(UTC) - timedelta(hours=3)).date()

    from core.job_runs import JobRunLogger
    with JobRunLogger("negocio_movimientos") as jr:
        res = run(fecha_d=d, dry=args.dry)
        if isinstance(res, dict):
            for k, v in res.items():
                jr.set_stat(k, v)
    print(f"\n→ {res}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
