"""Debug del cálculo YTM de un bono soberano.

Reproduce el cálculo del branch curva='soberanos' en engines/curvas.py
paso a paso para entender por qué el TEA sale raro (ej. GD30D dando
100% cuando debería dar ~6.5%).

Muestra:
  1. Doc de Trading.Curvas (flujos crudos del prospecto).
  2. Precio convertido a USD (si aplica MEP).
  3. Lista de flujos FUTUROS (al settlement) con monto USD calculado.
  4. Cashflow: [-precio_usd, flujo_1, flujo_2, ...].
  5. XIRR resultante.
  6. Paridad = precio_usd / residual_vivo × 100.

Uso:
    python -m scripts.debug_soberano GD30D
    python -m scripts.debug_soberano GD30 --precio 84000
    python -m scripts.debug_soberano GD30D --precio 64.7
"""
from __future__ import annotations

import argparse
from datetime import UTC, date, datetime

from core.mongo import get_mongo_client
from engines.curvas import (
    cargar_dias_habiles,
    cargar_mep_actual,
    fecha_flujo,
    macaulay_duration,
    monto_flujo_soberano,
    precio_soberano_a_usd,
    siguiente_dia_habil,
    xirr,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("ticker_corto", help="p.ej. GD30D, GD30, AL30D")
    parser.add_argument("--precio", type=float, default=None,
                        help="Precio manual (si no se pasa, se usa el último trade en TimeSales)")
    args = parser.parse_args()

    client = get_mongo_client()

    # ── 1. Doc del bono en Trading.Curvas ──────────────────────────
    inst = client["Trading"]["Curvas"].find_one({"ticker_corto": args.ticker_corto})
    if not inst:
        print(f"✗ No existe doc con ticker_corto='{args.ticker_corto}' en Trading.Curvas")
        return 1

    print(f"\n══ Trading.Curvas ══")
    print(f"  ticker:            {inst['ticker']!r}")
    print(f"  ticker_corto:      {inst['ticker_corto']!r}")
    print(f"  tipo / curva:      {inst.get('tipo')!r} / {inst.get('curva')!r}")
    print(f"  fecha_vencimiento: {inst['fecha_vencimiento']}")
    print(f"  valor_nominal:     {inst.get('valor_nominal')}")
    print(f"  # flujos total:    {len(inst.get('flujos') or [])}")

    # ── 2. Precio a usar ──────────────────────────────────────────
    precio = args.precio
    if precio is None:
        last_trade = client["Trading"]["TimeSales"].find_one(
            {"ticker": inst["ticker"]},
            {"_id": 0, "price": 1, "timestamp": 1},
            sort=[("timestamp", -1)],
        )
        if not last_trade:
            print("✗ No hay trades de este ticker en TimeSales. Pasá --precio manualmente.")
            return 2
        precio = float(last_trade["price"])
        print(f"\n══ Precio ══")
        print(f"  último trade: {last_trade['timestamp']}  →  price={precio}")
    else:
        print(f"\n══ Precio (manual) ══")
        print(f"  {precio}")

    mep = cargar_mep_actual(client)
    print(f"  MEP actual:   {mep}")

    precio_usd = precio_soberano_a_usd(precio, inst["ticker"], mep)
    if precio_usd is None:
        print("✗ No se pudo convertir precio a USD (falta MEP para pesos?).")
        return 3
    print(f"  precio USD:   {precio_usd:.4f}")

    # ── 3. Flujos futuros ─────────────────────────────────────────
    dias_habiles = cargar_dias_habiles(client)
    hoy = datetime.now(UTC).date()
    settlement_str = siguiente_dia_habil(dias_habiles, hoy)
    fecha_settlement = date.fromisoformat(settlement_str) if settlement_str else hoy
    print(f"\n══ Settlement T+1 ══")
    print(f"  hoy:          {hoy}")
    print(f"  settlement:   {fecha_settlement}")

    flujos_raw = inst.get("flujos") or []
    valor_nominal = float(inst.get("valor_nominal", 100))

    print(f"\n══ Flujos FUTUROS (> settlement) ══")
    print(f"  {'fecha':<12} {'amort_pct':>10} {'cup_s_res':>10} {'res_prev':>10} {'monto_USD':>12}")
    print(f"  {'-'*12} {'-'*10} {'-'*10} {'-'*10} {'-'*12}")

    flujos_futuros = []
    for f in flujos_raw:
        fd = fecha_flujo(f)
        if not fd or fd <= fecha_settlement:
            continue
        monto = monto_flujo_soberano(f, valor_nominal)
        if monto <= 0:
            continue
        print(f"  {str(fd):<12} "
              f"{f.get('amortizacion_pct', 0):>10} "
              f"{f.get('cupon_sobre_residual', 0):>10} "
              f"{f.get('residual_previo_pct', 0):>10} "
              f"{monto:>12.4f}")
        flujos_futuros.append((fd, monto))

    if not flujos_futuros:
        print("✗ No hay flujos futuros.")
        return 4

    total_flujos = sum(m for _, m in flujos_futuros)
    print(f"  {'-'*12} {'-'*10} {'-'*10} {'-'*10} {'-'*12}")
    print(f"  {'TOTAL':<46} {total_flujos:>12.4f}")

    # ── 4. XIRR ───────────────────────────────────────────────────
    fechas_dt = [datetime.combine(fecha_settlement, datetime.min.time())] + \
                [datetime.combine(fd, datetime.min.time()) for fd, _ in flujos_futuros]
    cf = [-precio_usd] + [m for _, m in flujos_futuros]

    print(f"\n══ Cashflow para XIRR ══")
    for fdt, c in zip(fechas_dt, cf, strict=False):
        print(f"  {fdt.date()}  {c:>+12.4f}")

    tea = xirr(fechas_dt, cf)
    print(f"\n══ Resultado ══")
    if tea is None:
        print("  ✗ XIRR no convergió.")
    else:
        print(f"  TEA (YTM):  {tea:.6f}  =  {tea*100:.2f}%")
        fechas_flujos_dt = [datetime.combine(fd, datetime.min.time()) for fd, _ in flujos_futuros]
        montos_flujos    = [m for _, m in flujos_futuros]
        fecha_base_dt    = datetime.combine(fecha_settlement, datetime.min.time())
        dur = macaulay_duration(fechas_flujos_dt, montos_flujos, tea, fecha_base_dt)
        print(f"  Duration:   {dur}  años")

    # Paridad
    primer_flujo_raw = next(
        (f for f in flujos_raw
         if fecha_flujo(f) and fecha_flujo(f) > fecha_settlement),
        None,
    )
    if primer_flujo_raw:
        residual = float(primer_flujo_raw.get("residual_previo_pct", 100))
        paridad = precio_usd / residual * 100 if residual > 0 else None
        print(f"  Paridad:    {paridad:.4f}%  (precio_usd={precio_usd:.4f} / residual_vivo={residual})")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
