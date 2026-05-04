"""migrate_timesales_swap.py — migración real de Trading.TimeSales a Time Series Collection.

A diferencia de migrate_timesales_test.py (que crea TimeSales_test sin tocar la
original), este script HACE EL SWAP: la colección TimeSales actual queda
renombrada a TimeSales_old (backup) y una nueva TimeSales TS Collection toma
su lugar.

Modos (ejecutar en orden):
    python -m scripts.migrate_timesales_swap --mode precheck
    python -m scripts.migrate_timesales_swap --mode swap
    python -m scripts.migrate_timesales_swap --mode validate
    # ... esperar 24-48h, validar que todo anda en producción ...
    python -m scripts.migrate_timesales_swap --mode cleanup

Si algo sale mal después del swap:
    python -m scripts.migrate_timesales_swap --mode rollback

Detalles:
- timeField=timestamp, metaField=ticker, granularity=seconds (default).
- Sólo motores stopped (rueda cerrada) — el script no fuerza nada, asume
  que ya verificaste el estado.
- Idempotente parcial: si TimeSales_ts ya existe (por reintento), --force
  la borra y empieza de nuevo.
- El swap usa renameCollection (Mongo 6.0+, soportado por Atlas M10 actual).
"""
from __future__ import annotations

import argparse
import logging
import time

from core.mongo import get_mongo_client

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
logger = logging.getLogger("migrate_swap")

DB_NAME = "Trading"
SRC = "TimeSales"
TMP = "TimeSales_ts"
BACKUP = "TimeSales_old"

TIMESERIES_OPTIONS = {
    "timeField":   "timestamp",
    "metaField":   "ticker",
    "granularity": "seconds",
}

# Solo copiamos los campos REALES del trade. Los campos enriquecidos viejos
# (TEA, TEM, duration, mod_duration, paridad, convexity) son legacy: motor_curvas
# dejó de escribirlos en TimeSales el 2026-05-04. La data analítica vive en
# MarketSnapshot.metrics (live) y SnapshotsCierre (histórico). No tiene sentido
# arrastrarlos a la TS Collection nueva.
FIELDS_KEEP = ("timestamp", "ticker", "price", "size", "side", "money")

COPY_BATCH = 5000


def _coll_info(db, name: str) -> dict | None:
    return next(db.list_collections(filter={"name": name}), None)


def _is_ts(info: dict | None) -> bool:
    return bool((info or {}).get("options", {}).get("timeseries"))


def _fmt_size(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.2f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}k"
    return str(n)


def mode_precheck() -> int:
    """Reporta estado actual: counts, tipo de colección, indexes."""
    client = get_mongo_client()
    db = client[DB_NAME]
    print()
    print("=" * 60)
    print(" PRE-CHECK migración TimeSales → TS Collection")
    print("=" * 60)

    src_info = _coll_info(db, SRC)
    tmp_info = _coll_info(db, TMP)
    bak_info = _coll_info(db, BACKUP)

    if not src_info:
        print(f"  [!! ] {DB_NAME}.{SRC} NO existe.")
        return 1

    src_is_ts = _is_ts(src_info)
    src_count = db[SRC].count_documents({})
    print(f"\n  {DB_NAME}.{SRC}:")
    print(f"    tipo            : {'TS Collection' if src_is_ts else 'regular'}")
    print(f"    docs totales    : {_fmt_size(src_count)}")
    print(f"    índices         : {[i['name'] for i in db[SRC].list_indexes()]}")

    if src_is_ts:
        print("    [OK] Ya es TS — no hace falta migrar.")
        return 0

    if tmp_info:
        tmp_count = db[TMP].count_documents({})
        print(f"\n  {DB_NAME}.{TMP} (temporal):")
        print(f"    tipo            : {'TS Collection' if _is_ts(tmp_info) else 'regular'}")
        print(f"    docs totales    : {_fmt_size(tmp_count)}")
        print("    [WARN] Ya existe — corré --mode swap --force para resetear.")

    if bak_info:
        bak_count = db[BACKUP].count_documents({})
        print(f"\n  {DB_NAME}.{BACKUP} (backup):")
        print(f"    tipo            : {'TS Collection' if _is_ts(bak_info) else 'regular'}")
        print(f"    docs totales    : {_fmt_size(bak_count)}")
        print("    [WARN] El swap ya se hizo. Si querés migrar de nuevo,")
        print("           corré --mode cleanup primero o renombrá manual.")

    # Sample doc
    sample = db[SRC].find_one()
    if sample:
        print("\n  sample doc keys (esperados: timestamp, ticker, price, size, side, money):")
        print(f"    {sorted(sample.keys())}")

    print()
    print(" Listo para correr --mode swap si todo OK.")
    print("=" * 60)
    print()
    return 0


def mode_swap(force: bool = False) -> int:
    """Hace el swap completo (TS Collections no se pueden renombrar, así que
    el orden importa):
    1. Renombra TimeSales (regular) → TimeSales_old.
    2. Crea TimeSales como TS Collection nueva.
    3. Copia TimeSales_old → TimeSales en batches (filtrando campos legacy).
    4. Valida count match.

    Si falla en cualquier paso post-rename, hace rollback automático
    (revierte el rename) para no dejar el sistema sin TimeSales.
    """
    client = get_mongo_client()
    db = client[DB_NAME]
    admin = client.admin

    src_info = _coll_info(db, SRC)
    bak_info = _coll_info(db, BACKUP)

    # Caso A: TimeSales ya es TS → ya migrado, nada que hacer.
    if src_info and _is_ts(src_info):
        logger.info("%s.%s ya es TS — nada que migrar.", DB_NAME, SRC)
        return 0

    # Caso B: estado limpio (TimeSales regular existe, TimeSales_old no).
    # Hace el rename y sigue el flujo completo.
    # Caso C: estado intermedio post intento fallido (TimeSales no existe,
    # TimeSales_old regular existe). Skipea el rename y continúa.
    if src_info and not _is_ts(src_info) and not bak_info:
        # Caso B
        total_src = db[SRC].count_documents({})
        logger.info("[1/4] Renombrando %s.%s → %s.%s (regular, %s docs)",
                    DB_NAME, SRC, DB_NAME, BACKUP, _fmt_size(total_src))
        admin.command({
            "renameCollection": f"{DB_NAME}.{SRC}",
            "to":               f"{DB_NAME}.{BACKUP}",
        })
    elif not src_info and bak_info and not _is_ts(bak_info):
        # Caso C
        total_src = db[BACKUP].count_documents({})
        logger.info("[1/4] Estado intermedio detectado: %s.%s ya está renombrada "
                    "como %s (%s docs). Skipeo el rename.",
                    DB_NAME, SRC, BACKUP, _fmt_size(total_src))
    else:
        logger.error("Estado inesperado: src_info=%s bak_info=%s. "
                     "Inspeccioná manualmente con --mode precheck.",
                     bool(src_info), bool(bak_info))
        return 1

    # Limpiar TMP residual (puede haber quedado de un intento previo).
    if _coll_info(db, TMP):
        if not force:
            logger.error("%s.%s YA existe (intento previo fallido). "
                         "Corré con --force para borrarla.", DB_NAME, TMP)
            return 1
        logger.info("--force: borrando %s.%s residual.", DB_NAME, TMP)
        db.drop_collection(TMP)

    # ── Paso 2: crear TS Collection con el nombre final ──
    try:
        logger.info("[2/4] Creando %s.%s como TS Collection (timeField=%s, "
                    "metaField=%s, granularity=%s)…",
                    DB_NAME, SRC,
                    TIMESERIES_OPTIONS["timeField"],
                    TIMESERIES_OPTIONS["metaField"],
                    TIMESERIES_OPTIONS["granularity"])
        db.create_collection(SRC, timeseries=TIMESERIES_OPTIONS)
    except Exception as e:
        logger.error("    [!! ] Falló crear TS — rollback: %s", e)
        admin.command({
            "renameCollection": f"{DB_NAME}.{BACKUP}",
            "to":               f"{DB_NAME}.{SRC}",
        })
        return 2

    # ── Paso 3: copiar TimeSales_old → TimeSales (TS) ──
    src = db[BACKUP]
    dst = db[SRC]
    logger.info("[3/4] Copiando %s docs de %s → %s (batches de %d, filtrando "
                "campos legacy)…",
                _fmt_size(total_src), BACKUP, SRC, COPY_BATCH)

    t0 = time.time()
    copied = 0
    batch: list[dict] = []
    last_progress = 0
    projection = {k: 1 for k in FIELDS_KEEP}
    projection["_id"] = 0
    try:
        for doc in src.find({}, projection, sort=[("timestamp", 1)]):
            clean = {k: doc[k] for k in FIELDS_KEEP if k in doc}
            batch.append(clean)
            if len(batch) >= COPY_BATCH:
                dst.insert_many(batch, ordered=False)
                copied += len(batch)
                batch = []
                pct = (copied * 100) // max(total_src, 1)
                if pct >= last_progress + 5:
                    last_progress = pct - (pct % 5)
                    elapsed = time.time() - t0
                    rate = copied / max(elapsed, 0.001)
                    eta = (total_src - copied) / max(rate, 1)
                    logger.info("    %s/%s (%d%%) · %.0f docs/s · ETA %.0fs",
                                _fmt_size(copied), _fmt_size(total_src), pct,
                                rate, eta)
        if batch:
            dst.insert_many(batch, ordered=False)
            copied += len(batch)
    except Exception as e:
        logger.error("    [!! ] Falló la copia — rollback: %s", e)
        db.drop_collection(SRC)
        admin.command({
            "renameCollection": f"{DB_NAME}.{BACKUP}",
            "to":               f"{DB_NAME}.{SRC}",
        })
        return 3

    elapsed = time.time() - t0
    logger.info("    Copy done en %.1fs (%.0f docs/s)",
                elapsed, copied / max(elapsed, 0.001))

    # ── Paso 4: validar count match ──
    logger.info("[4/4] Validando count match…")
    n_src = src.count_documents({})
    n_dst = dst.count_documents({})
    if n_src != n_dst:
        logger.error("    [!! ] count MISMATCH: backup=%d new=%d (diff %+d). "
                     "ROLLBACK automático.", n_src, n_dst, n_dst - n_src)
        db.drop_collection(SRC)
        admin.command({
            "renameCollection": f"{DB_NAME}.{BACKUP}",
            "to":               f"{DB_NAME}.{SRC}",
        })
        return 4
    logger.info("    [OK] count match: %s docs en ambas.", _fmt_size(n_src))

    final_info = _coll_info(db, SRC)
    if not _is_ts(final_info):
        logger.error("    [!! ] %s.%s POST-swap NO es TS.", DB_NAME, SRC)
        return 5

    final_count = db[SRC].count_documents({})
    logger.info("    [OK] %s.%s ahora es TS Collection con %s docs.",
                DB_NAME, SRC, _fmt_size(final_count))

    print()
    print("=" * 60)
    print(" ✓ SWAP COMPLETADO")
    print("=" * 60)
    print(f"  {DB_NAME}.{SRC}     : TS Collection ({_fmt_size(final_count)} docs)")
    print(f"  {DB_NAME}.{BACKUP}  : backup regular ({_fmt_size(n_src)} docs)")
    print()
    print(" Próximos pasos:")
    print("   1. Correr: python -m scripts.migrate_timesales_swap --mode validate")
    print("   2. Probar la app/MM en el browser.")
    print("   3. Mañana 13 UTC el motor arranca y escribe a la nueva TS.")
    print("   4. Después de 24-48h sin issues:")
    print("      python -m scripts.migrate_timesales_swap --mode cleanup")
    print("=" * 60)
    print()
    return 0


def mode_validate() -> int:
    """Compara counts y queries de prueba contra TimeSales_old para confirmar
    integridad post-swap."""
    client = get_mongo_client()
    db = client[DB_NAME]

    src_info = _coll_info(db, SRC)
    bak_info = _coll_info(db, BACKUP)

    if not src_info or not bak_info:
        logger.error("Falta %s o %s — corré --mode swap primero.", SRC, BACKUP)
        return 1

    if not _is_ts(src_info):
        logger.error("%s.%s NO es TS Collection — el swap no se hizo.",
                     DB_NAME, SRC)
        return 1

    print()
    print("=" * 60)
    print(" VALIDATE post-swap")
    print("=" * 60)

    n_src = db[SRC].count_documents({})
    n_bak = db[BACKUP].count_documents({})
    flag = "[OK]" if n_src == n_bak else "[!! ]"
    print(f"  {flag} count: TimeSales={_fmt_size(n_src)}  "
          f"TimeSales_old={_fmt_size(n_bak)}")

    # Query random: últimos 100 trades por ticker.
    sample_ticker = db[SRC].find_one({})
    if sample_ticker:
        ticker = sample_ticker.get("ticker")
        n1 = db[SRC].count_documents({"ticker": ticker})
        n2 = db[BACKUP].count_documents({"ticker": ticker})
        flag = "[OK]" if n1 == n2 else "[!! ]"
        print(f"  {flag} count para ticker={ticker!r}: "
              f"new={_fmt_size(n1)}  old={_fmt_size(n2)}")

    # Estimación de tamaño en disco (solo en server, comando collStats).
    print("\n  Tamaño en disco (collStats):")
    for col in (SRC, BACKUP):
        try:
            stats = db.command("collStats", col)
            size_bytes = stats.get("size", 0)
            storage_size = stats.get("storageSize", 0)
            mb = size_bytes / (1024 * 1024)
            mb_storage = storage_size / (1024 * 1024)
            print(f"    {col:18s} · uncompressed {mb:.1f} MB · "
                  f"on disk {mb_storage:.1f} MB")
        except Exception as e:
            print(f"    {col}: error collStats — {e}")

    print()
    print("=" * 60)
    print()
    return 0


def mode_rollback() -> int:
    """Revierte el swap: TimeSales (TS) → TimeSales_failed_swap;
    TimeSales_old → TimeSales. Solo usar si la migración rompió algo."""
    client = get_mongo_client()
    db = client[DB_NAME]
    admin = client.admin

    src_info = _coll_info(db, SRC)
    bak_info = _coll_info(db, BACKUP)

    if not bak_info:
        logger.error("%s.%s NO existe — no hay nada que revertir.", DB_NAME, BACKUP)
        return 1
    if not src_info:
        logger.warning("%s.%s no existe — voy directo a renombrar backup.", DB_NAME, SRC)
    elif _is_ts(src_info):
        logger.info("Renombrando TimeSales (TS, fallida) → TimeSales_failed_swap")
        admin.command({
            "renameCollection": f"{DB_NAME}.{SRC}",
            "to":               f"{DB_NAME}.TimeSales_failed_swap",
        })
    else:
        logger.error("%s.%s ya es regular (no TS) — el rollback ya se hizo.",
                     DB_NAME, SRC)
        return 1

    logger.info("Renombrando %s → %s", BACKUP, SRC)
    admin.command({
        "renameCollection": f"{DB_NAME}.{BACKUP}",
        "to":               f"{DB_NAME}.{SRC}",
    })

    n = db[SRC].count_documents({})
    logger.info("[OK] Rollback completado. %s.%s vuelve a ser regular con "
                "%s docs.", DB_NAME, SRC, _fmt_size(n))
    return 0


def mode_cleanup(yes: bool = False) -> int:
    """Borra TimeSales_old definitivamente. Confirmación obligatoria con --yes."""
    client = get_mongo_client()
    db = client[DB_NAME]

    if not _coll_info(db, BACKUP):
        logger.info("%s.%s no existe — nada que limpiar.", DB_NAME, BACKUP)
        return 0

    n = db[BACKUP].count_documents({})
    if not yes:
        print()
        print("=" * 60)
        print(f"  ATENCIÓN: vas a borrar {DB_NAME}.{BACKUP} ({_fmt_size(n)} docs).")
        print("  Esto es IRREVERSIBLE (excepto via snapshot Atlas).")
        print("  Confirmar con: --mode cleanup --yes")
        print("=" * 60)
        return 1

    db.drop_collection(BACKUP)
    logger.info("[OK] %s.%s borrada (%s docs).", DB_NAME, BACKUP, _fmt_size(n))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--mode", required=True,
                        choices=["precheck", "swap", "validate", "rollback", "cleanup"])
    parser.add_argument("--force", action="store_true",
                        help="(--mode swap) borra TimeSales_ts residual antes de empezar.")
    parser.add_argument("--yes", action="store_true",
                        help="(--mode cleanup) confirma borrado de TimeSales_old.")
    args = parser.parse_args()

    if args.mode == "precheck":
        return mode_precheck()
    if args.mode == "swap":
        return mode_swap(force=args.force)
    if args.mode == "validate":
        return mode_validate()
    if args.mode == "rollback":
        return mode_rollback()
    if args.mode == "cleanup":
        return mode_cleanup(yes=args.yes)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
