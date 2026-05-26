"""db_maintenance.py — cambios de optimización DBA. Idempotente, dry-run por default.

De la auditoría (scripts/audit_db + diag_index_usage):

FASE 1 — índices
  • CREATE PortfolioAPI.AumAPI (fecha desc, id_cuenta) — 248k docs SIN índice propio
    → COLLSCAN en cada vista de AuM. (api_migrate.migrate_aum lo recrea en cada sync;
    esto lo aplica YA sin rebuildear.)
  • DROP redundantes (cubiertos por un compuesto):
      - Manager.AumBackfillLog.run_id_1   (⊂ run_id_1_fecha_snapshot_1_id_cuenta_1)
      - Market.EconomicCalendar.time_1    (⊂ time_1_country_1_event_1)

FASE 2 — retención (TTL), 90 días
  • Logs/auditorías que crecen sin límite (AumBackfillLog, OrdenesAudit, RoleAudit,
    AsistenteLogs, ChangeLog, PortfolioSnapshotLog, *Audit).
  • Time-series de mercado: Opciones.Data y Trading.TimeSales (el uso vivo de
    TimeSales es ≤20 días — get_historico_trades 15d, liquidez 20d; el dato diario
    ya vive en SnapshotsCierre). 90d = 4.5× el uso real.

El TTL detecta automáticamente: time-series → collMod expireAfterSeconds; colección
normal → TTL index sobre el ÚNICO campo datetime (si es ambiguo, reporta y saltea —
no adivina).

    python -m scripts.db_maintenance            # dry-run (no toca nada)
    python -m scripts.db_maintenance --apply     # ejecuta
    python -m scripts.db_maintenance --solo ttl  # solo una fase (indices|ttl)
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime

from core.mongo import get_mongo_client

_CREATE = [
    ("PortfolioAPI", "AumAPI", [("fecha", -1), ("id_cuenta", 1)], "fecha_idcuenta"),
]
_DROP = [
    ("Manager", "AumBackfillLog", "run_id_1"),
    ("Market", "EconomicCalendar", "time_1"),
]
# (db, coll, días de retención)
_TTL = [
    ("Manager", "AumBackfillLog", 90),
    ("Manager", "AsistenteLogs", 90),
    ("Manager", "RoleAudit", 90),
    ("Manager", "ChangeLog", 90),
    ("Manager", "PortfolioSnapshotLog", 90),
    ("Operaciones", "OrdenesAudit", 90),
    ("Derivados", "AgroPizarraAudit", 90),
    ("Derivados", "CamaraCerealesAudit", 90),
    ("Opciones", "Data", 90),       # time-series
    ("Trading", "TimeSales", 90),   # time-series
]


def _ts_info(db, coll: str) -> tuple[bool, int | None]:
    """(es_timeseries, expireAfterSeconds_actual)."""
    for c in db.list_collections(filter={"name": coll}):
        opts = c.get("options", {})
        if "timeseries" in opts:
            return True, opts.get("expireAfterSeconds")
    return False, None


def _campos_fecha(coll_obj, n: int = 60) -> list[str]:
    """Campos top-level datetime presentes en ≥80% del sample."""
    cnt: dict[str, int] = defaultdict(int)
    total = 0
    for d in coll_obj.aggregate([{"$sample": {"size": n}}]):
        total += 1
        for k, v in d.items():
            if k != "_id" and isinstance(v, datetime):
                cnt[k] += 1
    if not total:
        return []
    return [k for k, c in cnt.items() if c >= 0.8 * total]


def _fase_indices(cli, dry: bool) -> None:
    print("=" * 66)
    print("FASE 1 — índices")
    print("=" * 66)
    for dbn, coll, keys, name in _CREATE:
        c = cli[dbn][coll]
        if name in c.index_information():
            print(f"  ✓ {dbn}.{coll} [{name}] ya existe — skip")
            continue
        ks = ", ".join(f"{k}:{d}" for k, d in keys)
        if dry:
            print(f"  [dry] CREATE {dbn}.{coll} ({ks}) name={name}")
        else:
            c.create_index(keys, name=name)
            print(f"  ✔ CREADO {dbn}.{coll} ({ks})")
    for dbn, coll, name in _DROP:
        c = cli[dbn][coll]
        info = c.index_information()
        if name not in info:
            print(f"  ✓ {dbn}.{coll} [{name}] no existe — skip")
            continue
        if dry:
            print(f"  [dry] DROP {dbn}.{coll} [{name}]  (key={info[name]['key']})")
        else:
            c.drop_index(name)
            print(f"  ✔ DROPEADO {dbn}.{coll} [{name}]")


def _fase_ttl(cli, dry: bool) -> None:
    print("\n" + "=" * 66)
    print("FASE 2 — retención (TTL)")
    print("=" * 66)
    for dbn, coll, dias in _TTL:
        db = cli[dbn]
        c = db[coll]
        secs = dias * 86400
        es_ts, actual = _ts_info(db, coll)

        if es_ts:
            if actual == secs:
                print(f"  ✓ {dbn}.{coll} (TS) ya tiene TTL {dias}d — skip")
                continue
            if dry:
                print(f"  [dry] collMod {dbn}.{coll} (time-series) expireAfterSeconds={secs} ({dias}d)")
            else:
                try:
                    db.command("collMod", coll, expireAfterSeconds=secs)
                    print(f"  ✔ TTL {dias}d en {dbn}.{coll} (time-series)")
                except Exception as e:
                    print(f"  ✗ {dbn}.{coll}: collMod falló ({str(e)[:50]}) — hacelo en Atlas UI")
            continue

        # Colección normal: TTL index sobre el único campo datetime.
        if any("expireAfterSeconds" in s for s in c.index_information().values()):
            print(f"  ✓ {dbn}.{coll} ya tiene un TTL index — skip")
            continue
        campos = _campos_fecha(c)
        if len(campos) != 1:
            print(f"  ⚠ {dbn}.{coll}: campo de fecha {'ambiguo' if campos else 'no detectado'} "
                  f"{campos or ''} — REVISAR a mano, no aplico TTL")
            continue
        campo = campos[0]
        if dry:
            print(f"  [dry] CREATE TTL {dbn}.{coll} sobre '{campo}' expireAfterSeconds={secs} ({dias}d)")
        else:
            c.create_index([(campo, 1)], expireAfterSeconds=secs, name=f"ttl_{campo}")
            print(f"  ✔ TTL {dias}d en {dbn}.{coll} sobre '{campo}'")


def main() -> None:
    ap = argparse.ArgumentParser(description="Mantenimiento DBA (índices + TTL)")
    ap.add_argument("--apply", action="store_true", help="ejecutar (default: dry-run)")
    ap.add_argument("--solo", choices=["indices", "ttl"], help="correr solo una fase")
    args = ap.parse_args()
    dry = not args.apply
    cli = get_mongo_client()

    if args.solo in (None, "indices"):
        _fase_indices(cli, dry)
    if args.solo in (None, "ttl"):
        _fase_ttl(cli, dry)

    print("\n(DRY-RUN — nada se modificó; correr con --apply.)" if dry
          else "\nListo. Verificá con: python -m scripts.audit_db")


if __name__ == "__main__":
    main()
