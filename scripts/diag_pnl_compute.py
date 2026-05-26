"""diag_pnl_compute.py — desglosa los ~27s de pnl_todas_cuentas_compute.

Separa el costo en fases y cuenta las llamadas a get_mep_for_date, para decidir
el fix con datos (no a ojo):

  • PRELOAD  → _load_pnl_bulk_deps (las ~6 queries bulk, incl. scan de boletos).
  • LOOP     → recorrer las N cuentas con _pnl_por_cuenta_core (todo en RAM salvo
               el fallback get_mep_for_date).
  • MEP      → cuántas veces se llama get_mep_for_date y cuánto suman. Si es alto,
               el fix es compartir el cache entre cuentas (hoy es local por cuenta
               → la misma fecha histórica se re-busca N veces).

Replica la estructura de pnl_todas_cuentas_compute con timing; no persiste nada.

    python -m scripts.diag_pnl_compute          # todas las cuentas (~27s)
    python -m scripts.diag_pnl_compute --n 100  # primeras 100 cuentas (rápido)
"""
from __future__ import annotations

import argparse
import time
from datetime import date

import api.services.pnl as pnl
from api.services.portfolio import listar_cuentas


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=0, help="limitar a las primeras N cuentas (0 = todas)")
    args = ap.parse_args()

    # Instrumentar get_mep_for_date: contar llamadas + tiempo (en el namespace
    # de pnl, que es donde lo resuelve _pesificar/_usdificar).
    stat = {"n": 0, "t": 0.0}
    orig = pnl.get_mep_for_date

    def wrapped(*a, **k):
        t0 = time.perf_counter()
        try:
            return orig(*a, **k)
        finally:
            stat["t"] += time.perf_counter() - t0
            stat["n"] += 1

    pnl.get_mep_for_date = wrapped
    try:
        cuentas = listar_cuentas()
        if args.n:
            cuentas = cuentas[: args.n]

        db_cf = pnl.get_db_cashflow()
        db_v = pnl.get_db_valuaciones()
        db_t = pnl.get_db_trading()

        t0 = time.perf_counter()
        deps = pnl._load_pnl_bulk_deps(db_v, db_cf, db_t)
        t_preload = time.perf_counter() - t0

        n_boletos = sum(len(v) for v in deps.get("boletos_by_id_cuenta", {}).values())

        mep_hoy = pnl.get_mep_for_date(date.today().isoformat())
        # Reset: a partir de acá stat mide SOLO las llamadas del loop.
        stat["n"] = 0
        stat["t"] = 0.0

        t1 = time.perf_counter()
        procesadas = 0
        for c in cuentas:
            id_cta = c.get("id_cuenta")
            if not id_cta:
                continue
            try:
                pnl._pnl_por_cuenta_core(
                    id_cuenta=str(id_cta),
                    db_cf=db_cf, db_v=db_v, db_t=db_t,
                    mep_hoy=mep_hoy, **deps,
                )
                procesadas += 1
            except Exception:
                continue
        t_loop = time.perf_counter() - t1
    finally:
        pnl.get_mep_for_date = orig

    print("\n" + "=" * 64)
    print("DESGLOSE de pnl_todas_cuentas_compute")
    print("=" * 64)
    print(f"  cuentas procesadas:      {procesadas:,}")
    print(f"  boletos precargados:     {n_boletos:,}")
    print("-" * 64)
    print(f"  PRELOAD (6 queries):     {t_preload:8.2f} s")
    print(f"  LOOP (por cuenta):       {t_loop:8.2f} s")
    print(f"     de eso en get_mep:    {stat['t']:8.2f} s  ({stat['n']:,} llamadas)")
    print(f"     resto del loop:       {t_loop - stat['t']:8.2f} s (cómputo + otros)")
    print("=" * 64)
    print(f"  TOTAL:                   {t_preload + t_loop:8.2f} s")
    print("=" * 64)

    print("\nLectura:")
    print("  • Si get_mep domina el LOOP → fix: compartir mep_cache entre cuentas")
    print("    (la misma fecha se re-busca N veces; hay solo ~1.3k fechas posibles).")
    print("  • Si PRELOAD domina → el costo es transferir los boletos; ahí el fix")
    print("    es proyectar menos campos / filtrar, no el mep.")


if __name__ == "__main__":
    main()
