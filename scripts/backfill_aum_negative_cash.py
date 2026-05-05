"""backfill_aum_negative_cash.py — re-corre jobs/aum.py para snapshots
históricos y reemplaza los docs en Valuaciones.AuM con la nueva lógica
(sin el filtro de cash negativo que ocultaba posiciones short de
ARS/USD).

Por qué: hasta el commit be8bedb (2026-05-05), procesar() en jobs/aum.py
filtraba los rows con `unidad ∈ {ARS, USD}` y `cantidad < 0`, dropeando
posiciones short de cash legítimas (margen / debt). Para cuentas
leverageadas, esto hacía que el saldo total fuera artificialmente más
alto y la valuación / PnL falsos.

Este script vuelve a pegarle a Aunesa con `desde` = T+2 de la
fecha_snapshot histórica, procesa con la lógica actualizada (sin el
filtro), y REEMPLAZA todos los docs de esa fecha en AuM.

Uso:
    python -m scripts.backfill_aum_negative_cash --fecha 2025-09-15
    python -m scripts.backfill_aum_negative_cash --desde 2025-09-15 --hasta 2025-09-30
    python -m scripts.backfill_aum_negative_cash --all
    python -m scripts.backfill_aum_negative_cash --all --dry      # preview

⚠ Latencia: cada fecha hace ~50 calls a Aunesa en paralelo (8 workers).
Una fecha tarda ~5-30s. --all sobre 8 meses puede tomar 30-60min. Usar
tmux/screen para correrlo desconectado.

⚠ Idempotente pero destructivo a nivel `fecha_snapshot`: borra TODOS los
docs de esa fecha antes de insertar los nuevos. Si Aunesa devuelve vacío
para una fecha, NO borra (skip silencioso para no zerificar el snapshot).
"""
from __future__ import annotations

import argparse
import logging
import sys
import threading
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta

import holidays
from pymongo import UpdateOne

sys.path.insert(0, ".")

from core.mongo import get_mongo_client
from jobs.aum import _consultar_cuenta, autenticar, obtener_cuentas

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("backfill_aum_neg_cash")

DB_NAME = "Valuaciones"
COL_NAME = "AuM"


def _t2_de(d: date) -> str:
    """T+2 hábil de la fecha dada, formato DD/MM/YYYY (Aunesa lo espera así)."""
    arg_holidays = holidays.Argentina()

    def proximo_habil(x: date) -> date:
        x += timedelta(days=1)
        while x.weekday() >= 5 or x in arg_holidays:
            x += timedelta(days=1)
        return x

    return proximo_habil(proximo_habil(d)).strftime("%d/%m/%Y")


def _backfill_una_fecha(
    fecha: date,
    headers_ref: dict,
    headers_lock: threading.Lock,
    cuentas,
    dry: bool,
) -> int:
    """Backfillea una sola fecha. Devuelve cantidad de docs persistidos
    (o que persistirían en --dry)."""
    fecha_snapshot = fecha.isoformat()
    desde = _t2_de(fecha)
    # Timestamp = fin del día en UTC. Sirve como audit trail del re-run.
    timestamp = datetime.combine(fecha, datetime.min.time().replace(hour=23))

    logger.info("──────────────────────────────────────────")
    logger.info("📅 fecha_snapshot=%s · desde T+2=%s · cuentas=%d",
                fecha_snapshot, desde, len(cuentas))

    all_registros: list[dict] = []
    futures_map = {}
    with ThreadPoolExecutor(max_workers=8) as executor:
        for i, row in cuentas.iterrows():
            cuenta_id = str(row["id"])
            denominacion = row["denominacion"]
            f = executor.submit(
                _consultar_cuenta,
                cuenta_id, denominacion, i + 1, len(cuentas),
                desde, fecha_snapshot, timestamp,
                headers_ref, headers_lock,
            )
            futures_map[f] = cuenta_id

        for f in as_completed(futures_map):
            registros = f.result()
            if registros:
                all_registros.extend(registros)

    logger.info("   ✓ Aunesa devolvió %d registros", len(all_registros))

    if not all_registros:
        logger.warning("   ⚠ Sin datos para %s — NO se reemplaza el snapshot existente",
                       fecha_snapshot)
        return 0

    if dry:
        logger.info("   [DRY] persistirían %d docs (replace por fecha_snapshot)",
                    len(all_registros))
        return len(all_registros)

    client = get_mongo_client()
    col = client[DB_NAME][COL_NAME]

    # Reemplazo limpio: delete-then-insert dentro del scope de fecha_snapshot.
    deleted = col.delete_many({"fecha_snapshot": fecha_snapshot}).deleted_count
    ops = [
        UpdateOne(
            {
                "id_cuenta":      r["id_cuenta"],
                "unidad":         r["unidad"],
                "fecha_snapshot": r["fecha_snapshot"],
            },
            {"$set": r},
            upsert=True,
        )
        for r in all_registros
    ]
    if ops:
        col.bulk_write(ops, ordered=False)
    logger.info("   ✓ Replaced %d → %d docs en %s.%s",
                deleted, len(all_registros), DB_NAME, COL_NAME)
    return len(all_registros)


def _fechas_objetivo(args: argparse.Namespace) -> Iterable[date]:
    """Determina la lista de fechas a procesar según los flags."""
    if args.fecha:
        return [date.fromisoformat(args.fecha)]
    if args.desde or args.hasta:
        if not (args.desde and args.hasta):
            raise SystemExit("--desde y --hasta deben ir juntos")
        d_desde = date.fromisoformat(args.desde)
        d_hasta = date.fromisoformat(args.hasta)
        if d_desde > d_hasta:
            raise SystemExit("--desde debe ser <= --hasta")
        # Rango calendario — incluye fines de semana, pero Aunesa devolverá
        # vacío y el script skipea sin borrar (validación es safe).
        out = []
        d = d_desde
        while d <= d_hasta:
            out.append(d)
            d += timedelta(days=1)
        return out
    if args.all:
        client = get_mongo_client()
        col = client[DB_NAME][COL_NAME]
        unique = sorted(col.distinct("fecha_snapshot"))
        return [date.fromisoformat(f) for f in unique if f]
    raise SystemExit("Especificá --fecha, --desde/--hasta, o --all")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--fecha", help="YYYY-MM-DD (single day)")
    parser.add_argument("--desde", help="YYYY-MM-DD inclusive (con --hasta)")
    parser.add_argument("--hasta", help="YYYY-MM-DD inclusive (con --desde)")
    parser.add_argument("--all", action="store_true",
                        help="Re-procesa TODAS las fechas únicas en AuM")
    parser.add_argument("--dry", action="store_true",
                        help="No escribe a Mongo, solo reporta")
    args = parser.parse_args()

    fechas = list(_fechas_objetivo(args))
    if not fechas:
        logger.warning("Nada para procesar.")
        return 0

    logger.info("🎯 %d fechas a procesar: %s → %s",
                len(fechas), fechas[0].isoformat(), fechas[-1].isoformat())

    logger.info("🔑 Auth Aunesa…")
    headers_ref = autenticar()
    headers_lock = threading.Lock()

    logger.info("📋 Listado de cuentas activas…")
    cuentas = obtener_cuentas(headers_ref)
    logger.info("   %d cuentas activas", len(cuentas))

    total_registros = 0
    fallas = 0
    for f in fechas:
        try:
            n = _backfill_una_fecha(f, headers_ref, headers_lock, cuentas, args.dry)
            total_registros += n
        except Exception as e:
            fallas += 1
            logger.exception("Falló fecha %s: %s", f.isoformat(), e)
            continue

    logger.info("══════════════════════════════════════════")
    logger.info("🏁 %d fechas procesadas · %d registros · %d fallas%s",
                len(fechas), total_registros, fallas,
                "  (DRY: nada escrito)" if args.dry else "")
    return 0 if fallas == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
