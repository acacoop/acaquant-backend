"""archive_options_data.py — backup + purga de Opciones.Data.

1) Exporta el 100% de Opciones.Data a un JSON local (streaming, sin volar
   memoria aunque haya millones de docs).
2) Borra de Mongo los docs con timestamp < hoy 00:00 ART — la colección
   queda solo con trades de la rueda en curso.

Uso esperado: correrlo manualmente en el Droplet cada vez que pase un
OPEX o cuando se quiera liberar espacio.

Comandos:
    python -m jobs.archive_options_data            # dry-run
    python -m jobs.archive_options_data --apply    # ejecuta
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from core.mongo import get_mongo_client

logger = logging.getLogger("archive_options_data")

AR_TZ = ZoneInfo("America/Argentina/Buenos_Aires")
ARCHIVE_ROOT = Path(__file__).resolve().parent.parent / "archive"


def _serialize(doc: dict) -> dict:
    out = {}
    for k, v in doc.items():
        if k == "_id":
            out[k] = str(v)
        elif isinstance(v, datetime):
            out[k] = v.isoformat()
        else:
            out[k] = v
    return out


def run(apply: bool) -> int:
    client = get_mongo_client()
    col = client["Opciones"]["Data"]

    total = col.count_documents({})  # perf-ok: PERF003,PERF004 — conteo EXACTO: es el guard de seguridad pre-delete
    hoy_ar  = datetime.now(AR_TZ).replace(hour=0, minute=0, second=0, microsecond=0)
    hoy_utc = hoy_ar.astimezone(UTC)
    a_borrar = col.count_documents({"timestamp": {"$lt": hoy_utc}})

    ts_corrida = datetime.now(AR_TZ).strftime("%Y%m%d_%H%M%S")
    path = ARCHIVE_ROOT / f"options_data_{ts_corrida}.json"

    logger.info("Total docs en Opciones.Data : %d", total)
    logger.info("Docs con timestamp < %s : %d", hoy_ar.strftime("%Y-%m-%d %H:%M %Z"), a_borrar)
    logger.info("Archivo destino            : %s", path)

    if total == 0:
        logger.info("Colección vacía. Salgo.")
        return 0

    if not apply:
        logger.info("[DRY-RUN] NO escribe archivo ni borra Mongo. Pasá --apply para ejecutar.")
        return 0

    ARCHIVE_ROOT.mkdir(parents=True, exist_ok=True)

    logger.info("Exportando 100%% de la colección...")
    exportados = 0
    with path.open("w") as f:
        f.write("[\n")
        first = True
        for doc in col.find({}).batch_size(10_000):
            if not first:
                f.write(",\n")
            f.write(json.dumps(_serialize(doc), default=str))
            first = False
            exportados += 1
            if exportados % 50_000 == 0:
                logger.info("  ... %d / %d", exportados, total)
        f.write("\n]\n")

    size_mb = path.stat().st_size / (1024 * 1024)
    logger.info("✅ Exportados %d docs → %.1f MB", exportados, size_mb)

    if exportados < total * 0.99:
        logger.error("Exportados=%d << total=%d. Abortando delete por seguridad.",
                     exportados, total)
        return 1

    logger.info("Borrando docs con timestamp < hoy ART de Mongo...")
    resultado = col.delete_many({"timestamp": {"$lt": hoy_utc}})
    logger.info("🗑️  Borrados: %d docs", resultado.deleted_count)

    restantes = col.count_documents({})  # perf-ok: PERF003 — verificación post-delete (conteo exacto)
    logger.info("Quedan en la colección: %d docs", restantes)

    # Misma purga en el espejo SQL (mercado.options_data): el `ts` es naive ART (== Mongo
    # datetime.now()), así que el corte es contra `hoy_ar` SIN tz. Best-effort: si PG está
    # caído no aborta el job (Mongo ya quedó limpio). Mantiene la tabla SQL acotada a la
    # rueda en curso — es el motivo de existir de este job.
    try:
        from core.postgres import get_pool
        hoy_naive = hoy_ar.replace(tzinfo=None)
        with get_pool().connection() as cn, cn.cursor() as cur:
            cur.execute("DELETE FROM mercado.options_data WHERE ts < %s", (hoy_naive,))
            logger.info("🗑️  SQL options_data: borrados %d ticks (ts < hoy ART)", cur.rowcount or 0)
    except Exception as e:
        logger.error("No se pudo purgar mercado.options_data SQL: %s", e)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true",
                        help="Ejecuta de verdad. Sin este flag es dry-run.")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    return run(apply=args.apply)


if __name__ == "__main__":
    sys.exit(main())
