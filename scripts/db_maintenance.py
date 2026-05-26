"""db_maintenance.py — aplica cambios de optimización DBA (índices). Idempotente.

Fase 1 de la auditoría (scripts/audit_db + diag_index_usage):
  • CREATE PortfolioAPI.AumAPI (fecha desc, id_cuenta) — hoy 248k docs SIN índice
    propio → COLLSCAN en cada vista de AuM. (El fix de api_migrate.migrate_aum lo
    recrea en cada sync; esto lo aplica YA sin rebuildear.)
  • DROP índices redundantes detectados por el audit (cubiertos por un compuesto):
      - Manager.AumBackfillLog.run_id_1   (⊂ run_id_1_fecha_snapshot_1_id_cuenta_1)
      - Market.EconomicCalendar.time_1    (⊂ time_1_country_1_event_1)

DRY-RUN por default: solo imprime qué haría. Con --apply ejecuta. Cada acción
verifica el estado actual (no falla si ya está hecho).

    python -m scripts.db_maintenance            # dry-run (no toca nada)
    python -m scripts.db_maintenance --apply     # ejecuta

NO toca datos — solo índices. Read del estado con el cliente RW (create/dropIndex
requieren escritura; $indexStats NO se usa acá, va por permisos).
"""
from __future__ import annotations

import argparse

from core.mongo import get_mongo_client

# Acciones declarativas. (db, coll, tipo, ...).
_CREATE = [
    # (db, coll, keys, name)
    ("PortfolioAPI", "AumAPI", [("fecha", -1), ("id_cuenta", 1)], "fecha_idcuenta"),
]
_DROP = [
    # (db, coll, index_name)
    ("Manager", "AumBackfillLog", "run_id_1"),
    ("Market", "EconomicCalendar", "time_1"),
]


def main() -> None:
    ap = argparse.ArgumentParser(description="Mantenimiento de índices (Fase 1 DBA)")
    ap.add_argument("--apply", action="store_true", help="ejecutar (default: dry-run)")
    args = ap.parse_args()
    dry = not args.apply
    cli = get_mongo_client()

    print("=" * 64)
    print("CREATE índices faltantes")
    print("=" * 64)
    for dbn, coll, keys, name in _CREATE:
        c = cli[dbn][coll]
        existe = name in c.index_information()
        keystr = ", ".join(f"{k}:{d}" for k, d in keys)
        if existe:
            print(f"  ✓ {dbn}.{coll} [{name}] ya existe — skip")
            continue
        if dry:
            print(f"  [dry] CREATE {dbn}.{coll} ({keystr}) name={name}")
        else:
            c.create_index(keys, name=name)
            print(f"  ✔ CREADO {dbn}.{coll} ({keystr}) name={name}")

    print("\n" + "=" * 64)
    print("DROP índices redundantes")
    print("=" * 64)
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

    if dry:
        print("\n(DRY-RUN — nada se modificó. Correr con --apply para ejecutar.)")
    else:
        print("\nListo. Verificá con: python -m scripts.audit_db")


if __name__ == "__main__":
    main()
