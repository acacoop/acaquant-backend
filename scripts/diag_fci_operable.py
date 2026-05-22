"""Discovery read-only de FCI operables vía pyRofex.

Lista los instruments con cficode "CIO…" (cuotapartes de FCI) y sus
parámetros operables, para diseñar la vista de suscripción/rescate de
Trading SIN enviar órdenes. Solo lecturas REST (`get_detailed_instruments`
+ `get_market_data`); no manda nada al mercado.

Contexto de diseño (lo que vamos a construir con esto):
  - La orden al mercado va SIEMPRE por cantidad (cuotapartes).
  - La UI permite ingresar importe en $ → se convierte a cuotapartes con la
    cuota (last/close). Este script muestra la cuota de cada fondo para
    validar esa conversión y la precisión de cantidad permitida.
  - Suscripción = BUY, rescate = SELL (a confirmar contra settlType/orderTypes).

Uso:
    python -m scripts.diag_fci_operable
"""
from __future__ import annotations

import pyRofex

from core.rofex_session import inicializar_sesion

CFI_FCI_PREFIX = "CIO"
# Tope de lookups de market data (REST secuencial). El metadata se lista para
# TODOS; la cuota solo para los primeros N para que la corrida sea ágil.
MD_LOOKUP_LIMIT = 60


def _f(v) -> str:
    return "—" if v is None else str(v)


def _sym(inst: dict) -> str:
    sym = inst.get("symbol")
    if isinstance(sym, str) and sym:
        return sym
    iid = inst.get("instrumentId") or {}
    s = iid.get("symbol")
    return s if isinstance(s, str) else "?"


def main() -> None:
    if not inicializar_sesion():
        print("❌ No pude iniciar sesión pyRofex — abortando.")
        return

    res = pyRofex.get_detailed_instruments()
    if not res or res.get("status") != "OK":
        print(f"❌ get_detailed_instruments() falló: {res}")
        return

    instruments = res.get("instruments") or []
    fci = [i for i in instruments if (i.get("cficode") or "").startswith(CFI_FCI_PREFIX)]
    fci.sort(key=_sym)

    print(
        f"\nTotal instruments: {len(instruments)} · "
        f"FCI (cficode {CFI_FCI_PREFIX}…): {len(fci)}\n"
    )
    if not fci:
        print("No se encontraron FCI con ese cficode. ¿Cambió el prefijo?")
        return

    for n, inst in enumerate(fci):
        sym = _sym(inst)
        print("─" * 84)
        print(f"TICKER      {sym}")
        print(f"  cficode             {_f(inst.get('cficode'))}")
        print(f"  underlying          {_f(inst.get('underlying'))}")
        print(f"  segment             {_f(inst.get('marketSegmentId'))}")
        print(f"  currency            {_f(inst.get('currency'))}")
        print(f"  settlType           {_f(inst.get('settlType'))}")
        print(f"  orderTypes          {_f(inst.get('orderTypes'))}")
        print(f"  minTradeVol         {_f(inst.get('minTradeVol'))}")
        print(f"  maxTradeVol         {_f(inst.get('maxTradeVol'))}")
        print(
            f"  priceIncrement      "
            f"{_f(inst.get('minPriceIncrement') or inst.get('tickSize'))}"
        )
        print(f"  pricePrecision      {_f(inst.get('instrumentPricePrecision'))}")
        print(f"  sizePrecision       {_f(inst.get('instrumentSizePrecision'))}")
        print(f"  contractMultiplier  {_f(inst.get('contractMultiplier'))}")
        print(
            f"  low/highLimit       "
            f"{_f(inst.get('lowLimitPrice'))} / {_f(inst.get('highLimitPrice'))}"
        )

        # Cuota actual (read-only) — para importe ↔ cuotapartes.
        if n < MD_LOOKUP_LIMIT:
            try:
                md = pyRofex.get_market_data(
                    sym,
                    entries=[
                        pyRofex.MarketDataEntry.LAST,
                        pyRofex.MarketDataEntry.CLOSING_PRICE,
                        pyRofex.MarketDataEntry.BIDS,
                        pyRofex.MarketDataEntry.OFFERS,
                    ],
                )
                data = (md or {}).get("marketData", {}) or {}
                la = data.get("LA") or {}
                bids = data.get("BI") or []
                offers = data.get("OF") or []
                print(
                    f"  CUOTA last/close    {_f(la.get('price'))} / {_f(data.get('CL'))}"
                    f"   ·  book bids/offers {len(bids)}/{len(offers)}"
                )
            except Exception as e:
                print(f"  market data: error {e}")
        else:
            print("  CUOTA               (omitida — superado MD_LOOKUP_LIMIT)")

    print("\n" + "=" * 84)
    print("LECTURA PARA LA VISTA FCI (Trading):")
    print("  • Orden al mercado = SIEMPRE cantidad (cuotapartes).")
    print("  • UI importe $ → cuotapartes = importe / cuota (last/close).")
    print("  • Suscripción = BUY · rescate = SELL (confirmar vs settlType/orderTypes).")
    print("  • Respetar minTradeVol y sizePrecision al redondear la cantidad.")
    print("  • Si las cuotas vienen en null/0 fuera de rueda, la conversión")
    print("    importe→cuotapartes solo es confiable con mercado abierto.")


if __name__ == "__main__":
    main()
