"""diag_cedears_pyrofex.py — verifica que pyRofex publica market data
para los CEDEARs piloto vía REST.

Antes de codear un motor WS dedicado (`engines/motor_cedears.py`),
necesitamos confirmar:

  1. Que el ticker `MERV - XMEV - AMD - 24hs` existe en el padrón pyRofex.
  2. Que `get_market_data()` devuelve OP/HI/LO/CL/LA poblados durante
     horario de mercado.
  3. Qué CFI tienen (probablemente 'ES' = Equity Share, vía CEDEAR).
  4. Si hay tickers similares para validar nuestra nomenclatura
     (ej. AMDD para USD, AMD CI para caja inmediata) — confirma que
     '24hs' es el sufijo correcto para liquidación ARS T+2.

Sin esto, el motor que codeemos puede estar suscribiéndose a un ticker
que pyRofex nunca publica → silent failure.

Uso (desde la raíz del repo, en el Droplet con creds Rofex en .env):
    python -m scripts.diag_cedears_pyrofex

Salida esperada (sano, en horario de mercado):
  [AMD]  exists=True  cfi=ES...  OP=XX  HI=YY  LO=ZZ  LA=WW
  [NVDA] exists=True  cfi=ES...  OP=...
"""
from __future__ import annotations

import sys
import traceback
from typing import Any

import pyRofex

from core.rofex_session import inicializar_sesion


# Tickers a chequear desde argv (ej. `python -m scripts.diag_cedears_pyrofex DELL ORCL`).
# Default si no se pasa nada: AMD + NVDA.
DEFAULT_PREFIJOS = ["AMD", "NVDA"]


def _summary_marketdata(md: dict[str, Any]) -> str:
    """Resumen one-line de los entries que devolvió get_market_data."""
    if not md or md.get("status") != "OK":
        return f"FAIL status={md.get('status')} description={md.get('description')}"
    d = md.get("marketData", {})
    parts = []
    for k in ("OP", "HI", "LO", "CL", "LA", "BI", "OF", "EV"):
        v = d.get(k)
        if v is None:
            parts.append(f"{k}=∅")
            continue
        if isinstance(v, dict):
            # LA, BI, OF típicamente son dicts con .price
            px = v.get("price", v.get(0, {}).get("price") if isinstance(v.get(0), dict) else None)
            parts.append(f"{k}={px}")
        elif isinstance(v, list) and v:
            # BI/OF a veces vienen como lista de levels
            first = v[0]
            if isinstance(first, dict):
                parts.append(f"{k}={first.get('price')}")
            else:
                parts.append(f"{k}={first}")
        else:
            parts.append(f"{k}={v}")
    return " ".join(parts)


def run(prefijos: list[str]) -> None:
    print("=" * 100)
    print(f"DIAG CEDEARs pyRofex — chequeo: {prefijos}")
    print("=" * 100)

    print("\n[1] Iniciando sesión Rofex…")
    try:
        inicializar_sesion()
        print("    ✓ Sesión Rofex inicializada")
    except Exception:
        print("    ✗ No se pudo iniciar sesión Rofex.")
        traceback.print_exc()
        return

    # Tickers a probar con get_market_data — el formato BYMA 24hs.
    tickers = [f"MERV - XMEV - {p} - 24hs" for p in prefijos]

    # ── [2] get_market_data por ticker explícito ──
    print("\n[2] get_market_data() para cada ticker")
    entries = [
        pyRofex.MarketDataEntry.OPENING_PRICE,
        pyRofex.MarketDataEntry.HIGH_PRICE,
        pyRofex.MarketDataEntry.LOW_PRICE,
        pyRofex.MarketDataEntry.CLOSING_PRICE,
        pyRofex.MarketDataEntry.LAST,
        pyRofex.MarketDataEntry.BIDS,
        pyRofex.MarketDataEntry.OFFERS,
        pyRofex.MarketDataEntry.TRADE_EFFECTIVE_VOLUME,
    ]
    for t in tickers:
        try:
            md = pyRofex.get_market_data(t, entries=entries)
            print(f"  · {t}")
            print(f"    → {_summary_marketdata(md)}")
        except Exception as e:
            print(f"  ✗ {t} → exception: {type(e).__name__}: {e}")

    # ── [3] Inspección del padrón ──
    print(f"\n[3] get_detailed_instruments — variantes con prefijos {prefijos}")
    try:
        res = pyRofex.get_detailed_instruments()
        if not res or res.get("status") != "OK":
            print(f"    ✗ status={res.get('status') if res else 'None'}")
            return
        instruments = res.get("instruments", [])
        print(f"    Padrón total: {len(instruments)} instrumentos")

        for prefijo in prefijos:
            print(f"\n  · Variantes con '{prefijo}' en symbol:")
            matches = []
            for inst in instruments:
                sym = inst.get("instrumentId", {}).get("symbol", "")
                # Buscamos symbol que contenga el prefijo seguido de espacio o 'D' (USD).
                # Filtramos opciones (cfi empieza con O).
                cfi = inst.get("cficode", "")
                if cfi.startswith("O"):
                    continue
                if f" {prefijo} " in sym or f" {prefijo}D " in sym or f" {prefijo}24 " in sym:
                    matches.append({
                        "symbol":   sym,
                        "cfi":      cfi,
                        "currency": inst.get("currency"),
                        "underlying": inst.get("underlying"),
                    })

            # Ordenar por symbol para output legible
            matches.sort(key=lambda x: x["symbol"])
            if not matches:
                print(f"    ∅ Ninguno. Probable que pyRofex no exponga {prefijo}, o que la sesión no tenga permisos.")
            else:
                for m in matches[:15]:
                    print(f"      {m['symbol']:<40s} cfi={m['cfi']:<10s} ccy={m['currency']:<6s} und={m['underlying']!r}")
                if len(matches) > 15:
                    print(f"      … y {len(matches) - 15} más")

    except Exception:
        print("    ✗ exception en get_detailed_instruments:")
        traceback.print_exc()

    print("\n" + "=" * 100)
    print("LECTURA")
    print("=" * 100)
    print("• Si [2] devuelve OP/HI/LO/LA poblados con números → feed OK, podemos codear el motor WS.")
    print("• Si [2] devuelve todo ∅ pero el ticker existe en [3] → mercado cerrado o sesión sin permisos.")
    print("• Si [3] no muestra el ticker exacto pero sí variantes → ajustar nomenclatura antes de motor.")
    print("• Si [3] no muestra NADA con prefijo → pyRofex no tiene equity en esta sesión / cuenta.")


if __name__ == "__main__":
    args = [a.upper() for a in sys.argv[1:]] or DEFAULT_PREFIJOS
    run(prefijos=args)
