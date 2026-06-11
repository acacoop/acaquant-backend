"""diag_tenencia_hd.py — READ-ONLY. Verifica la base para la vista "Tenencia Valorizada".

Antes de diseñar la vista/rollup confirmamos contra prod (REGLA #2):
  1) ¿"HD" es un valor de CARTERA en Valuaciones.Assets? ¿cuántas `unidad` tiene?
     (muestra TODAS las carteras para ver cómo se escribe HD exactamente).
  2) ¿Valuaciones.AuM tiene snapshots diarios para las cuentas 100/255/256 desde
     abril 2026? (min/max fecha + nº de días con foto en el rango).
  3) Foto del último snapshot: AuM HD por cuenta + cuántas posiciones HD tiene cada una.

READ-ONLY. Scopeado a 3 cuentas (índice id_cuenta) + distinct de fecha (índice).

Uso:
    python -m scripts.diag_tenencia_hd
    python -m scripts.diag_tenencia_hd --cartera HD --desde 2026-04-01
"""
from __future__ import annotations

import argparse

from core.mongo import get_mongo_client_read

_CUENTAS = ["100", "255", "256"]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cartera", default="HD", help="valor de CARTERA a filtrar (default HD)")
    ap.add_argument("--desde", default="2026-04-01", help="inicio del backfill a chequear")
    args = ap.parse_args()

    cli = get_mongo_client_read()
    assets = cli["Valuaciones"]["Assets"]
    aum = cli["Valuaciones"]["AuM"]

    # ── 1) CARTERAS — ver cómo se escribe HD ───────────────────────────────────
    print("=== Tenencia Valorizada — verificación de base ===\n")
    print("── 1) CARTERAS en Assets (para confirmar 'HD') ──")
    carteras = sorted(c for c in assets.distinct("CARTERA") if c)
    for c in carteras:
        n = assets.count_documents({"CARTERA": c})
        marca = "  ← ESTA" if c.strip().upper() == args.cartera.strip().upper() else ""
        print(f"    {c:<28} {n:>5} unidades{marca}")
    hd_unidades = {d["unidad"] for d in assets.find(
        {"CARTERA": args.cartera}, {"_id": 0, "unidad": 1}) if d.get("unidad")}
    print(f"\n    → CARTERA={args.cartera!r}: {len(hd_unidades)} unidades\n")
    if not hd_unidades:
        print("    ⚠️ No hay unidades con esa CARTERA — confirmá cómo se escribe (mirá la lista de arriba).\n")

    # ── 2) Cobertura de AuM por cuenta (desde abril) ───────────────────────────
    print(f"── 2) Cobertura AuM cuentas {_CUENTAS} (desde {args.desde}) ──")
    print(f"    {'cuenta':<8}{'min fecha':<14}{'max fecha':<14}{'días ≥ desde':>14}")
    for c in _CUENTAS:
        fechas = aum.distinct("fecha_snapshot", {"id_cuenta": c})
        fechas = sorted(str(f) for f in fechas if f)
        if not fechas:
            print(f"    {c:<8}{'—':<14}{'—':<14}{0:>14}")
            continue
        en_rango = [f for f in fechas if f >= args.desde]
        print(f"    {c:<8}{fechas[0]:<14}{fechas[-1]:<14}{len(en_rango):>14}")
    print()

    # ── 3) Foto del último snapshot: AuM HD por cuenta + nº posiciones ─────────
    snap = aum.find_one({}, {"_id": 0, "fecha_snapshot": 1}, sort=[("fecha_snapshot", -1)])
    fecha = snap["fecha_snapshot"] if snap else None
    print(f"── 3) Último snapshot {fecha} — solo CARTERA {args.cartera} ──")
    print(f"    {'cuenta':<8}{'AuM HD':>18}{'posiciones HD':>16}")
    total = 0.0
    for c in _CUENTAS:
        rows = list(aum.aggregate([
            {"$match": {"fecha_snapshot": fecha, "id_cuenta": c,
                        "unidad": {"$in": list(hd_unidades)}}},
            {"$group": {"_id": None, "aum": {"$sum": "$valuacion"}, "n": {"$sum": 1}}},
        ]))
        a = float(rows[0]["aum"]) if rows else 0.0
        n = rows[0]["n"] if rows else 0
        total += a
        print(f"    {c:<8}{a:>18,.0f}{n:>16}")
    print(f"    {'TOTAL':<8}{total:>18,.0f}")
    print()

    print("=== Lectura ===")
    print("  - Paso 1 confirma el string exacto de la CARTERA HD (y cuántos títulos abarca).")
    print("  - Paso 2 dice si el backfill abril→hoy es posible (si AuM tiene esos días).")
    print("  - Paso 3 muestra números reales por cuenta — sanity de que las 3 tienen tenencia HD.")


if __name__ == "__main__":
    main()
