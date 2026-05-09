"""
diag_aum_backfill_log.py — inspeccionar Manager.AumBackfillLog.

Modos:

  # Resumen por (run_id, fecha_snapshot) — útil para ver qué corridas
  # hubo y cómo les fue.
  python -m scripts.diag_aum_backfill_log

  # Detalle por cuenta para una fecha — ÚLTIMO intento por id_cuenta.
  python -m scripts.diag_aum_backfill_log --fecha 2025-07-31

  # Solo cuentas con cierto status (CSV).
  python -m scripts.diag_aum_backfill_log --fecha 2025-07-31 --status timeout,error

  # Histórico de una cuenta puntual.
  python -m scripts.diag_aum_backfill_log --id-cuenta 805
"""
from __future__ import annotations

import argparse

from core.mongo import get_mongo_client

_LOG_COLL = "AumBackfillLog"


def _resumen_runs(coll) -> None:
    """Tabla por (run_id, fecha_snapshot) con counts por status."""
    pipeline = [
        {"$group": {
            "_id": {"run_id": "$run_id", "fecha": "$fecha_snapshot"},
            "ok":         {"$sum": {"$cond": [{"$eq": ["$status", "ok"]},        1, 0]}},
            "timeout":    {"$sum": {"$cond": [{"$eq": ["$status", "timeout"]},   1, 0]}},
            "error":      {"$sum": {"$cond": [{"$eq": ["$status", "error"]},     1, 0]}},
            "sin_datos":  {"$sum": {"$cond": [{"$eq": ["$status", "sin_datos"]}, 1, 0]}},
            "started":    {"$min": "$started_at"},
            "finished":   {"$max": "$finished_at"},
        }},
        {"$sort": {"_id.fecha": 1, "started": 1}},
    ]
    rows = list(coll.aggregate(pipeline, allowDiskUse=True))
    if not rows:
        print("(sin runs)")
        return

    print(f"\n  {'fecha_snapshot':<14} {'ok':>5} {'timeout':>8} {'error':>6} "
          f"{'sin_datos':>10}  started_at            run_id")
    print("  " + "-" * 14 + " " + "-" * 5 + " " + "-" * 8 + " " + "-" * 6 + " " + "-" * 10
          + "  " + "-" * 19 + " " + "-" * 36)
    for r in rows:
        rid    = r["_id"]["run_id"]
        fecha  = r["_id"]["fecha"]
        started = r["started"].strftime("%Y-%m-%d %H:%M:%S") if r.get("started") else "-"
        marker = "  ⚠" if r["timeout"] + r["error"] > 0 else ""
        print(f"  {fecha:<14} {r['ok']:>5} {r['timeout']:>8} {r['error']:>6} "
              f"{r['sin_datos']:>10}  {started}  {rid}{marker}")


def _detalle_fecha(coll, fecha: str, status_filter: list[str] | None) -> None:
    """Último intento por id_cuenta para una fecha. Filtro opcional por status."""
    pipeline: list[dict] = [
        {"$match": {"fecha_snapshot": fecha}},
        {"$sort": {"finished_at": -1}},
        {"$group": {
            "_id":              "$id_cuenta",
            "denominacion":     {"$first": "$denominacion"},
            "status":           {"$first": "$status"},
            "n_registros":      {"$first": "$n_registros"},
            "attempts":         {"$first": "$attempts"},
            "error_msg":        {"$first": "$error_msg"},
            "duration_ms":      {"$first": "$duration_ms"},
            "finished_at":      {"$first": "$finished_at"},
        }},
        {"$sort": {"status": 1, "_id": 1}},
    ]
    if status_filter:
        pipeline.insert(-1, {"$match": {"status": {"$in": status_filter}}})

    rows = list(coll.aggregate(pipeline, allowDiskUse=True))
    if not rows:
        print(f"(sin entries para fecha={fecha} status={status_filter})")
        return

    counts: dict[str, int] = {}
    for r in rows:
        counts[r["status"]] = counts.get(r["status"], 0) + 1

    print(f"\nFecha {fecha} — {len(rows)} cuentas | "
          f"{', '.join(f'{k}={v}' for k, v in sorted(counts.items()))}")
    print(f"\n  {'id_cuenta':>8} {'status':<10} {'n_pos':>6} {'att':>4} "
          f"{'ms':>7}  denominacion / error")
    print("  " + "-" * 8 + " " + "-" * 10 + " " + "-" * 6 + " " + "-" * 4 + " "
          + "-" * 7 + "  " + "-" * 50)
    for r in rows:
        cid   = r["_id"]
        denom = (r.get("denominacion") or "")[:40]
        err   = r.get("error_msg") or ""
        col   = denom if r["status"] in ("ok", "sin_datos") else f"{denom} | {err[:40]}"
        print(f"  {cid:>8} {r['status']:<10} {r.get('n_registros') or 0:>6} "
              f"{r.get('attempts') or 0:>4} {r.get('duration_ms') or 0:>7}  {col}")


def _historico_cuenta(coll, id_cuenta: str) -> None:
    """Todos los intentos por fecha para una cuenta puntual."""
    rows = list(coll.find(
        {"id_cuenta": id_cuenta},
        {"_id": 0, "fecha_snapshot": 1, "status": 1, "n_registros": 1,
         "attempts": 1, "error_msg": 1, "finished_at": 1, "run_id": 1},
    ).sort("finished_at", -1))
    if not rows:
        print(f"(sin entries para id_cuenta={id_cuenta})")
        return

    print(f"\nCuenta {id_cuenta} — {len(rows)} intentos\n")
    print(f"  {'fecha':<14} {'status':<10} {'n_pos':>6} {'att':>4}  finished_at         "
          f"  run_id")
    print("  " + "-" * 14 + " " + "-" * 10 + " " + "-" * 6 + " " + "-" * 4 + "  "
          + "-" * 19 + "  " + "-" * 36)
    for r in rows:
        f  = r.get("finished_at")
        fs = f.strftime("%Y-%m-%d %H:%M:%S") if f else "-"
        err = r.get("error_msg") or ""
        line = (f"  {r['fecha_snapshot']:<14} {r['status']:<10} "
                f"{r.get('n_registros') or 0:>6} {r.get('attempts') or 0:>4}  "
                f"{fs}  {r.get('run_id') or '-'}")
        print(line)
        if err:
            print(f"      error: {err[:100]}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fecha",      help="fecha_snapshot YYYY-MM-DD — detalle por cuenta")
    ap.add_argument("--id-cuenta",  help="id_cuenta — historial de la cuenta")
    ap.add_argument("--status",     help="CSV de status a filtrar (ok,timeout,error,sin_datos)")
    args = ap.parse_args()

    client = get_mongo_client()
    coll = client["Manager"][_LOG_COLL]

    if args.id_cuenta:
        _historico_cuenta(coll, args.id_cuenta)
    elif args.fecha:
        status_filter = (
            [s.strip() for s in args.status.split(",") if s.strip()]
            if args.status else None
        )
        _detalle_fecha(coll, args.fecha, status_filter)
    else:
        _resumen_runs(coll)


if __name__ == "__main__":
    main()
