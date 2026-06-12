"""scripts/diag_tenencia_hd_fecha.py — valida la Tenencia Valorizada HD de un día.

Read-only. Para reconciliar contra el sistema contable:
  1. Dumpea lo CONGELADO en Valuaciones.TenenciaHD para la fecha (lo que muestra
     la página): total, TC, AuM por cuenta y CADA posición (unidad × cuenta).
  2. RECALCULA la tenencia con el AuM ACTUAL de esa fecha (lo que el job
     produciría hoy) — misma lógica (_doc_del_dia).
  3. DIFF: si congelado ≠ recalculado, el AuM se corrigió DESPUÉS del freeze →
     la página muestra valor viejo. Ahí está la causa del descalce.

Uso:
    python -m scripts.diag_tenencia_hd_fecha               # 2026-05-29
    python -m scripts.diag_tenencia_hd_fecha 2026-05-29
"""
from __future__ import annotations

import sys
from datetime import UTC, datetime

from core.mongo import get_mongo_client_read
from jobs.tenencia_hd import CUENTAS, _doc_del_dia, _hd_unidades


def _posmap(posiciones: list[dict]) -> dict[str, dict]:
    return {p["unidad"]: p for p in posiciones}


def _print_doc(titulo: str, doc: dict) -> None:
    print(f"\n===== {titulo} =====")
    if not doc:
        print("  (no existe doc para esa fecha)")
        return
    aum = doc.get("aum", {})
    print(f"  TC (MEP del día): {doc.get('tc')}")
    print(f"  TOTAL: {doc.get('total', 0):,.2f}")
    print("  AuM por cuenta: " + "  ".join(f"{c}={aum.get(c, 0):,.2f}" for c in CUENTAS))
    pos = doc.get("posiciones", [])
    print(f"  posiciones: {len(pos)}")


def main() -> int:
    fecha = sys.argv[1] if len(sys.argv) > 1 else "2026-05-29"
    client = get_mongo_client_read()

    frozen = client["Valuaciones"]["TenenciaHD"].find_one({"fecha_snapshot": fecha}) or {}
    hd = _hd_unidades(client)
    recalc = _doc_del_dia(client["Valuaciones"]["AuM"], fecha, hd, datetime.now(UTC))

    print(f"FECHA: {fecha}   |   unidades CARTERA=HD en Assets: {len(hd)}")
    _print_doc("CONGELADO (lo que ve la página)", frozen)
    _print_doc("RECALCULADO (AuM actual de esa fecha)", recalc)

    fmap, rmap = _posmap(frozen.get("posiciones", [])), _posmap(recalc.get("posiciones", []))
    todas = sorted(set(fmap) | set(rmap))

    print("\n===== DIFERENCIAS por unidad (congelado vs recalculado) =====")
    print(f"  {'UNIDAD':<40} {'CONGELADO':>16} {'RECALC':>16} {'DIFF':>16}")
    hubo = False
    for u in todas:
        fv = float(fmap.get(u, {}).get("total", 0) or 0)
        rv = float(rmap.get(u, {}).get("total", 0) or 0)
        diff = rv - fv
        if abs(diff) > 0.5:  # tolerancia de redondeo
            hubo = True
            marca = "  ← solo congelado" if u not in rmap else ("  ← solo recalc" if u not in fmap else "")
            print(f"  {u[:40]:<40} {fv:>16,.2f} {rv:>16,.2f} {diff:>16,.2f}{marca}")
    if not hubo:
        print("  (sin diferencias > 0.5 — el congelado coincide con el AuM actual)")

    tf = float(frozen.get("total", 0) or 0)
    tr = float(recalc.get("total", 0) or 0)
    print(f"\n  TOTAL congelado: {tf:,.2f}   recalculado: {tr:,.2f}   diff: {tr - tf:,.2f}")
    print("\nNOTA: si hay DIFF, la página muestra el valor congelado (viejo) y el AuM")
    print("ya fue corregido → para reflejarlo habría que re-freezear ese día. Si NO hay")
    print("diff y igual no matchea tu contable, el descalce está en el AuM mismo (precio")
    print("o cantidad de alguna unidad) — comparás esas filas contra tu sistema.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
