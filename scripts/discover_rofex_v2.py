"""scripts/discover_rofex_v2.py — discovery enfocado v2.

Cierra las 5 preguntas que quedaron abiertas tras el primer discovery:

1. Lista TODOS los plazos de Cauciones Pesos ordenados por días.
2. Lista TODOS los plazos de Cauciones USD ordenados por días.
3. Confirma si existe 'MERV - XMEV - PESOS - 1D' y trae su market data.
4. Confirma si existe 'MERV - XMEV - DOLAR - 1D' y trae su market data.
5. Lista el primer DLR OUTRIGHT vigente (cficode FXXXSX, sin segundo '/')
   y trae su market data — para ver cómo viene el precio real.
6. Bonus: lista los 76 instrumentos del underlying 'Mercado de Dinero'.

Uso:
    /root/TradingAV/venv/bin/python -m scripts.discover_rofex_v2
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime

import pyRofex

from core.rofex_session import inicializar_sesion


def _safe(d, k, default=None):
    return d.get(k, default) if isinstance(d, dict) else default


def _str(v) -> str:
    return v if isinstance(v, str) else ""


def _ticker(inst) -> str:
    sym = _str(_safe(inst, "symbol"))
    if sym:
        return sym
    iid = _safe(inst, "instrumentId")
    if isinstance(iid, dict):
        return _str(iid.get("symbol"))
    return ""


def _maturity(inst):
    return _str(_safe(inst, "maturity_date")) or _str(_safe(inst, "maturityDate")) or ""


def _print_md(symbol: str, label: str):
    print(f"\n  → MD de '{symbol}'  ({label})")
    try:
        md = pyRofex.get_market_data(
            symbol,
            entries=[
                pyRofex.MarketDataEntry.BIDS,
                pyRofex.MarketDataEntry.OFFERS,
                pyRofex.MarketDataEntry.LAST,
                pyRofex.MarketDataEntry.OPENING_PRICE,
                pyRofex.MarketDataEntry.HIGH_PRICE,
                pyRofex.MarketDataEntry.LOW_PRICE,
                pyRofex.MarketDataEntry.CLOSING_PRICE,
                pyRofex.MarketDataEntry.NOMINAL_VOLUME,
                pyRofex.MarketDataEntry.TRADE_EFFECTIVE_VOLUME,
            ],
        )
        out = json.dumps(md, indent=2, default=str, ensure_ascii=False)
        print(out[:1800])
        if len(out) > 1800:
            print(f"  ... ({len(out) - 1800} chars más)")
    except Exception as e:
        print(f"  ERROR get_market_data({symbol}): {e}")


def _extract_dias_caucion(ticker: str) -> int | None:
    """De 'MERV - XMEV - PESOS - 99D' extrae 99."""
    m = re.search(r" - (\d+)D$", ticker)
    return int(m.group(1)) if m else None


def main():
    print("=" * 78)
    print("ROFEX — discovery v2 (enfocado en cauciones + DLR outright)")
    print("=" * 78)

    if not inicializar_sesion():
        print("No pude inicializar pyRofex.")
        sys.exit(1)

    res = pyRofex.get_detailed_instruments()
    if not res or res.get("status") != "OK":
        print(f"get_detailed_instruments() devolvió: {res}")
        sys.exit(1)

    instrumentos = res.get("instruments") or []
    print(f"\nTotal instrumentos: {len(instrumentos)}")

    # ─────────────────────────────────────────────────────────────────────────
    # 1. TODOS los plazos de Cauciones Pesos
    # ─────────────────────────────────────────────────────────────────────────
    print("\n─── Cauciones Pesos: TODOS los plazos (ordenados por días) ──────────────")
    cauciones_ars = [
        i for i in instrumentos
        if _str(_safe(i, "underlying")) == "Cauciones Pesos"
    ]
    plazos_ars: list[tuple[int, str]] = []
    sin_plazo_ars: list[str] = []
    for c in cauciones_ars:
        t = _ticker(c)
        d = _extract_dias_caucion(t)
        if d is not None:
            plazos_ars.append((d, t))
        else:
            sin_plazo_ars.append(t)

    plazos_ars.sort()
    print(f"  Total con formato '{{N}}D': {len(plazos_ars)}")
    for dias, ticker in plazos_ars[:15]:
        print(f"    {dias:>4d}D  {ticker}")
    if len(plazos_ars) > 15:
        print(f"    ... y {len(plazos_ars) - 15} más, hasta {plazos_ars[-1][0]}D")

    if sin_plazo_ars:
        print(f"\n  {len(sin_plazo_ars)} sin formato '{{N}}D' (primeros 5):")
        for t in sin_plazo_ars[:5]:
            print(f"    {t}")

    # ─────────────────────────────────────────────────────────────────────────
    # 2. TODOS los plazos de Cauciones USD
    # ─────────────────────────────────────────────────────────────────────────
    print("\n─── Cauciones USD: TODOS los plazos (ordenados por días) ────────────────")
    cauciones_usd = [
        i for i in instrumentos
        if _str(_safe(i, "underlying")) == "Cauciones USD"
    ]
    plazos_usd: list[tuple[int, str]] = []
    sin_plazo_usd: list[str] = []
    for c in cauciones_usd:
        t = _ticker(c)
        d = _extract_dias_caucion(t)
        if d is not None:
            plazos_usd.append((d, t))
        else:
            sin_plazo_usd.append(t)

    plazos_usd.sort()
    print(f"  Total con formato '{{N}}D': {len(plazos_usd)}")
    for dias, ticker in plazos_usd[:15]:
        print(f"    {dias:>4d}D  {ticker}")
    if len(plazos_usd) > 15:
        print(f"    ... y {len(plazos_usd) - 15} más, hasta {plazos_usd[-1][0]}D")

    # ─────────────────────────────────────────────────────────────────────────
    # 3 + 4. Tickers exactos para 1D
    # ─────────────────────────────────────────────────────────────────────────
    print("\n─── Caución 1D — pesos y dólares (sample MD si existen) ─────────────────")
    candidatos_1d = [
        ("MERV - XMEV - PESOS - 1D",  "Caución ARS 1 día"),
        ("MERV - XMEV - DOLAR - 1D",  "Caución USD 1 día"),
        ("MERV - XMEV - PESOS - 7D",  "Caución ARS 7 días (referencia)"),
        ("MERV - XMEV - DOLAR - 7D",  "Caución USD 7 días (referencia)"),
    ]
    for t, label in candidatos_1d:
        existe = any(_ticker(i) == t for i in instrumentos)
        marca = "✓ existe" if existe else "✗ NO está en el universo"
        print(f"  {marca}: '{t}'  ({label})")
        if existe:
            _print_md(t, label)

    # ─────────────────────────────────────────────────────────────────────────
    # 5. DLR outright — primer vigente sin spread
    # ─────────────────────────────────────────────────────────────────────────
    print("\n─── DLR outright — primer vigente single-leg (sin spread) ───────────────")
    hoy_str = datetime.now().strftime("%Y%m%d")
    dlr_outrights = []
    for i in instrumentos:
        if _str(_safe(i, "underlying")) != "Dólar USA A3500":
            continue
        if _str(_safe(i, "cficode")) != "FXXXSX":
            continue
        if _maturity(i) <= hoy_str:
            continue
        t = _ticker(i)
        if not t or t.count("/") != 1:
            # Outright single-leg tiene exactamente 1 '/' (DLR/MMM_YY).
            continue
        dlr_outrights.append((_maturity(i), t))

    dlr_outrights.sort()
    print(f"  Total outrights vigentes: {len(dlr_outrights)}")
    for mat, t in dlr_outrights[:10]:
        print(f"    maturity={mat}  ticker={t}")

    if dlr_outrights:
        print("\n  Sample del más cercano:")
        _print_md(dlr_outrights[0][1], f"DLR outright más cercano (vto {dlr_outrights[0][0]})")
        if len(dlr_outrights) > 5:
            print("\n  Sample del 5to (mediano horizonte):")
            _print_md(dlr_outrights[5][1], f"DLR outright (vto {dlr_outrights[5][0]})")

    # ─────────────────────────────────────────────────────────────────────────
    # 6. Mercado de Dinero — qué hay
    # ─────────────────────────────────────────────────────────────────────────
    print("\n─── Underlying 'Mercado de Dinero' (76 instrumentos) ────────────────────")
    md_instruments = [
        i for i in instrumentos
        if _str(_safe(i, "underlying")) == "Mercado de Dinero"
    ]
    print(f"  Total: {len(md_instruments)}")
    for inst in md_instruments[:25]:
        sym = _ticker(inst) or "(sin ticker)"
        cf = _str(_safe(inst, "cficode")) or "?"
        sd = _str(_safe(inst, "securityDescription"))
        print(f"    {sym}  |  cf={cf}  |  desc={sd}")
    if len(md_instruments) > 25:
        print(f"    ... ({len(md_instruments) - 25} más)")

    print("\n" + "=" * 78)
    print("Fin v2. Mandame el output entero.")
    print("=" * 78)


if __name__ == "__main__":
    main()
