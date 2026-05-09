"""Diagnóstico in-process de pnl_todas_cuentas.

Corre `pnl_todas_cuentas("todas")` directo en el proceso (sin pasar por
HTTP/Cloudflare/Vercel), midiendo:
  - Tiempo de cada step de _load_pnl_bulk_deps (Assets / PortfolioSnapshot /
    SnapshotsCierre).
  - Tiempo total + por cuenta del loop principal.
  - Si una cuenta tira excepción, capta y reporta cuál.

Uso (desde la raíz del repo, con venv activado):
    python -m scripts.diag_pnl_internal

Sirve para localizar EXACTAMENTE qué está rompiendo /pnl-todas cuando el
endpoint devuelve 502/504 sin mensaje útil. Va al log/stdout, no al cliente.
"""
from __future__ import annotations

import time
import traceback
from typing import Any

from api.db import get_db_cashflow, get_db_trading, get_db_valuaciones
from api.services.pnl import (
    _build_unidad_maps,
    _load_pnl_bulk_deps,
    _pnl_por_cuenta_core,
)
from api.services.portfolio import listar_cuentas


def _t(label: str, fn) -> tuple[Any, float]:
    t0 = time.time()
    try:
        out = fn()
    except Exception:
        dt = time.time() - t0
        print(f"  [FAIL] {label} en {dt:.2f}s")
        traceback.print_exc()
        return None, dt
    dt = time.time() - t0
    print(f"  [OK  ] {label} en {dt:.2f}s")
    return out, dt


def main():
    print("=== diag_pnl_internal ===")
    db_cf = get_db_cashflow()
    db_v  = get_db_valuaciones()
    db_t  = get_db_trading()

    print("\n[1] _build_unidad_maps()")
    res, _ = _t("_build_unidad_maps", lambda: _build_unidad_maps())
    if res is None:
        return
    unidad_to_match, match_to_display = res
    print(f"  unidad_to_match: {len(unidad_to_match)} entries")
    print(f"  match_to_display: {len(match_to_display)} entries")

    print("\n[2] _load_pnl_bulk_deps(db_v, db_t)")
    deps, _ = _t("_load_pnl_bulk_deps", lambda: _load_pnl_bulk_deps(db_v, db_t))
    if deps is None:
        return
    for k in (
        "instrumentos_by_unidad",
        "portfolio_snap_by_ticker",
        "snapshots_cierre_by_ticker",
    ):
        v = deps.get(k) or {}
        print(f"  {k}: {len(v)} entries")

    print("\n[3] listar_cuentas()")
    cuentas, _ = _t("listar_cuentas", lambda: listar_cuentas())
    if not cuentas:
        print("  sin cuentas — abortando")
        return
    print(f"  {len(cuentas)} cuentas")

    print("\n[4] _pnl_por_cuenta_core por cuenta")
    n_ok = 0
    n_err = 0
    slow: list[tuple[float, str]] = []
    err_samples: list[tuple[str, str]] = []
    t_total_start = time.time()
    for c in cuentas:
        id_cta = c.get("id_cuenta")
        if not id_cta:
            continue
        t0 = time.time()
        try:
            r = _pnl_por_cuenta_core(
                id_cuenta=str(id_cta),
                db_cf=db_cf, db_v=db_v, db_t=db_t,
                **deps,
            )
            n_ok += 1
            dt = time.time() - t0
            slow.append((dt, str(id_cta)))
            n_filas = len(r.get("rows", []))
            if dt > 0.5:
                print(f"  [{id_cta}] {dt:.2f}s · {n_filas} filas (slow)")
        except Exception as e:
            n_err += 1
            err_samples.append((str(id_cta), repr(e)))
            print(f"  [{id_cta}] EXCEPTION: {e!r}")
            if n_err <= 3:
                traceback.print_exc()
    t_total = time.time() - t_total_start

    slow.sort(reverse=True)
    print(f"\n[5] resumen")
    print(f"  total {t_total:.2f}s · ok={n_ok} err={n_err}")
    print(f"  top-5 cuentas más lentas:")
    for dt, cta in slow[:5]:
        print(f"    [{cta}] {dt:.2f}s")
    if err_samples:
        print(f"  top-5 errores:")
        for cta, e in err_samples[:5]:
            print(f"    [{cta}] {e}")


if __name__ == "__main__":
    main()
