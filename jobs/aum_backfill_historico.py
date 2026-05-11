"""
aum_backfill_historico.py — backfill de cierres de mes (jul-2025 → feb-2026).

Diferencias con jobs/aum_backfill.py:
  1. Usa TODOS los id_cuenta únicos vistos alguna vez en Valuaciones.AuM
     (distinct global, no llama a Aunesa /listado). Cubre cuentas que
     pueden haberse cerrado pero que en la fecha histórica sí existían.
  2. `desde` = MISMA fecha del backfill (NO T+2). Aunesa procesa
     directamente la fecha que le mandamos.
  3. fecha_snapshot = ÚLTIMO DÍA CALENDARIO del mes (NO ajustado por
     hábil). Aunesa devuelve el dato del día hábil correspondiente.
  4. Por cada (fecha_snapshot, id_cuenta) persiste un doc en
     Manager.AumBackfillLog con outcome (ok/timeout/error/sin_datos),
     reintentos y error_msg — auditable post-hoc.
  5. Lista de meses target hardcoded — el plan es jul-2025 a feb-2026.
     A partir de marzo-2026 ya hay daily.
  6. NO toca jobs/aum.py (cron diario). Solo importa helpers read-only.

Uso típico:
    # Todos los meses (jul-2025 a feb-2026)
    python -m jobs.aum_backfill_historico

    # Un solo mes
    python -m jobs.aum_backfill_historico --mes 2025-07

    # Reintentar solo las cuentas que dieron timeout/error en un mes
    python -m jobs.aum_backfill_historico --mes 2025-07 --solo-fallidas

    # Para fechas viejas Aunesa puede ser MUY lenta — bajar paralelismo:
    python -m jobs.aum_backfill_historico --mes 2025-07 --workers 2 --timeout 360 --retries 5

Para inspeccionar los logs guardados:
    python -m scripts.diag_aum_backfill_log
    python -m scripts.diag_aum_backfill_log --fecha 2025-07-31
    python -m scripts.diag_aum_backfill_log --fecha 2025-07-31 --status timeout,error
"""
from __future__ import annotations

import argparse
import os
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta

from pymongo import UpdateOne

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from core.mongo import get_mongo_client
from jobs.aum import (
    _sincronizar_assets,
    autenticar,
    consultar_posicion,
    procesar,
)

# Lista fija de meses pendientes de cierre real. Ampliar manualmente si crece.
_MESES_TARGET = [
    "2025-07", "2025-08", "2025-09", "2025-10",
    "2025-11", "2025-12", "2026-01", "2026-02",
]

_LOG_COLL_NAME = "AumBackfillLog"


def _ultimo_dia_del_mes(yyyy_mm: str) -> datetime:
    """Último día CALENDARIO del mes (sin ajustar por hábil/feriado).
    Aunesa procesa la fecha que le mandamos — no calculamos T+2 ni
    ajuste hábil acá."""
    year, month = (int(p) for p in yyyy_mm.split("-"))
    if month == 12:
        return datetime(year, 12, 31)
    return datetime(year, month + 1, 1) - timedelta(days=1)


def _cuentas_universo(client) -> list[tuple[str, str]]:
    """TODOS los id_cuenta únicos que alguna vez aparecieron en AuM,
    como (id_cuenta, denominacion).

    `$first` sobre cuenta string toma la primera denominacion vista — si
    la cuenta cambió de nombre en el tiempo es la más vieja, lo que está
    bien para fines de auditoría (igual el log persiste denominacion al
    momento del backfill, no la de hoy).

    Cubre cuentas que pueden haber sido activas en algún momento del
    pasado pero ya no aparecen en el último snapshot — para tener
    universo completo y no dejar huecos en backfills históricos.
    """
    db = client["Valuaciones"]
    pipeline = [
        {"$group": {
            "_id":     "$id_cuenta",
            "cuenta":  {"$first": "$cuenta"},
        }},
        {"$sort": {"_id": 1}},
    ]
    out: list[tuple[str, str]] = []
    for d in db["AuM"].aggregate(pipeline, allowDiskUse=True):
        cid = str(d.get("_id") or "").strip()
        if not cid:
            continue
        cuenta_str = d.get("cuenta") or ""
        if "] " in cuenta_str:
            denom = cuenta_str.split("] ", 1)[1]
        else:
            denom = cuenta_str
        out.append((cid, denom))
    return out


def _ids_fallidas_previas(db_mgr, fecha_snapshot: str) -> set[str]:
    """Devuelve los id_cuenta cuyo ÚLTIMO intento para esa fecha fue
    timeout o error (ignora ok y sin_datos). Sirve para `--solo-fallidas`."""
    pipeline = [
        {"$match": {"fecha_snapshot": fecha_snapshot}},
        {"$sort": {"finished_at": -1}},
        {"$group": {
            "_id":            "$id_cuenta",
            "ultimo_status":  {"$first": "$status"},
        }},
        {"$match": {"ultimo_status": {"$in": ["timeout", "error"]}}},
    ]
    return {d["_id"] for d in db_mgr[_LOG_COLL_NAME].aggregate(pipeline)}


def _ensure_log_indexes(db_mgr) -> None:
    coll = db_mgr[_LOG_COLL_NAME]
    coll.create_index([("fecha_snapshot", 1), ("status", 1)])
    coll.create_index([("id_cuenta", 1), ("fecha_snapshot", 1)])
    coll.create_index([("run_id", 1)])
    # Index único por (run_id, fecha, cuenta) — un doc por intento dentro del run.
    coll.create_index(
        [("run_id", 1), ("fecha_snapshot", 1), ("id_cuenta", 1)],
        unique=True,
    )


def _classify_error(exc: Exception) -> str:
    """timeout / error según el tipo de excepción."""
    name = type(exc).__name__.lower()
    msg = str(exc).lower()
    if "timeout" in name or "timeout" in msg or "timed out" in msg:
        return "timeout"
    return "error"


def backfill_un_mes(
    *,
    yyyy_mm: str,
    cuentas: list[tuple[str, str]],
    headers_ref: dict,
    headers_lock: threading.Lock,
    client,
    log_coll,
    workers: int,
    timeout: int,
    retries: int,
) -> dict:
    """Corre el backfill para un mes (último día calendario). Devuelve resumen counts."""
    fecha_dt   = _ultimo_dia_del_mes(yyyy_mm)
    fecha_iso  = fecha_dt.strftime("%Y-%m-%d")
    timestamp  = fecha_dt.replace(hour=23, minute=0, second=0)
    # `desde` = MISMA fecha del backfill (no T+2). Aunesa procesa la fecha
    # que le mandamos y devuelve el dato del día correspondiente.
    desde      = fecha_dt.strftime("%d/%m/%Y")
    run_id     = str(uuid.uuid4())

    print(f"\n{'=' * 70}")
    print(f"📅 {yyyy_mm} → fecha_snapshot={fecha_iso} | desde={desde}")
    print(f"   run_id={run_id} | {len(cuentas)} cuentas | "
          f"workers={workers} timeout={timeout}s retries={retries}")
    print(f"{'=' * 70}\n", flush=True)

    aum_coll = client["Valuaciones"]["AuM"]
    counts = {"ok": 0, "timeout": 0, "error": 0, "sin_datos": 0}
    counts_lock = threading.Lock()

    def _consultar(cid: str, denom: str, idx: int) -> tuple[str, list]:
        """Consulta una cuenta con retry + log a Mongo. Devuelve (status, registros)."""
        started = datetime.utcnow()
        last_err: str = ""
        n_registros = 0
        attempts = 0
        status = "error"
        for intento in range(1, retries + 1):
            attempts = intento
            try:
                with headers_lock:
                    h = dict(headers_ref)
                data, necesita_reauth = consultar_posicion(cid, h, desde, timeout=timeout)
                if necesita_reauth:
                    with headers_lock:
                        nuevos = autenticar()
                        headers_ref.clear()
                        headers_ref.update(nuevos)
                        h = dict(headers_ref)
                    data, _ = consultar_posicion(cid, h, desde, timeout=timeout)
                if not data:
                    status = "sin_datos"
                    print(f"[{idx:4d}/{len(cuentas)}] [{cid}] {denom[:40]} → sin datos",
                          flush=True)
                    break
                registros = procesar(data, fecha_iso, timestamp)
                n_registros = len(registros)
                status = "ok"
                tag = f" (intento {intento})" if intento > 1 else ""
                print(f"[{idx:4d}/{len(cuentas)}] [{cid}] {denom[:40]} "
                      f"→ {n_registros} pos{tag}", flush=True)
                _save_outcome(
                    log_coll, run_id, fecha_iso, cid, denom,
                    status, n_registros, attempts, "",
                    started, datetime.utcnow(),
                )
                with counts_lock:
                    counts[status] += 1
                return status, registros
            except Exception as e:
                last_err = f"{type(e).__name__}: {e}"
                status = _classify_error(e)
                if intento < retries:
                    backoff = 2 ** intento
                    print(f"[{idx:4d}/{len(cuentas)}] [{cid}] ⚠ intento {intento} "
                          f"falló ({last_err}); reintento en {backoff}s", flush=True)
                    time.sleep(backoff)
                else:
                    print(f"[{idx:4d}/{len(cuentas)}] [{cid}] ❌ tras {retries} "
                          f"intentos: {last_err}", flush=True)
        # Solo llega acá si terminó en error/timeout/sin_datos sin registros.
        _save_outcome(
            log_coll, run_id, fecha_iso, cid, denom,
            status, n_registros, attempts, last_err,
            started, datetime.utcnow(),
        )
        with counts_lock:
            counts[status] += 1
        return status, []

    futures_map = {}
    with ThreadPoolExecutor(max_workers=workers) as executor:
        for i, (cid, denom) in enumerate(cuentas):
            futures_map[executor.submit(_consultar, cid, denom, i + 1)] = cid

        registros_total = 0
        for f in as_completed(futures_map):
            status, registros = f.result()
            if status != "ok" or not registros:
                continue
            ops = [
                UpdateOne(
                    {"id_cuenta": r["id_cuenta"], "unidad": r["unidad"],
                     "fecha_snapshot": r["fecha_snapshot"]},
                    {"$set": r},
                    upsert=True,
                )
                for r in registros
            ]
            aum_coll.bulk_write(ops, ordered=False)
            registros_total += len(registros)

    print(f"\n→ {yyyy_mm} resumen: ok={counts['ok']} timeout={counts['timeout']} "
          f"error={counts['error']} sin_datos={counts['sin_datos']} "
          f"| {registros_total} registros persistidos en AuM",
          flush=True)

    if counts["ok"] > 0:
        unidades = client["Valuaciones"]["AuM"].distinct(
            "unidad", {"fecha_snapshot": fecha_iso},
        )
        _sincronizar_assets(client["Valuaciones"]["Assets"], unidades)
        print(f"  ✅ Assets sincronizado: {len(unidades)} unidades",
              flush=True)

    return {"yyyy_mm": yyyy_mm, "fecha_snapshot": fecha_iso,
            "run_id": run_id, **counts,
            "registros_persistidos": registros_total}


def _save_outcome(
    log_coll, run_id: str, fecha_iso: str, cid: str, denom: str,
    status: str, n_registros: int, attempts: int, error_msg: str,
    started_at: datetime, finished_at: datetime,
) -> None:
    """Persistencia idempotente del outcome de un (run, fecha, cuenta)."""
    duration_ms = int((finished_at - started_at).total_seconds() * 1000)
    try:
        log_coll.update_one(
            {"run_id": run_id, "fecha_snapshot": fecha_iso, "id_cuenta": cid},
            {"$set": {
                "run_id":          run_id,
                "fecha_snapshot":  fecha_iso,
                "id_cuenta":       cid,
                "denominacion":    denom,
                "status":          status,
                "n_registros":     n_registros,
                "attempts":        attempts,
                "error_msg":       error_msg or None,
                "duration_ms":     duration_ms,
                "started_at":      started_at,
                "finished_at":     finished_at,
            }},
            upsert=True,
        )
    except Exception as e:
        # Nunca abortar el backfill por un fallo del log — solo print.
        print(f"  ⚠ no pude guardar log para [{cid}]: {e}", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mes",
                    help="YYYY-MM. Si no, corre todos los meses target.")
    ap.add_argument("--solo-fallidas", action="store_true",
                    help="Solo cuentas cuyo último intento fue timeout/error")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--timeout", type=int, default=240)
    ap.add_argument("--retries", type=int, default=3)
    args = ap.parse_args()

    if args.mes:
        if args.mes not in _MESES_TARGET:
            print(f"⚠ {args.mes} no está en MESES_TARGET ({_MESES_TARGET}). "
                  "Si es intencional, edita _MESES_TARGET.", flush=True)
            sys.exit(1)
        meses = [args.mes]
    else:
        meses = list(_MESES_TARGET)

    print("🔑 Autenticando contra Aunesa...", flush=True)
    headers_ref  = autenticar()
    headers_lock = threading.Lock()
    print("✅ Auth OK\n", flush=True)

    client = get_mongo_client()
    db_mgr = client["Manager"]
    _ensure_log_indexes(db_mgr)
    log_coll = db_mgr[_LOG_COLL_NAME]

    cuentas_full = _cuentas_universo(client)
    print(f"📋 {len(cuentas_full)} cuentas (TODOS los id_cuenta únicos de AuM)",
          flush=True)
    if not cuentas_full:
        print("Sin cuentas en AuM — nada para procesar.", flush=True)
        return

    resumenes = []
    for yyyy_mm in meses:
        if args.solo_fallidas:
            fecha_iso = _ultimo_dia_del_mes(yyyy_mm).strftime("%Y-%m-%d")
            ids_fallidas = _ids_fallidas_previas(db_mgr, fecha_iso)
            cuentas_mes = [c for c in cuentas_full if c[0] in ids_fallidas]
            print(f"\n--solo-fallidas: {len(cuentas_mes)} cuentas para {yyyy_mm} "
                  f"(de {len(cuentas_full)} activas)", flush=True)
            if not cuentas_mes:
                print(f"  Nada que reintentar para {yyyy_mm}.", flush=True)
                continue
        else:
            cuentas_mes = cuentas_full

        r = backfill_un_mes(
            yyyy_mm=yyyy_mm,
            cuentas=cuentas_mes,
            headers_ref=headers_ref,
            headers_lock=headers_lock,
            client=client,
            log_coll=log_coll,
            workers=args.workers,
            timeout=args.timeout,
            retries=args.retries,
        )
        resumenes.append(r)

    print(f"\n{'#' * 70}")
    print("RESUMEN GLOBAL")
    print(f"{'#' * 70}")
    for r in resumenes:
        print(f"  {r['yyyy_mm']} ({r['fecha_snapshot']})  "
              f"ok={r['ok']:4d}  timeout={r['timeout']:4d}  "
              f"error={r['error']:4d}  sin_datos={r['sin_datos']:4d}  "
              f"→ {r['registros_persistidos']} pos | run={r['run_id']}")

    if any(r["timeout"] + r["error"] > 0 for r in resumenes):
        print("\n⚠ Hay cuentas en timeout/error. Para reintentar dirigido:")
        for r in resumenes:
            if r["timeout"] + r["error"] > 0:
                print(f"  python -m jobs.aum_backfill_historico --mes {r['yyyy_mm']} "
                      f"--solo-fallidas --workers 1 --timeout 360 --retries 5")


if __name__ == "__main__":
    main()
