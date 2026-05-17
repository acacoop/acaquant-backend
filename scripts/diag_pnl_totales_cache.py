"""diag_pnl_totales_cache.py — valida el refactor de PNL TOTALES.

Confirma que `pnl_todas_cuentas` dejó de calcular en vivo y ahora sirve
desde `Valuaciones.PnLTotalesCache`. Imprime tres pruebas:

  1. La colección está poblada — cuántas cuentas/filas y CUÁNDO se
     calculó (`computed_at`). Si no cambia entre requests, es precalculado.
  2. Velocidad: el endpoint (lee la colección) vs el cálculo pesado
     completo — lo que el endpoint hacía ANTES en CADA request.
  3. Fidelidad: el precompute vs un cálculo fresco. Δ≈0 = fiel; Δ grande
     en rueda = precios se movieron desde la última corrida del cron.

Corre (en el Droplet — tarda ~1 min porque hace el cálculo completo para
comparar):  python -m scripts.diag_pnl_totales_cache
"""
from __future__ import annotations

import time

from api.services.pnl import pnl_todas_cuentas, pnl_todas_cuentas_compute
from core.mongo import get_mongo_client_read


def main() -> None:
    col = get_mongo_client_read()["Valuaciones"]["PnLTotalesCache"]

    # ── 1. Estado de la colección ──────────────────────────────────────
    print("=" * 66)
    print("1. Valuaciones.PnLTotalesCache (lo que llena el cron)")
    print("=" * 66)
    n_docs = col.count_documents({})
    if n_docs == 0:
        print("  ⚠ VACÍA — falta correr `python -m jobs.pnl_totales_precompute`.")
        print("    El endpoint devuelve rows: [] hasta que el job la llene.")
        return
    n_filas = 0
    computed = None
    for d in col.find({}, {"_id": 0, "rows": 1, "computed_at": 1}):
        n_filas += len(d.get("rows") or [])
        c = d.get("computed_at")
        if c and (computed is None or c > computed):
            computed = c
    print(f"  cuentas (docs): {n_docs}")
    print(f"  filas totales:  {n_filas}")
    print(f"  computed_at:    {computed}")
    print("  → si ese timestamp NO cambia al refrescar la web, el dato es")
    print("    precalculado (no se recalcula por request). Cambia solo")
    print("    cuando corre el cron.")

    # ── 2. Velocidad: endpoint vs cálculo completo ─────────────────────
    print("\n" + "=" * 66)
    print("2. Velocidad — endpoint (lee) vs cálculo completo (lo de antes)")
    print("=" * 66)
    t0 = time.perf_counter()
    r_lee = pnl_todas_cuentas(filtro_cuenta="todas")
    t_lee = time.perf_counter() - t0
    print(f"  pnl_todas_cuentas('todas')   {len(r_lee.get('rows', []))} filas"
          f"   en  {t_lee:.2f}s   ← lo que hace el endpoint AHORA")

    t0 = time.perf_counter()
    compute = pnl_todas_cuentas_compute()
    t_comp = time.perf_counter() - t0
    n_filas_comp = sum(len(c.get("rows") or []) for c in compute)
    print(f"  pnl_todas_cuentas_compute()  {n_filas_comp} filas crudas"
          f"   en  {t_comp:.2f}s   ← lo que tardaba ANTES en CADA request")
    print(f"\n  → el endpoint es ~{t_comp / max(t_lee, 0.001):.0f}× más rápido. "
          f"El cálculo pesado")
    print("    salió del request HTTP y lo hace el cron de fondo. Esa es la mejora.")

    # ── 3. Fidelidad: precompute vs cálculo fresco ─────────────────────
    print("\n" + "=" * 66)
    print("3. Fidelidad — ¿el precompute coincide con un cálculo fresco?")
    print("=" * 66)
    tot_lee = r_lee.get("totales", {})
    agg = {"pnl_total": 0.0, "valor_actual": 0.0}
    for c in compute:
        t = c.get("totales", {}) or {}
        agg["pnl_total"]    += float(t.get("pnl_total") or 0)
        agg["valor_actual"] += float(t.get("valor_actual") or 0)
    for k in ("pnl_total", "valor_actual"):
        v_col = float(tot_lee.get(k) or 0)
        v_fresh = agg[k]
        print(f"  {k:14}  colección={v_col:>18,.0f}   fresco={v_fresh:>18,.0f}"
              f"   Δ={v_fresh - v_col:+,.0f}")
    print("\n  Δ≈0 → el precompute es fiel. Δ aparece en rueda cuando los")
    print("  precios se movieron entre la corrida del cron y ahora (esperable).")


if __name__ == "__main__":
    main()
