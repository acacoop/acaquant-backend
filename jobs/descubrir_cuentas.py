"""Descubre cuentas autorizadas para el user master del broker.

Itera un rango (default 1-12000), llama get_account_report por cada
una, y persiste en `Operaciones.AccountsDescubiertas` las que el
broker autoriza (status=OK). Las que tira "no autorizada" / "Invalid"
se ignoran silencioso.

Diseño:
  - Pensado como BACKFILL: la primera corrida es larga (~40 min para
    12000 cuentas con sleep 0.2). Después corre 1 vez/día por cron
    para detectar cuentas nuevas que el broker haya agregado y
    refrescar el snapshot de saldos.
  - Idempotente: cada cuenta se upsertea por `account_id`. Las que
    desaparecen del broker NO se borran — quedan con `last_discovered_at`
    viejo, así el endpoint de listado puede filtrar por "vista en las
    últimas 24h" si quiere.
  - NO escribe en el broker: 100% read-only.

Uso:
    python -m jobs.descubrir_cuentas                       # 1-12000
    python -m jobs.descubrir_cuentas --desde 1 --hasta 500 # subset
    python -m jobs.descubrir_cuentas --sleep 0.5           # rate-limit-friendly
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from datetime import UTC, datetime

import pyRofex
from dotenv import load_dotenv
from pymongo import ASCENDING, UpdateOne

from core.mongo import get_mongo_client

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("jobs.descubrir_cuentas")

DB = "Operaciones"
COL = "AccountsDescubiertas"


def _read(name: str) -> str | None:
    v = (os.getenv(name) or "").strip()
    return v or None


def _ensure_indexes() -> None:
    col = get_mongo_client()[DB][COL]
    col.create_index([("account_id", ASCENDING)], unique=True)
    col.create_index([("last_discovered_at", ASCENDING)])
    col.create_index([("activa", ASCENDING)])


def _login() -> str:
    """Inicializa pyRofex con las creds del .env y devuelve account de login."""
    user = _read("ROFEX_USER")
    password = _read("ROFEX_PASSWORD")
    account = _read("ROFEX_ACCOUNT")
    if not (user and password and account):
        raise RuntimeError("Faltan ROFEX_USER / ROFEX_PASSWORD / ROFEX_ACCOUNT en .env")

    api_url = (os.getenv("ROFEX_API_URL") or "").strip()
    ws_url = (os.getenv("ROFEX_WS_URL") or "").strip()
    if api_url:
        pyRofex._set_environment_parameter("url", api_url, pyRofex.Environment.LIVE)
    if ws_url:
        pyRofex._set_environment_parameter("ws", ws_url, pyRofex.Environment.LIVE)

    pyRofex.initialize(
        user=user, password=password, account=account,
        environment=pyRofex.Environment.LIVE,
    )
    logger.info("pyRofex inicializado (user=%s account_login=%s)", user, account)
    return account


def _probe_account(acc: str) -> dict | None:
    """Devuelve el snapshot de la cuenta si el broker la autoriza, sino None."""
    try:
        rpt = pyRofex.get_account_report(account=acc)
    except Exception as e:
        logger.debug("acc=%s exc en report: %s", acc, e)
        return None
    if not rpt or rpt.get("status") != "OK":
        return None  # "Invalid URL" / "no autorizada" / NPE / etc.

    ad = rpt.get("accountData") or {}
    settle_ci = (ad.get("detailedAccountReports") or {}).get("0") or {}
    cb = (settle_ci.get("currencyBalance") or {}).get("detailedCurrencyBalance") or {}
    ars = (cb.get("ARS") or {}).get("available")
    usd_d = (cb.get("USD D") or {}).get("available")

    n_pos = 0
    try:
        pos = pyRofex.get_account_position(account=acc)
        if pos and pos.get("status") == "OK":
            n_pos = len(pos.get("positions") or [])
    except Exception:
        pass

    return {
        "ars_disponible":   ars,
        "usd_d_disponible": usd_d,
        "n_posiciones":     n_pos,
        "activa":           bool((ars or 0) != 0 or (usd_d or 0) != 0 or n_pos > 0),
    }


def _persist(snapshots: list[tuple[str, dict]]) -> int:
    """Bulk upsert. Devuelve cantidad escrita."""
    if not snapshots:
        return 0
    now = datetime.now(UTC)
    ops = []
    for acc, snap in snapshots:
        ops.append(UpdateOne(
            {"account_id": acc},
            {
                "$set": {
                    "account_id":         acc,
                    "last_discovered_at": now,
                    "last_snapshot":      snap,
                    "activa":             snap["activa"],
                },
                "$setOnInsert": {"primera_vez_at": now},
            },
            upsert=True,
        ))
    res = get_mongo_client()[DB][COL].bulk_write(ops, ordered=False)
    return res.upserted_count + res.modified_count


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--desde", type=int, default=1)
    parser.add_argument("--hasta", type=int, default=12000)
    parser.add_argument("--sleep", type=float, default=0.2)
    parser.add_argument("--batch", type=int, default=200,
                        help="Cuántas cuentas acumular antes de persistir (default 200)")
    args = parser.parse_args()

    if args.hasta < args.desde:
        parser.error("--hasta debe ser >= --desde")

    _ensure_indexes()
    _login()

    total = args.hasta - args.desde + 1
    logger.info(
        "Backfill rango %d..%d (%d cuentas, sleep=%ss, batch=%d) …",
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

            # Persistir en bloques para no perder progreso si el script muere
            if len(pendientes) >= args.batch:
                _persist(pendientes)
                pendientes.clear()
                elapsed = time.monotonic() - t0
                hechos = acc_int - args.desde + 1
                logger.info(
                    "  progreso: %d/%d (%.1f%%) — %d autorizadas, %d activas — %.0fs",
                    hechos, total, 100 * hechos / total, n_autorizadas, n_activas, elapsed,
                )

            time.sleep(args.sleep)

        # Cola final
        if pendientes:
            _persist(pendientes)

    except KeyboardInterrupt:
        logger.warning("interrumpido — persistiendo lo pendiente y saliendo")
        if pendientes:
            _persist(pendientes)
        return 130

    elapsed = time.monotonic() - t0
    logger.info(
        "Listo. Rango %d..%d procesado en %.0fs. Autorizadas=%d, activas=%d.",
        args.desde, args.hasta, elapsed, n_autorizadas, n_activas,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
