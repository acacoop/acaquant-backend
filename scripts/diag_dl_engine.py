"""Diag READ-ONLY: corre el MOTOR real (engines.curvas.calcular_campos) para los
bonos dolar_linked y compara la TEA que RECALCULA vs la TEA PERSISTIDA en
mercado.market_snapshot (la que ve la pantalla).

Por qué existe: `debug_curva` (el endpoint /manager/checks/debug-curva-tea) tiene
un bug en la rama dolar_linked — filtra los flujos con `monto_flujo()` (que busca
`amortizacion`/`interes`) en vez de `monto_flujo_soberano()` (que lee
`amortizacion_pct`) → reporta "Sin flujos futuros" para TODOS los dolar_linked,
falso. Este diag saltea esa herramienta rota y va directo al motor.

Muestra por bono:
  last_price  |  monto flujo (motor)  |  TEA persistida (pantalla)  |  TEA motor (recalc)  |  veredicto

Uso (en el Droplet):  python -m scripts.diag_dl_engine
No escribe nada.
"""
from __future__ import annotations

from datetime import datetime


def _fmt(x, pct=False):
    if x is None:
        return "--"
    return f"{x:.2%}" if pct else f"{x:g}"


def main() -> None:
    from core import curvas_sql, market_snapshot
    from engines.curvas import (
        calcular_campos,
        cargar_a3500_actual,
        cargar_cer,
        cargar_dias_habiles,
        cargar_mep_actual,
        monto_flujo_soberano,
    )

    cer = cargar_cer()
    dias = cargar_dias_habiles()
    mep = cargar_mep_actual()
    a3500 = cargar_a3500_actual()
    print(f"\nMEP={mep}  A3500={a3500}")
    if not a3500:
        print("⚠️  A3500 (feed MAE) OFFLINE → el motor NO calcula TEA de dolar_linked "
              "aunque los flujos estén bien. Correr con el feed arriba para ver el cálculo real.\n")

    docs = [d for d in curvas_sql.cargar_todos() if (d.get("curva") or "") == "dolar_linked"]
    tickers_full = [d.get("ticker") for d in docs if d.get("ticker")]
    lp = {r["ticker"]: r for r in market_snapshot.last_prices(tickers_full)}
    persist = market_snapshot.snapshot_docs(tickers_full)

    print(f"{'ticker':8s} {'vto':11s} {'amort_pct':9s} {'monto_flujo':11s} "
          f"{'last_price':11s} {'TEA_pant':9s} {'TEA_motor':9s}  veredicto")
    print("-" * 110)

    for d in sorted(docs, key=lambda x: x.get("ticker_corto") or ""):
        tk = d.get("ticker_corto") or "?"
        full = d.get("ticker")
        vto = str(d.get("fecha_vencimiento") or "")[:10]
        flujos = d.get("flujos") or []
        vn = float(d.get("valor_nominal") or 100)
        amort_pct = flujos[0].get("amortizacion_pct") if flujos else None
        monto = monto_flujo_soberano(flujos[0], vn) if flujos else None

        row_lp = lp.get(full)
        price = row_lp["last_price"] if row_lp else None
        tea_pant = (persist.get(full, {}).get("metrics") or {}).get("TEA")

        tea_motor = None
        if price:
            fake = {"ticker": full, "price": price,
                    "timestamp": (row_lp.get("updated_at") if row_lp else None) or datetime.utcnow()}
            campos = calcular_campos(fake, d, cer, dias, mep, a3500) or {}
            tea_motor = campos.get("TEA")

        # Veredicto
        if monto is not None and 0 < monto < 5:
            vd = "⛔ monto ~1 (amort_pct en fracción, debería ser 100) → XIRR explota → sin TEA"
        elif tea_motor is None and tea_pant is not None:
            vd = "⚠️  motor NO calcula pero pantalla muestra TEA VIEJA (residuo no limpiado)"
        elif tea_motor is None:
            vd = "sin TEA (ver A3500 / flujos)"
        else:
            vd = "OK"

        print(f"{tk:8s} {vto:11s} {amort_pct!s:9s} {_fmt(monto):11s} "
              f"{_fmt(price):11s} {_fmt(tea_pant, True):9s} {_fmt(tea_motor, True):9s}  {vd}")


if __name__ == "__main__":
    main()
