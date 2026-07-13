"""test_dolar_mtr.py — PRUEBA AISLADA: ¿qué trae el dólar Matba Rofex DOLAR/MTR?

Fetch sincrónico de una sola vez (pyRofex.get_market_data) — NO abre WS ni toca
ningún motor. Solo pide el market data del símbolo y lo imprime, para confirmar
que trae precio ANTES de meterlo en motor_rofex.

Correr donde haya sesión ROFEX (Droplet):  python -m scripts.test_dolar_mtr
"""
from __future__ import annotations

import pyRofex

from core.rofex_session import inicializar_sesion

SYMBOL = "DOLAR/MTR"

ENTRIES = [
    pyRofex.MarketDataEntry.LAST,
    pyRofex.MarketDataEntry.BIDS,
    pyRofex.MarketDataEntry.OFFERS,
    pyRofex.MarketDataEntry.OPENING_PRICE,
    pyRofex.MarketDataEntry.CLOSING_PRICE,
    pyRofex.MarketDataEntry.HIGH_PRICE,
    pyRofex.MarketDataEntry.LOW_PRICE,
]


def main() -> None:
    inicializar_sesion()
    print(f"pidiendo market data de {SYMBOL!r} ...\n")
    md = pyRofex.get_market_data(ticker=SYMBOL, entries=ENTRIES)

    print("=== RESPUESTA CRUDA ===")
    print(md)

    status = (md or {}).get("status")
    if status != "OK":
        print(f"\n⚠ status = {status!r} — el símbolo no trajo dato. "
              "Errores típicos: símbolo mal escrito o sin permiso de mercado.")
        print("Detalle:", (md or {}).get("description") or (md or {}).get("message"))
        return

    data = (md or {}).get("marketData") or {}

    def _px(entry):
        v = data.get(entry)
        if isinstance(v, dict):
            return v.get("price")
        if isinstance(v, list) and v:
            return v[0].get("price")
        return v

    print("\n=== RESUMEN ===")
    print(f"  LAST     : {_px('LA')}")
    print(f"  BID      : {_px('BI')}")
    print(f"  OFFER    : {_px('OF')}")
    print(f"  CIERRE   : {_px('CL')}")
    print(f"  OPEN     : {_px('OP')}")
    print(f"  HIGH/LOW : {_px('HI')} / {_px('LO')}")
    print("\nSi LAST (o CIERRE) trae el ~1.487 que ves en pantalla, "
          "lo metemos en motor_rofex y alimentamos dolar_matba.")


if __name__ == "__main__":
    main()
