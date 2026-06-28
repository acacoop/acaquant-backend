"""compare_manager_sql_vs_mongo.py — GATE de Fase 4 (Manager infra → SQL).

Read-only. Compara el path SQL (`api/services/manager_infra_sql`) vs Mongo de las
lecturas de infra de Manager, para decidir el cutover de lectura (flags
`MANAGER_SQL=1` para job history / audit, `AUTH_SQL=1` para roles/grupos):

  - /jobs/history        → jobs_history_sql            vs Manager.JobRuns.find
  - /jobs/history/stats  → jobs_history_stats_sql      vs Manager.JobRuns.aggregate
  - /roles/audit         → list_audit_sql              vs core.roles.list_audit

SQL ya está poblado por `jobs/sync_postgres.py` (sync_job_runs / sync_role_audit)
como baseline; el dual-write `MANAGER_SQL_WRITE` lo mantiene fresco entre syncs.
Por eso este comparador es corrible YA (off-market, sólo necesita Atlas up) — no
depende de rueda abierta.

El gate exige paridad de **identidad + conteos + campos estables**. Como SQL puede
ir 1 sync por detrás de Mongo (runs nuevos aún no espejados), se reporta el
desfasaje asimétrico (sólo-Mongo / sólo-SQL) aparte: un puñado de runs sólo-Mongo
recientes es ESPERADO y no bloquea; filas sólo-SQL o divergencia de campos en
runs comunes SÍ es bug.

    python -m scripts.compare_manager_sql_vs_mongo
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from core.mongo import get_mongo_client_read
from core.roles import list_audit as list_audit_mongo

_AR_TZ = ZoneInfo("America/Argentina/Buenos_Aires")

_HISTORY_LIMIT = 500   # mismo tope que el endpoint (Query(..., le=500))
_AUDIT_LIMIT = 50
_STATS_DESDE_DIAS = 30


def _fmt_ar(v) -> str | None:
    if not isinstance(v, datetime):
        return v
    aware = v if v.tzinfo else v.replace(tzinfo=UTC)
    return aware.astimezone(_AR_TZ).strftime("%Y-%m-%d %H:%M:%S")


# ── 1. /jobs/history ─────────────────────────────────────────────────────────
def _mongo_history(limit: int) -> list[dict]:
    """Réplica EXACTA del path Mongo de api/routers/manager/jobs.py::get_jobs_history."""
    docs = list(
        get_mongo_client_read()["Manager"]["JobRuns"]
        .find({}, {"_id": 0})
        .sort("started_at", -1)
        .limit(limit)
    )
    for d in docs:
        for k in ("started_at", "finished_at"):
            d[k] = _fmt_ar(d.get(k))
    return docs


def _ident(d: dict) -> tuple:
    """Identidad estable de un run: (tipo, started_at AR, status)."""
    return (d.get("tipo"), d.get("started_at"), d.get("status"))


def compare_history() -> bool:
    from api.services import manager_infra_sql

    mg = _mongo_history(_HISTORY_LIMIT)
    sq = manager_infra_sql.jobs_history_sql(limit=_HISTORY_LIMIT)

    mg_ids = {_ident(d) for d in mg}
    sq_ids = {_ident(d) for d in sq}
    solo_mg = mg_ids - sq_ids
    solo_sq = sq_ids - mg_ids
    comun = mg_ids & sq_ids

    print(f"\n── /jobs/history (últimos {_HISTORY_LIMIT}) ──")
    print(f"  Mongo: {len(mg):>4} runs   SQL: {len(sq):>4} runs   común: {len(comun)}")
    print(f"  sólo-Mongo (SQL atrasado, tolerable si son recientes): {len(solo_mg)}")
    print(f"  sólo-SQL  (NO debería existir): {len(solo_sq)}")
    for k in sorted(solo_sq)[:5]:
        print(f"     ⚠ sólo-SQL: {k}")
    for k in sorted(solo_mg, reverse=True)[:5]:
        print(f"     · sólo-Mongo: {k}")

    # Campos estables de los runs comunes (status ya está en la identidad).
    mg_by = {_ident(d): d for d in mg}
    sq_by = {_ident(d): d for d in sq}
    drift = 0
    for k in comun:
        for campo in ("tipo", "started_at", "finished_at", "status"):
            if mg_by[k].get(campo) != sq_by[k].get(campo):
                drift += 1
                if drift <= 5:
                    print(f"     ✗ drift {campo} en {k}: "
                          f"mg={mg_by[k].get(campo)!r} sq={sq_by[k].get(campo)!r}")
    ok = not solo_sq and drift == 0
    print(f"  → {'OK' if ok else 'REVISAR'} (sólo-SQL={len(solo_sq)}, drift campos={drift})")
    return ok


# ── 2. /jobs/history/stats ───────────────────────────────────────────────────
def _mongo_stats(desde: datetime) -> list[dict]:
    pipeline = [
        {"$match": {"started_at": {"$gte": desde}}},
        {"$group": {
            "_id": "$tipo",
            "total":   {"$sum": 1},
            "ok":      {"$sum": {"$cond": [{"$eq": ["$status", "ok"]},      1, 0]}},
            "partial": {"$sum": {"$cond": [{"$eq": ["$status", "partial"]}, 1, 0]}},
            "error":   {"$sum": {"$cond": [{"$eq": ["$status", "error"]},   1, 0]}},
        }},
        {"$sort": {"_id": 1}},
    ]
    rows = list(get_mongo_client_read()["Manager"]["JobRuns"].aggregate(pipeline))
    for r in rows:
        r["tipo"] = r.pop("_id")
    return rows


def compare_stats() -> bool:
    from api.services import manager_infra_sql

    desde = datetime.now(UTC) - timedelta(days=_STATS_DESDE_DIAS)
    mg = {r["tipo"]: r for r in _mongo_stats(desde)}
    sq = {r["tipo"]: r for r in manager_infra_sql.jobs_history_stats_sql(desde=desde)}

    print(f"\n── /jobs/history/stats (últimos {_STATS_DESDE_DIAS}d) ──")
    tipos = sorted(set(mg) | set(sq))
    diffs = 0
    print(f"  {'tipo':<22} {'mongo (t/ok/p/e)':<22} {'sql (t/ok/p/e)':<22}")
    for t in tipos:
        m, s = mg.get(t, {}), sq.get(t, {})
        mt = (m.get("total", 0), m.get("ok", 0), m.get("partial", 0), m.get("error", 0))
        st = (s.get("total", 0), s.get("ok", 0), s.get("partial", 0), s.get("error", 0))
        flag = "" if mt == st else "  ✗"
        if mt != st:
            diffs += 1
        print(f"  {t:<22} {mt!s:<22} {st!s:<22}{flag}")
    ok = diffs == 0
    print(f"  → {'OK' if ok else f'REVISAR ({diffs} tipos divergen)'}")
    return ok


# ── 3. /roles/audit ──────────────────────────────────────────────────────────
def compare_audit() -> bool:
    from api.services import manager_infra_sql

    mg = list_audit_mongo(_AUDIT_LIMIT)
    sq = manager_infra_sql.list_audit_sql(_AUDIT_LIMIT)

    def _akey(d: dict) -> tuple:
        return (str(d.get("ts")), d.get("email") or d.get("target") or d.get("actor"),
                d.get("action") or d.get("accion"))

    mg_ids = {_akey(d) for d in mg}
    sq_ids = {_akey(d) for d in sq}
    print(f"\n── /roles/audit (últimos {_AUDIT_LIMIT}) ──")
    print(f"  Mongo: {len(mg):>4}   SQL: {len(sq):>4}   común: {len(mg_ids & sq_ids)}")
    solo_sq = sq_ids - mg_ids
    print(f"  sólo-SQL (NO debería existir): {len(solo_sq)}")
    for k in list(solo_sq)[:5]:
        print(f"     ⚠ sólo-SQL: {k}")
    ok = not solo_sq
    print(f"  → {'OK' if ok else 'REVISAR'}")
    return ok


def main() -> None:
    print(f"=== Manager infra: SQL vs Mongo ({datetime.now(UTC):%Y-%m-%d %H:%M} UTC) ===")
    print("Gate de Fase 4 (MANAGER_SQL / AUTH_SQL). SQL lo refresca sync_postgres + "
          "dual-write MANAGER_SQL_WRITE.")
    r1 = compare_history()
    r2 = compare_stats()
    r3 = compare_audit()
    print("\n=== VEREDICTO ===")
    if r1 and r2 and r3:
        print("✓ PARIDAD OK — se puede prender MANAGER_SQL=1 (job history/audit) con red de")
        print("  fallback. Para AUTH_SQL validar aparte roles/matriz/grupos (lecturas con")
        print("  fallback Mongo ya en core/roles.py + core/grupos.py).")
    else:
        print("✗ HAY DIVERGENCIAS — revisar arriba antes de flipear lecturas a SQL.")
        print("  (filas sólo-Mongo recientes por desfasaje de sync NO bloquean; sólo-SQL")
        print("   o drift de campos SÍ.)")


if __name__ == "__main__":
    main()
