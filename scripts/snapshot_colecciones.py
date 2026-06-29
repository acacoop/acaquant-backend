"""scripts/snapshot_colecciones.py — baseline de colecciones para el ciclo restore→migrar→re-dropear.

Plan: vas a restaurar un backup COMPLETO sobre la misma base (vuelven TODAS las
colecciones ya dropeadas), migrás lo que falta (ej. el cupo, con backfill_cupo_sql),
y después tenés que volver al estado actual borrando lo que resucitó. Este script
hace ese ida y vuelta SIN adivinar:

  1) ANTES del restore:  python -m scripts.snapshot_colecciones --save
       Guarda el inventario ACTUAL (db → [colecciones]) en colecciones_baseline.json.
       Eso es EXACTAMENTE lo que hay que conservar.

  2) (sin args):         python -m scripts.snapshot_colecciones
       Muestra el inventario actual (no toca nada, no guarda).

  3) DESPUÉS del restore + migración:
       python -m scripts.snapshot_colecciones --diff           # lista lo que SOBRA (resucitó)
       python -m scripts.snapshot_colecciones --diff --apply   # las DROPEA → vuelve al baseline

Las del baseline NUNCA se tocan. Solo se dropea lo que está ahora y NO estaba en el
baseline. Idempotente. Salta bases de sistema (admin/local/config). REGLA #0/#4/#5.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

BASELINE = Path(__file__).resolve().parent.parent / "colecciones_baseline.json"
SYS_DBS = {"admin", "local", "config"}


def _inventario(cli) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for db in sorted(cli.list_database_names()):
        if db in SYS_DBS:
            continue
        out[db] = sorted(cli[db].list_collection_names())
    return out


def _print_inv(inv: dict[str, list[str]]) -> None:
    total = sum(len(v) for v in inv.values())
    print(f"{len(inv)} bases, {total} colecciones:")
    for db, colls in inv.items():
        print(f"  {db} ({len(colls)}): {', '.join(colls) or '—'}")


def main() -> int:
    from core.mongo import get_mongo_client

    cli = get_mongo_client()

    if "--save" in sys.argv:
        inv = _inventario(cli)
        BASELINE.write_text(json.dumps(inv, indent=2, ensure_ascii=False), encoding="utf-8")
        _print_inv(inv)
        print(f"\n✅ Baseline guardado en {BASELINE}  ({sum(len(v) for v in inv.values())} colecciones a CONSERVAR).")
        return 0

    if "--diff" in sys.argv:
        if not BASELINE.exists():
            print(f"❌ No existe {BASELINE}. Corré primero --save ANTES del restore.")
            return 1
        baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
        actual = _inventario(cli)
        # Sobrantes = (db, coll) presentes AHORA y NO en el baseline.
        sobran: list[tuple[str, str]] = []
        for db, colls in actual.items():
            keep = set(baseline.get(db, []))
            for c in colls:
                if c not in keep:
                    sobran.append((db, c))

        apply = "--apply" in sys.argv
        modo = "APPLY (DROP REAL)" if apply else "DRY-RUN"
        print(f"snapshot_colecciones --diff — modo {modo}")
        print(f"Baseline: {sum(len(v) for v in baseline.values())} colecciones · "
              f"Ahora: {sum(len(v) for v in actual.values())} · Sobran: {len(sobran)}\n")
        if not sobran:
            print("Nada sobra — ya estás en el estado del baseline.")
            return 0
        for db, c in sobran:
            try:
                n = cli[db][c].estimated_document_count()
            except Exception:
                n = -1
            if apply:
                cli[db].drop_collection(c)
                print(f"  🗑  {db}.{c:<26} DROPEADA (~{n:,} docs)")
            else:
                print(f"  •  {db}.{c:<26} se dropearía (~{n:,} docs)")
        print("\nListo." if apply else "\nDRY-RUN. Re-correr con --apply para volver al baseline.")
        return 0

    # Sin args: solo mostrar.
    _print_inv(_inventario(cli))
    print("\n(solo lectura) — --save para fijar baseline · --diff para comparar tras el restore.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
