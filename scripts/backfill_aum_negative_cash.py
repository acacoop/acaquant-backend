"""backfill_aum_negative_cash.py — re-corre jobs/aum.py para snapshots
históricos y reemplaza los docs en Valuaciones.AuM.

Por qué: hasta el commit be8bedb (2026-05-05), procesar() en jobs/aum.py
filtraba rows con `unidad ∈ {ARS, USD}` y `cantidad < 0`, dropeando
posiciones short de cash legítimas. Para cuentas leverageadas, el saldo
total quedaba inflado y la valuación / PnL falsos.

V2 (2026-05-05):
- PER-CUENTA replace (no destructive bulk delete por fecha): cuentas
  que timeoutean conservan sus rows existentes — no más data loss.
- Retry-on-timeout: 3 intentos con backoff exponencial antes de fallar.
- --from-csv: re-procesa solo los pares (fecha, id_cuenta) listados,
  output del audit script. Quirúrgico y rápido.
- Persistent audit log: cada intento se escribe a
  Valuaciones.AumBackfillRuns con status / attempt / error / ts. Survive
  tmux closes — toda la historia queda en Mongo.

Uso:
    python -m scripts.backfill_aum_negative_cash --fecha 2025-09-15
    python -m scripts.backfill_aum_negative_cash --desde 2025-09-15 --hasta 2025-09-30
    python -m scripts.backfill_aum_negative_cash --all
    python -m scripts.backfill_aum_negative_cash --from-csv /tmp/aum-corruption.csv
    python -m scripts.backfill_aum_negative_cash --all --dry      # preview
"""
from __future__ import annotations

import argparse
import csv
import logging
import sys
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import holidays
import requests
from pymongo import UpdateOne

sys.path.insert(0, ".")

from core.mongo import get_mongo_client
from jobs.aum import (
    autenticar,
    consultar_posicion,
    obtener_cuentas,
    procesar,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("backfill_aum_neg_cash")

DB_NAME = "Valuaciones"
COL_AUM = "AuM"
COL_RUNS = "AumBackfillRuns"

# Retry parameters para timeouts.
MAX_RETRIES = 3
BACKOFF_BASE_S = 2  # 1° retry: 2s, 2°: 4s, 3°: 8s


def _t2_de(d: date) -> str:
    """T+2 hábil (DD/MM/YYYY) — formato que espera Aunesa."""
    arg_holidays = holidays.Argentina()

    def proximo_habil(x: date) -> date:
        x += timedelta(days=1)
        while x.weekday() >= 5 or x in arg_holidays:
            x += timedelta(days=1)
        return x

    return proximo_habil(proximo_habil(d)).strftime("%d/%m/%Y")


def _consultar_con_retry(
    cuenta_id: str,
    headers_ref: dict,
    headers_lock: threading.Lock,
    desde: str,
) -> tuple[list | None, str | None]:
    """Llama consultar_posicion con retry-on-timeout y re-auth.

    Returns:
        (data, error_msg). Si todo OK: (json_response, None).
        Si error agotó retries: (None, "msg").
    """
    last_err: str | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            with headers_lock:
                h = dict(headers_ref)
            data, necesita_reauth = consultar_posicion(cuenta_id, h, desde)
            if necesita_reauth:
                with headers_lock:
                    nuevos = autenticar()
                    headers_ref.clear()
                    headers_ref.update(nuevos)
                    h = dict(headers_ref)
                data, _ = consultar_posicion(cuenta_id, h, desde)
            # Aunesa puede devolver lista vacía legítimamente (cuenta sin
            # posiciones) — eso NO es error.
            return data, None
        except requests.exceptions.Timeout as e:
            last_err = f"timeout (attempt {attempt}/{MAX_RETRIES}): {e}"
            if attempt < MAX_RETRIES:
                wait = BACKOFF_BASE_S ** attempt
                time.sleep(wait)
                continue
            return None, last_err
        except requests.exceptions.RequestException as e:
            last_err = f"http (attempt {attempt}/{MAX_RETRIES}): {e}"
            if attempt < MAX_RETRIES:
                time.sleep(BACKOFF_BASE_S ** attempt)
                continue
            return None, last_err
        except Exception as e:
            last_err = f"unexpected: {e}"
            return None, last_err  # no retry on unexpected errors
    return None, last_err


def _persist_run_state(
    col_runs,
    fecha_snapshot: str,
    cuenta_id: str,
    status: str,
    error_msg: str | None,
    n_registros: int,
) -> None:
    """Append-only audit log de cada intento."""
    col_runs.update_one(
        {"fecha_snapshot": fecha_snapshot, "id_cuenta": cuenta_id},
        {"$set": {
            "fecha_snapshot": fecha_snapshot,
            "id_cuenta":      cuenta_id,
            "status":         status,
            "error_msg":      error_msg,
            "n_registros":    n_registros,
            "ts":             datetime.now(UTC),
        }},
        upsert=True,
    )


def _replace_cuenta_data(
    col_aum,
    fecha_snapshot: str,
    cuenta_id: str,
    registros: list[dict],
) -> tuple[int, int]:
    """Per-cuenta replace: borra rows previos de (fecha, cuenta) e
    inserta los nuevos. Otras cuentas no son tocadas.

    Returns:
        (deleted, inserted)
    """
    deleted = col_aum.delete_many({
        "fecha_snapshot": fecha_snapshot,
        "id_cuenta":      cuenta_id,
    }).deleted_count
    if not registros:
        return deleted, 0
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
        for r in registros
    ]
    col_aum.bulk_write(ops, ordered=False)
    return deleted, len(registros)


def _procesar_cuenta(
    cuenta_id: str,
    fecha: date,
    headers_ref: dict,
    headers_lock: threading.Lock,
    dry: bool,
) -> dict:
    """Procesa una sola (fecha, cuenta). Devuelve dict con métricas."""
    fecha_snapshot = fecha.isoformat()
    desde = _t2_de(fecha)
    timestamp = datetime.combine(fecha, datetime.min.time().replace(hour=23))

    data, error_msg = _consultar_con_retry(cuenta_id, headers_ref, headers_lock, desde)

    client = get_mongo_client()
    col_aum = client[DB_NAME][COL_AUM]
    col_runs = client[DB_NAME][COL_RUNS]

    if data is None:
        # Falla — NO tocamos AuM, dejamos rows existentes intactos.
        if not dry:
            _persist_run_state(col_runs, fecha_snapshot, cuenta_id, "failed", error_msg, 0)
        return {"cuenta_id": cuenta_id, "status": "failed", "error": error_msg, "n": 0}

    registros = procesar(data, fecha_snapshot, timestamp)

    if dry:
        return {"cuenta_id": cuenta_id, "status": "dry", "n": len(registros)}

    deleted, inserted = _replace_cuenta_data(col_aum, fecha_snapshot, cuenta_id, registros)
    status = "ok" if inserted > 0 else "empty"
    _persist_run_state(col_runs, fecha_snapshot, cuenta_id, status, None, inserted)
    return {"cuenta_id": cuenta_id, "status": status, "deleted": deleted, "inserted": inserted}


def _backfill_pairs(
    pairs: list[tuple[date, str]],
    headers_ref: dict,
    headers_lock: threading.Lock,
    dry: bool,
    workers: int,
) -> dict[str, int]:
    """Procesa una lista de (fecha, id_cuenta) en paralelo. Persiste y
    audita per-cuenta. Devuelve estadísticas agregadas."""
    stats = defaultdict(int)
    total = len(pairs)

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(_procesar_cuenta, cuenta, fecha, headers_ref, headers_lock, dry): (fecha, cuenta)
            for fecha, cuenta in pairs
        }
        for done, f in enumerate(as_completed(futures), 1):
            try:
                res = f.result()
                stats[res["status"]] += 1
                stats["total"] = total
                if done % 50 == 0 or done == total:
                    logger.info(
                        "Progress %d/%d · ok=%d empty=%d failed=%d dry=%d",
                        done, total,
                        stats["ok"], stats["empty"], stats["failed"], stats["dry"],
                    )
                if res["status"] == "failed":
                    logger.warning(
                        "  ❌ cuenta %s: %s",
                        res["cuenta_id"], res.get("error", "(no msg)"),
                    )
            except Exception as e:
                logger.exception("Excepción procesando %s: %s", futures[f], e)
                stats["failed"] += 1
    return dict(stats)


def _all_cuentas_for_fecha(cuentas) -> list[str]:
    """Lista de id_cuenta del listado actual de Aunesa."""
    return [str(row["id"]) for _, row in cuentas.iterrows()]


def _pairs_from_args(args, cuentas_listado_ids: list[str]) -> list[tuple[date, str]]:
    """Resuelve la lista (fecha, cuenta) según los flags."""
    if args.from_csv:
        path = Path(args.from_csv)
        if not path.exists():
            raise SystemExit(f"CSV no existe: {path}")
        pairs: list[tuple[date, str]] = []
        with path.open("r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                f_str = row.get("fecha_snapshot")
                c_str = row.get("id_cuenta")
                if not f_str or not c_str:
                    continue
                pairs.append((date.fromisoformat(f_str), c_str.strip()))
        return pairs

    # Resolve fechas
    fechas: list[date] = []
    if args.fecha:
        fechas = [date.fromisoformat(args.fecha)]
    elif args.desde and args.hasta:
        d = date.fromisoformat(args.desde)
        end = date.fromisoformat(args.hasta)
        while d <= end:
            fechas.append(d)
            d += timedelta(days=1)
    elif args.all:
        client = get_mongo_client()
        col = client[DB_NAME][COL_AUM]
        unique = sorted(col.distinct("fecha_snapshot"))
        fechas = [date.fromisoformat(f) for f in unique if f]
    else:
        raise SystemExit("Especificá --fecha, --desde/--hasta, --all, o --from-csv")

    # Cross product con todas las cuentas activas.
    return [(f, c) for f in fechas for c in cuentas_listado_ids]


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--fecha", help="YYYY-MM-DD (single day)")
    parser.add_argument("--desde", help="YYYY-MM-DD inclusive (con --hasta)")
    parser.add_argument("--hasta", help="YYYY-MM-DD inclusive (con --desde)")
    parser.add_argument("--all", action="store_true",
                        help="Todas las fecha_snapshots únicas en AuM")
    parser.add_argument("--from-csv",
                        help="Path al CSV de audit_aum_corruption (cols: fecha_snapshot,id_cuenta,sources)")
    parser.add_argument("--workers", type=int, default=8,
                        help="Threads paralelos para llamadas a Aunesa (default 8)")
    parser.add_argument("--dry", action="store_true",
                        help="No escribe a Mongo, solo reporta")
    args = parser.parse_args()

    logger.info("🔑 Auth Aunesa…")
    headers_ref = autenticar()
    headers_lock = threading.Lock()

    logger.info("📋 Listado de cuentas activas…")
    cuentas = obtener_cuentas(headers_ref)
    cuentas_listado_ids = _all_cuentas_for_fecha(cuentas)
    logger.info("   %d cuentas activas en el listado actual", len(cuentas_listado_ids))

    pairs = _pairs_from_args(args, cuentas_listado_ids)
    if not pairs:
        logger.warning("Nada para procesar.")
        return 0

    fechas_unicas = sorted({p[0] for p in pairs})
    cuentas_unicas = sorted({p[1] for p in pairs})
    logger.info("🎯 %d pares a procesar · %d fechas · %d cuentas%s",
                len(pairs), len(fechas_unicas), len(cuentas_unicas),
                "  (DRY)" if args.dry else "")

    stats = _backfill_pairs(pairs, headers_ref, headers_lock, args.dry, args.workers)

    logger.info("══════════════════════════════════════════")
    logger.info("🏁 %d pares procesados", stats.get("total", 0))
    logger.info("   ok       = %d", stats.get("ok", 0))
    logger.info("   empty    = %d  (cuenta sin posiciones legítimo)", stats.get("empty", 0))
    logger.info("   failed   = %d  (timeouts/HTTP — AuM intacto, ver AumBackfillRuns)",
                stats.get("failed", 0))
    if args.dry:
        logger.info("   dry      = %d  (nada escrito)", stats.get("dry", 0))
    return 0 if stats.get("failed", 0) == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
