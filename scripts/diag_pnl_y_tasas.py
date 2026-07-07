"""Diag READ-ONLY para dos incidentes (NO escribe nada en la DB):

  A) job `pnl_totales_precompute` → PARTIAL "0 cuentas — cache NO tocada".
     Reproduce EXACTAMENTE el camino de `pnl_todas_cuentas_compute_sql()` pero
     SIN el `except Exception: continue` que traga el error real → imprime la
     causa raíz (cuántas cuentas hay, si las deps vienen vacías, y el traceback
     de la primera cuenta que falla + un conteo por tipo de excepción).

  B) RENTA FIJA → bonos que muestran "--" en TEA/TEM.
     Recorre TODO `mercado.curvas` y, por ticker, corre `debug_calculo_tea()`
     (recalcula la tasa igual que el motor). Clasifica cada bono en:
       - OK          : tiene TEA persistida.
       - RELLENABLE  : NO tiene TEA persistida PERO el recálculo SÍ da tasa
                       (→ backfill los arregla ya).
       - NO CALCULA  : ni persistida ni recalculada → imprime el motivo
                       (falta flujo_vencimiento / feed A3500 / curva no soportada…).

Uso (en el Droplet, desde la raíz):  python -m scripts.diag_pnl_y_tasas
No toca la cache ni el market_snapshot. Se puede correr a cualquier hora.
"""
from __future__ import annotations

import traceback
from collections import Counter
from datetime import date


def diag_pnl() -> None:
    print("=" * 72)
    print("A) DIAG job pnl_totales_precompute  (por qué devuelve 0 cuentas)")
    print("=" * 72)

    from api.services import pnl_sql, portfolio_sql
    from api.services.pnl import _pnl_por_cuenta_core

    cuentas = portfolio_sql.listar_cuentas()
    print(f"listar_cuentas() → {len(cuentas)} cuentas")
    if not cuentas:
        print("  ⛔ CAUSA = (a): portafolio.tenencia no tiene NINGUNA fila aum='si'.")
        print("     → el writer diario (portafolio_backfill --diario) no marcó aum, "
              "o la tabla está vacía de aum='si'. Revisar ese job.")
        return

    deps = pnl_sql._deps_sql(only_cuenta=None)
    n_bol = len(deps.get("boletos_by_id_cuenta") or {})
    n_aum = len(deps.get("aum_rows_by_id_cuenta") or {})
    print(f"_deps_sql(None) → boletos en {n_bol} cuentas | posición (aum) en {n_aum} cuentas")
    print(f"  fecha_actual_aum_global = {deps.get('fecha_actual_aum_global')}")
    if not n_bol and not n_aum:
        print("  ⛔ CAUSA = deps vacío: el job haría raise RuntimeError (status=error, no partial).")
        return

    mep_hoy = pnl_sql.get_mep_for_date(date.today().isoformat())
    print(f"mep_hoy = {mep_hoy}")
    mep_cache: dict = {}

    ok = 0
    fallas: Counter = Counter()
    primer_tb: str | None = None
    primer_cta: str | None = None

    for c in cuentas:
        id_cta = c.get("id_cuenta")
        if not id_cta:
            continue
        try:
            _pnl_por_cuenta_core(
                id_cuenta=str(id_cta),
                db_cf=None, db_v=None, db_t=None,
                mep_hoy=mep_hoy, mep_cache=mep_cache, **deps,
            )
            ok += 1
        except Exception as e:
            fallas[type(e).__name__] += 1
            if primer_tb is None:
                primer_tb = traceback.format_exc()
                primer_cta = str(id_cta)

    print(f"\nRESULTADO: {ok} cuentas OK / {sum(fallas.values())} fallaron "
          f"(de {len(cuentas)} totales)")
    if fallas:
        print("Excepciones por tipo:")
        for tipo, n in fallas.most_common():
            print(f"  {tipo}: {n}")
        print(f"\n── Traceback de la 1ª cuenta que falló (id_cuenta={primer_cta}) ──")
        print(primer_tb)
        print("⛔ CAUSA = (b): TODAS/las cuentas lanzan y el `except: continue` del job "
              "(pnl_sql.py:310) lo tragaba → out=[] → PARTIAL. Arriba está el error real.")
    elif ok == 0:
        print("  ⚠️  Ninguna cuenta OK pero tampoco excepciones — revisar filtro id_cuenta.")
    else:
        print("  ✅ El compute SÍ produce cuentas ahora. El fallo puede ser intermitente "
              "(deps vacío en el momento del cron) — revisar timing vs backfill.")


def diag_tasas() -> None:
    print("\n" + "=" * 72)
    print("B) DIAG RENTA FIJA  (bonos sin TEA/TEM y si el backfill los puede rellenar)")
    print("=" * 72)

    from api.services.debug_curva import debug_calculo_tea
    from core import curvas_sql

    docs = curvas_sql.cargar_todos()
    print(f"mercado.curvas → {len(docs)} instrumentos\n")

    rellenables: list[tuple[str, str, float]] = []
    no_calcula: list[tuple[str, str, str]] = []
    ok = 0

    for d in docs:
        tk = d.get("ticker_corto") or d.get("ticker") or "?"
        curva = d.get("curva", "?")
        try:
            r = debug_calculo_tea(tk)
        except Exception as e:
            no_calcula.append((tk, curva, f"debug crash: {type(e).__name__}: {e}"))
            continue

        if not r.get("ok"):
            no_calcula.append((tk, curva, r.get("message", "sin detalle")))
            continue

        tea_persist = (r.get("trade") or {}).get("TEA")
        tea_calc = (r.get("calculado") or {}).get("TEA")
        err = r.get("error_calc")

        if tea_persist not in (None, 0):
            ok += 1
        elif tea_calc not in (None, 0):
            rellenables.append((tk, curva, float(tea_calc)))
        else:
            no_calcula.append((tk, curva, err or "sin TEA persistida ni recalculada"))

    print(f"RESUMEN: {ok} con TEA OK | {len(rellenables)} RELLENABLES por backfill | "
          f"{len(no_calcula)} NO calculan\n")

    if rellenables:
        print("── RELLENABLES (el backfill les pone TEA ya mismo) ──")
        for tk, curva, tea in sorted(rellenables):
            print(f"  {tk:10s} [{curva:12s}]  TEA recalc = {tea:.4%}")
        print()

    if no_calcula:
        print("── NO CALCULAN (motivo — no se pueden rellenar sin arreglar el input) ──")
        for tk, curva, motivo in sorted(no_calcula):
            print(f"  {tk:10s} [{curva:12s}]  {motivo}")


def main() -> None:
    diag_pnl()
    diag_tasas()


if __name__ == "__main__":
    main()
