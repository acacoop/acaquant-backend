"""archive_options_data.py — archivar y limpiar Opciones.Data.

La colección Opciones.Data acumula 1 doc por cada trade de cada strike de
cada rueda. Crece sin freno y es la colección más pesada del cluster.

El valor de estos datos decae después del vencimiento: una vez que el OPEX
pasa, los strikes de esa serie ya no se operan. Los históricos pueden vivir
fuera de Mongo sin pérdida de utilidad.

Uso esperado: correr manualmente después de cada OPEX para archivar la
ventana que cerró y liberar espacio en Atlas.

Output:
    /root/TradingAV/archive/options_data/YYYY-MM.parquet  (particionado
    por año-mes del `timestamp`, re-ejecutable con deduplicación por _id)

Flujo:
    1. Query `{timestamp: {$lt: cutoff}}`.
    2. Stream a pandas, particiona por año-mes, escribe/mergea Parquet.
    3. Verifica count exportado == count a borrar.
    4. delete_many de Mongo (SOLO con --apply; default --dry-run).

Comandos:
    python -m jobs.archive_options_data                       # dry-run default
    python -m jobs.archive_options_data --apply               # corta hoy 00:00 ART
    python -m jobs.archive_options_data --cutoff 2026-04-17 --apply
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from core.mongo import get_mongo_client

logger = logging.getLogger("archive_options_data")

AR_TZ = ZoneInfo("America/Argentina/Buenos_Aires")
ARCHIVE_ROOT = Path(__file__).resolve().parent.parent / "archive" / "options_data"


def _parse_cutoff(s: str | None) -> datetime:
    if s:
        dt = datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=AR_TZ)
    else:
        dt = datetime.now(AR_TZ).replace(hour=0, minute=0, second=0, microsecond=0)
    return dt.astimezone(timezone.utc)


def _archivar_particion(df: pd.DataFrame, path: Path) -> None:
    """Escribe Parquet mergeando con lo existente (dedupe por _id)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        viejo = pd.read_parquet(path)
        df = pd.concat([viejo, df], ignore_index=True)
        df = df.drop_duplicates(subset=["_id"], keep="last")
    df = df.sort_values("timestamp")
    df.to_parquet(path, compression="zstd", index=False)


def run(cutoff: datetime, apply: bool) -> int:
    client = get_mongo_client()
    col = client["Opciones"]["Data"]

    filtro = {"timestamp": {"$lt": cutoff}}
    total = col.count_documents(filtro)
    cutoff_ar = cutoff.astimezone(AR_TZ).strftime("%Y-%m-%d %H:%M %Z")
    logger.info("Cutoff: timestamp < %s (%s UTC)", cutoff_ar, cutoff.isoformat())
    logger.info("Docs a archivar: %d", total)

    if total == 0:
        logger.info("Nada para archivar. Salgo.")
        return 0

    # Stream en batches para no volar memoria si hay millones de docs
    BATCH = 50_000
    por_particion: dict[str, list[dict]] = {}
    leidos = 0
    cursor = col.find(filtro).batch_size(BATCH)
    for doc in cursor:
        ts = doc.get("timestamp")
        if not isinstance(ts, datetime):
            continue
        key = f"{ts.year:04d}-{ts.month:02d}"
        por_particion.setdefault(key, []).append({**doc, "_id": str(doc["_id"])})
        leidos += 1
        if leidos % BATCH == 0:
            logger.info("  ... leídos %d / %d", leidos, total)

    if leidos != total:
        logger.error("Contador inconsistente: total=%d leidos=%d. Abortando sin borrar.",
                     total, leidos)
        return 1

    logger.info("Particiones a escribir: %s", sorted(por_particion.keys()))
    if not apply:
        sizes = {k: len(v) for k, v in por_particion.items()}
        logger.info("[DRY-RUN] NO se escribe Parquet ni se borra Mongo.")
        logger.info("[DRY-RUN] Distribución por partición: %s", sizes)
        logger.info("[DRY-RUN] Pasá --apply para ejecutar.")
        return 0

    total_escritos = 0
    for key in sorted(por_particion.keys()):
        df = pd.DataFrame(por_particion[key])
        path = ARCHIVE_ROOT / f"{key}.parquet"
        _archivar_particion(df, path)
        total_escritos += len(df)
        logger.info("  ✅ %s → %d docs", path.relative_to(ARCHIVE_ROOT.parent.parent), len(df))

    logger.info("Total escritos a Parquet: %d", total_escritos)

    if total_escritos < total:
        logger.error("Se escribieron %d < %d. Abortando delete por seguridad.",
                     total_escritos, total)
        return 2

    logger.info("Borrando de Mongo...")
    resultado = col.delete_many(filtro)
    logger.info("🗑️  Borrados: %d docs", resultado.deleted_count)

    if resultado.deleted_count != total:
        logger.warning("Borrados %d ≠ total leído %d (es posible si llegaron docs"
                       " nuevos entre scan y delete).", resultado.deleted_count, total)

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--cutoff",
        help="Fecha YYYY-MM-DD (ART). Default: hoy 00:00 ART. Se borra/archiva timestamp < cutoff.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Ejecutar de verdad. Sin este flag es dry-run.",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    try:
        cutoff = _parse_cutoff(args.cutoff)
    except ValueError as e:
        logger.error("Formato de --cutoff inválido: %s", e)
        return 2

    return run(cutoff, apply=args.apply)


if __name__ == "__main__":
    sys.exit(main())
