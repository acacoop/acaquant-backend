"""scripts/discover_rofex_instruments.py — qué expone ROFEX y cómo viene.

Ejecutar en el server (con .env cargado y la sesión pyRofex disponible)
para entender los strings reales de underlying, cficode y ticker que
usa ROFEX hoy. Sin esto, diseñar engines de futuros / caución es a
ciegas.

Uso:
    /root/TradingAV/venv/bin/python -m scripts.discover_rofex_instruments

Output (al terminal):
- Conteos de underlyings y cficodes únicos.
- Listing de futuros vigentes agrupados por underlying (ej. DLR, RFX20).
- Listing de candidatos a caución (heurístico).
- Sample real de get_market_data sobre un futuro DLR y una caución.

Pegame el output completo y armamos los engines con datos reales.
"""
from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from datetime import datetime

import pyRofex

from core.rofex_session import inicializar_sesion


def _safe(d, k, default=None):
    return d.get(k, default) if isinstance(d, dict) else default


def _str(v) -> str:
    """Normaliza a string seguro para .upper() / startswith. None → ''."""
    return v if isinstance(v, str) else ""


def _maturity(inst):
    return _str(_safe(inst, "maturity_date")) or _str(_safe(inst, "maturityDate")) or ""


def _ticker(inst) -> str:
    """Extrae el ticker real. ROFEX a veces lo pone en 'symbol' directo,
    otras veces dentro de 'instrumentId': {'symbol': ...}."""
    sym = _str(_safe(inst, "symbol"))
    if sym:
        return sym
    iid = _safe(inst, "instrumentId")
    if isinstance(iid, dict):
        return _str(iid.get("symbol"))
    return ""


def _print_md_sample(symbol: str, label: str):
    print(f"\n  Pidiendo MD de: {symbol}")
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
        # Truncamos para no inundar el terminal.
        out = json.dumps(md, indent=2, default=str, ensure_ascii=False)
        print(out[:2500])
        if len(out) > 2500:
            print(f"  ... ({len(out) - 2500} chars más)")
    except Exception as e:
        print(f"  ERROR get_market_data({label}): {e}")


def main():
    print("=" * 78)
    print("ROFEX — discovery de instrumentos")
    print("=" * 78)

    if not inicializar_sesion():
        print("No pude inicializar pyRofex. Revisá ROFEX_USER/PASSWORD/ACCOUNT en .env.")
        sys.exit(1)

    res = pyRofex.get_detailed_instruments()
    if not res or res.get("status") != "OK":
        print(f"get_detailed_instruments() devolvió: {res}")
        sys.exit(1)

    instrumentos = res.get("instruments") or []
    print(f"\nTotal de instrumentos: {len(instrumentos)}\n")

    # ─────────────────────────────────────────────────────────────────────────
    # 0. Dump completo del PRIMER instrumento — para ver todos los keys/values
    # ─────────────────────────────────────────────────────────────────────────
    print("─── ESTRUCTURA cruda del primer instrumento (todos los keys) ────────────")
    if instrumentos:
        print(json.dumps(instrumentos[0], indent=2, default=str, ensure_ascii=False))
    print()

    # Dump del primer FUTURO también, porque la estructura puede diferir
    print("─── ESTRUCTURA cruda del primer FUTURO (cficode F...) ───────────────────")
    primer_futuro = next(
        (i for i in instrumentos if _str(_safe(i, "cficode")).startswith("F")),
        None
    )
    if primer_futuro:
        print(json.dumps(primer_futuro, indent=2, default=str, ensure_ascii=False))
    else:
        print("  No encontré ningún futuro.")
    print()

    # Dump de la primera CAUCIÓN
    print("─── ESTRUCTURA cruda de la primera CAUCIÓN (cficode RP...) ──────────────")
    primera_caucion = next(
        (i for i in instrumentos if _str(_safe(i, "cficode")).startswith("R")),
        None
    )
    if primera_caucion:
        print(json.dumps(primera_caucion, indent=2, default=str, ensure_ascii=False))
    else:
        print("  No encontré ninguna caución.")
    print()

    # ─────────────────────────────────────────────────────────────────────────
    # 1. Categorización general
    # ─────────────────────────────────────────────────────────────────────────
    underlyings: Counter = Counter()
    cficodes_full: Counter = Counter()
    cficodes_cat: Counter = Counter()  # primer char
    for inst in instrumentos:
        underlyings[_str(_safe(inst, "underlying")) or "?"] += 1
        cf = _str(_safe(inst, "cficode")) or "?"
        cficodes_full[cf] += 1
        cficodes_cat[cf[:1]] += 1

    print("─── Underlyings (top 30 por cantidad) ───────────────────────────────────")
    for u, n in underlyings.most_common(30):
        print(f"  {n:6d}  {u!r}")

    print("\n─── Categorías CFI (primer char del cficode) ────────────────────────────")
    cfi_meanings = {
        "E": "Equities (acciones, ETFs, CEDEARs)",
        "D": "Debt (bonos, lecaps, ONs)",
        "F": "Futures",
        "O": "Options",
        "R": "Repos / cauciones",
        "M": "Mutual / others",
    }
    for c, n in sorted(cficodes_cat.items(), key=lambda x: -x[1]):
        print(f"  {n:6d}  {c!r}  {cfi_meanings.get(c, '?')}")

    print("\n─── Top 20 cficodes COMPLETOS (4 chars) ─────────────────────────────────")
    for cf, n in cficodes_full.most_common(20):
        print(f"  {n:6d}  {cf!r}")

    # ─────────────────────────────────────────────────────────────────────────
    # 2. Futuros vigentes — agrupados por underlying
    # ─────────────────────────────────────────────────────────────────────────
    print("\n─── FUTUROS (cficode empieza con 'F') ───────────────────────────────────")
    futuros = [i for i in instrumentos if _str(_safe(i, "cficode")).startswith("F")]
    print(f"Total futuros (todos): {len(futuros)}\n")

    hoy_str = datetime.now().strftime("%Y%m%d")
    futuros_por_und: dict[str, list] = defaultdict(list)
    for f in futuros:
        futuros_por_und[_str(_safe(f, "underlying")) or "?"].append(f)

    for und in sorted(futuros_por_und.keys()):
        items = futuros_por_und[und]
        vigentes = [i for i in items if _maturity(i) > hoy_str]
        if not vigentes:
            print(f"  underlying={und!r}: {len(items)} totales, 0 vigentes (todos vencidos)")
            continue
        print(f"  underlying={und!r}: {len(items)} totales, {len(vigentes)} vigentes")
        for inst in vigentes[:12]:  # primeros 12
            sym = _ticker(inst) or "(sin ticker)"
            cf = _str(_safe(inst, "cficode")) or "?"
            mat = _maturity(inst)
            print(f"      {sym}  |  cficode={cf}  |  maturity={mat}")
        if len(vigentes) > 12:
            print(f"      ... ({len(vigentes) - 12} más)")
        print()

    # ─────────────────────────────────────────────────────────────────────────
    # 3. Caución / repo (heurístico)
    # ─────────────────────────────────────────────────────────────────────────
    print("\n─── CAUCIONES (cficode 'R...' o '1D' en el symbol) ──────────────────────")
    cauciones = [
        i for i in instrumentos
        if _str(_safe(i, "cficode")).startswith("R")
        or "1D" in _ticker(i).upper()
    ]
    print(f"Total candidatos: {len(cauciones)}\n")

    # Agrupar por underlying para entender mejor
    cauciones_por_und: dict[str, list] = defaultdict(list)
    for c in cauciones:
        cauciones_por_und[_str(_safe(c, "underlying")) or "?"].append(c)

    for und in sorted(cauciones_por_und.keys()):
        items = cauciones_por_und[und]
        print(f"\n  underlying={und!r}: {len(items)} instrumentos")
        for inst in items[:8]:
            sym = _ticker(inst) or "(sin ticker)"
            cf = _str(_safe(inst, "cficode")) or "?"
            mat = _maturity(inst)
            print(f"      {sym}  |  cficode={cf}  |  maturity={mat}")
        if len(items) > 8:
            print(f"      ... ({len(items) - 8} más)")

    # ─────────────────────────────────────────────────────────────────────────
    # 4. Sample MD: futuros DLR
    # ─────────────────────────────────────────────────────────────────────────
    print("\n─── SAMPLE: get_market_data sobre un futuro Dólar A3500 ─────────────────")
    # Buscamos por underlying en vez de por substring del symbol — el discovery
    # confirmó que ROFEX usa "Dólar USA A3500" como underlying de los DLR.
    dlr_candidatos = [
        i for i in instrumentos
        if _str(_safe(i, "underlying")) == "Dólar USA A3500"
        and _str(_safe(i, "cficode")).startswith("F")
        and _maturity(i) > hoy_str
    ]
    if not dlr_candidatos:
        print("  No encontré futuros con underlying='Dólar USA A3500' y vigentes.")
    else:
        # Tomamos el de vencimiento más cercano
        dlr_candidatos.sort(key=_maturity)
        _print_md_sample(_ticker(dlr_candidatos[0]), "DLR cercano")

    # ─────────────────────────────────────────────────────────────────────────
    # 5. Sample MD: caución pesos / dólares
    # ─────────────────────────────────────────────────────────────────────────
    print("\n─── SAMPLE: get_market_data sobre caución pesos a 1 día ─────────────────")
    # Filtramos por underlying = 'Cauciones Pesos' y maturity más cercano (= 1 día).
    cauciones_ars = [
        i for i in instrumentos
        if _str(_safe(i, "underlying")) == "Cauciones Pesos"
    ]
    if not cauciones_ars:
        print("  No encontré cauciones con underlying='Cauciones Pesos'.")
    else:
        cauciones_ars.sort(key=_maturity)
        # La de maturity más temprana suele ser la 1D
        _print_md_sample(_ticker(cauciones_ars[0]), "caución ARS 1D")

    print("\n─── SAMPLE: get_market_data sobre caución dólares a 1 día ──────────────")
    cauciones_usd = [
        i for i in instrumentos
        if _str(_safe(i, "underlying")) == "Cauciones USD"
    ]
    if not cauciones_usd:
        print("  No encontré cauciones con underlying='Cauciones USD'.")
    else:
        cauciones_usd.sort(key=_maturity)
        _print_md_sample(_ticker(cauciones_usd[0]), "caución USD 1D")

    print("\n" + "=" * 78)
    print("Fin del discovery. Pegale a Claude el output completo.")
    print("=" * 78)


if __name__ == "__main__":
    main()
