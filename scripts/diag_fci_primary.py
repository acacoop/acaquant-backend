"""scripts/diag_fci_primary.py — qué datos de FCI da Primary (pyRofex). SOLO LECTURA.

Herramienta: fci · Diag: qué FCI lista Primary, qué campos trae y si hay histórico

Pregunta que responde ANTES de modelar el mercado FCI: ¿alcanza Primary para
armar la tabla de fondos (VCP de hoy + rendimientos 1D/WTD/MTD/YTD), o hace
falta otra fuente para la serie? Imprime hechos, no decide nada.

  1. Cuántos instrumentos con cficode CIO… (cuotapartes) lista Primary y cómo
     se llaman (símbolo, underlying, moneda, settlType).
  2. Un histograma por primera palabra del underlying (≈ la gerente/marca).
  3. Para N de ellos: el detalle COMPLETO del instrument (todos los campos) y la
     market data que responde Primary (LAST, CLOSE, BIDS/OFFERS, OPEN, HIGH,
     LOW, SETTLEMENT…) → si la banda low/high es el VCP y qué más viene.
  4. Para los mismos N: si `get_trade_history` devuelve algo en los últimos 60
     días → ¿hay SERIE en Primary o solo el dato de hoy? (la pregunta clave).
  5. Cruce con Manager: cuántos símbolos CIO de Primary ya están en
     portafolio.assets.instrumento (cartera FCI) y cuántos assets FCI no
     tienen símbolo.

Uso (en el Droplet, con la sesión pyRofex de config):
    venv/bin/python -m scripts.diag_fci_primary
    venv/bin/python -m scripts.diag_fci_primary --n 8 --dias 120 --filtro schroder
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import date, timedelta

import pyRofex

from core.postgres import get_pool
from core.rofex_session import inicializar_sesion

_CIO = "CIO"


def _sym(inst: dict) -> str | None:
    s = inst.get("symbol")
    if isinstance(s, str) and s:
        return s
    return ((inst.get("instrumentId") or {}).get("symbol")) or None


def _print(titulo: str, obj) -> None:
    print(f"\n── {titulo}")
    print(json.dumps(obj, indent=1, ensure_ascii=False, default=str)[:4000])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--n", type=int, default=5, help="cuántos FCI mirar en detalle")
    ap.add_argument("--dias", type=int, default=60, help="ventana del trade history")
    ap.add_argument("--filtro", default="", help="substring del símbolo/underlying para elegir los N")
    a = ap.parse_args()

    inicializar_sesion()
    res = pyRofex.get_detailed_instruments()
    if not res or res.get("status") != "OK":
        print("get_detailed_instruments falló:", (res or {}).get("status"), (res or {}).get("message"))
        return 1
    todos = res.get("instruments") or []
    fci = [i for i in todos if str(i.get("cficode") or "").startswith(_CIO)]
    print(f"Primary lista {len(todos)} instruments; con cficode CIO… (FCI): {len(fci)}")
    if not fci:
        return 0

    # 1) qué son
    print("\n── Primeros 30 FCI (símbolo · underlying · moneda · settlType · cficode)")
    for i in sorted(fci, key=lambda x: _sym(x) or "")[:30]:
        print(f"  {_sym(i)!s:40} {str(i.get('underlying'))[:34]:34} {i.get('currency')!s:4} "
              f"{i.get('settlType')!s:4} {i.get('cficode')}")
    print("\n── cficode distintos entre los FCI:", Counter(i.get("cficode") for i in fci))
    print("── monedas:", Counter(i.get("currency") for i in fci))
    print("── settlType:", Counter(i.get("settlType") for i in fci))

    # 2) marcas
    marcas = Counter((str(i.get("underlying") or _sym(i) or "").split() or ["?"])[0].upper() for i in fci)
    print("\n── Por primera palabra del underlying (≈ marca / gerente):")
    for k, v in marcas.most_common(60):
        print(f"  {v:4d}  {k}")

    # 3) detalle + market data de N
    f = a.filtro.lower()
    muestra = [i for i in fci if not f or f in (_sym(i) or "").lower() or f in str(i.get("underlying") or "").lower()]
    muestra = sorted(muestra, key=lambda x: _sym(x) or "")[: a.n]
    entries = [pyRofex.MarketDataEntry.LAST, pyRofex.MarketDataEntry.CLOSING_PRICE,
               pyRofex.MarketDataEntry.BIDS, pyRofex.MarketDataEntry.OFFERS,
               pyRofex.MarketDataEntry.OPENING_PRICE, pyRofex.MarketDataEntry.HIGH_PRICE,
               pyRofex.MarketDataEntry.LOW_PRICE, pyRofex.MarketDataEntry.SETTLEMENT_PRICE,
               pyRofex.MarketDataEntry.TRADE_VOLUME, pyRofex.MarketDataEntry.NOMINAL_VOLUME]
    hoy = date.today()
    desde = (hoy - timedelta(days=a.dias)).isoformat()
    for i in muestra:
        s = _sym(i)
        _print(f"DETALLE {s}", i)
        try:
            md = pyRofex.get_market_data(s, entries=entries, depth=1)
            _print(f"MARKET DATA {s}", md)
        except Exception as e:  # el diag sigue con el próximo
            print(f"  market data {s}: {type(e).__name__}: {e}")
        # 4) histórico
        try:
            th = pyRofex.get_trade_history(s, desde, hoy.isoformat())
            trades = (th or {}).get("trades") or []
            print(f"── TRADE HISTORY {s} desde {desde}: status={ (th or {}).get('status') } "
                  f"· {len(trades)} trades")
            if trades:
                print("   primero:", json.dumps(trades[0], default=str)[:300])
                print("   último: ", json.dumps(trades[-1], default=str)[:300])
                fechas = sorted({str(t.get("datetime") or t.get("date") or "")[:10] for t in trades})
                print(f"   días distintos con trade: {len(fechas)} ({fechas[0]} → {fechas[-1]})")
        except Exception as e:
            print(f"  trade history {s}: {type(e).__name__}: {e}")

    # 5) cruce con assets
    syms = {_sym(i) for i in fci if _sym(i)}
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT unidad, instrumento, cafci, emisor FROM portafolio.assets "
                    "WHERE cartera IN ('FCI', 'CARTERA FCI')")
        assets = cur.fetchall()
    con_sym = [r for r in assets if r[1]]
    en_primary = [r for r in con_sym if r[1] in syms]
    print(f"\n── Manager: {len(assets)} assets FCI · {len(con_sym)} con `instrumento` · "
          f"{len(en_primary)} de esos existen hoy en Primary")
    for r in con_sym[:15]:
        print(f"  {'✓' if r[1] in syms else '✗'} {r[1]!s:42} {r[2]!s:16} {str(r[3])[:20]:20} {r[0][:50]}")
    print(f"── assets FCI SIN símbolo Primary: {len(assets) - len(con_sym)} "
          f"(emisores: {Counter(str(r[3]) for r in assets if not r[1]).most_common(12)})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
