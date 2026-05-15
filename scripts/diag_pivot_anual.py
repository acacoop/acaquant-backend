"""diag_pivot_anual.py — debug del pivot point ANUAL de un ticker.

El usuario reporta que el pivot anual de MSFT muestra ~20% de distancia
cuando su calculo manual da ~10% (factor 2x sospechoso). Este script
muestra paso a paso de donde sale cada numero:

  - rango anual usado (desde / hasta)
  - todas las velas del rango (o un sample) con su OHLC
  - de que fecha viene el H maximo y el L minimo (detecta outliers / saltos
    de escala de precio)
  - PP / R1-R3 / S1-S3 calculados
  - last price + su fecha
  - distancia % de cada nivel al last

NO modifica nada. Solo reporta.

Corre:  python -m scripts.diag_pivot_anual [TICKER]
        (default MSFT — pasa otro ticker como argumento)
"""
from __future__ import annotations

import sys

from core.mongo import get_mongo_client
from quant.pivot_points import _rango_anual_previo, calcular, obtener_4_timeframes


def main() -> None:
    ticker = (sys.argv[1] if len(sys.argv) > 1 else "MSFT").upper()
    print("=" * 72)
    print(f"DEBUG PIVOT ANUAL — {ticker}")
    print("=" * 72)

    col = get_mongo_client()["Trading"]["PreciosAcciones"]

    # ── Rango anual ────────────────────────────────────────────────────
    desde, hasta = _rango_anual_previo()
    print(f"\nRango anual usado: [{desde.date()}, {hasta.date()})  (= ano calendario previo)")

    docs = list(col.find(
        {"ticker": ticker, "fecha": {"$gte": desde, "$lt": hasta}},
        {"_id": 0, "fecha": 1, "open": 1, "high": 1, "low": 1, "close": 1},
        sort=[("fecha", 1)],
    ))
    print(f"Velas en el rango: {len(docs)}")
    if not docs:
        print("!! SIN DATOS para este ticker en el rango anual.")
        print("   Proba con el ticker como aparece en Trading.PreciosAcciones.")
        # listar algunos tickers disponibles
        muestra = col.distinct("ticker")[:30]
        print(f"   tickers disponibles (sample): {muestra}")
        return

    print("\nPrimeras 3 velas del rango:")
    for d in docs[:3]:
        print(f"   {str(d['fecha'])[:10]}  O={d.get('open')} H={d.get('high')} "
              f"L={d.get('low')} C={d.get('close')}")
    print("Ultimas 3 velas del rango:")
    for d in docs[-3:]:
        print(f"   {str(d['fecha'])[:10]}  O={d.get('open')} H={d.get('high')} "
              f"L={d.get('low')} C={d.get('close')}")

    highs = [d["high"] for d in docs if d.get("high") is not None]
    lows = [d["low"] for d in docs if d.get("low") is not None]
    closes = [d["close"] for d in docs if d.get("close") is not None]
    if not highs or not lows or not closes:
        print("!! Faltan high/low/close en los docs.")
        return

    H, L, C = max(highs), min(lows), closes[-1]
    vela_h = max((d for d in docs if d.get("high") is not None),
                 key=lambda d: d["high"])
    vela_l = min((d for d in docs if d.get("low") is not None),
                 key=lambda d: d["low"])

    print(f"\nH (max high) = {H}      <- vela del {str(vela_h['fecha'])[:10]}")
    print(f"L (min low)  = {L}      <- vela del {str(vela_l['fecha'])[:10]}")
    print(f"C (close del ultimo doc del rango) = {C}  "
          f"<- {str(docs[-1]['fecha'])[:10]}")

    # Chequeo de escala: si H/L difieren en >5x es sospechoso (CEDEAR ARS
    # mezclado con USD, split, cambio de ratio).
    if L > 0 and H / L > 5:
        print(f"\n!! ALERTA: H/L = {H / L:.1f}x — posible mezcla de escalas "
              f"(ARS vs USD), split o cambio de ratio de CEDEAR en la serie.")

    levels = calcular(high=H, low=L, close=C)
    print("\nPivot levels (Floor Trader):")
    for k in ("r3", "r2", "r1", "pp", "s1", "s2", "s3"):
        print(f"   {k.upper():4} = {levels[k]:.4f}")

    # ── Last price ─────────────────────────────────────────────────────
    last_doc = col.find_one(
        {"ticker": ticker},
        {"_id": 0, "fecha": 1, "close": 1},
        sort=[("fecha", -1)],
    )
    last = last_doc.get("close") if last_doc else None
    last_fecha = last_doc.get("fecha") if last_doc else None
    print(f"\nLAST price = {last}   (fecha {str(last_fecha)[:10]})")

    if last:
        print("\nDistancia precio actual -> nivel  ((nivel/last - 1) * 100):")
        for k in ("r3", "r2", "r1", "pp", "s1", "s2", "s3"):
            dist = (levels[k] / last - 1) * 100
            print(f"   {k.upper():4} = {levels[k]:9.2f}   {dist:+7.2f}%")

    # ── Lo que devuelve el endpoint real ───────────────────────────────
    print()
    print("=" * 72)
    print("obtener_4_timeframes() — exactamente lo que recibe el frontend")
    print("=" * 72)
    res = obtener_4_timeframes(ticker)
    print(f"last={res.get('last')}   last_fecha={str(res.get('last_fecha'))[:10]}")
    anual = res.get("frames", {}).get("anual")
    if anual:
        print(f"frame ANUAL: h={anual['h']} l={anual['l']} c={anual['c']} "
              f"n_velas={anual['n_velas']}")
        print(f"  fecha_desde={str(anual['fecha_desde'])[:10]} "
              f"fecha_hasta={str(anual['fecha_hasta'])[:10]}")
        print(f"  levels={anual['levels']}")
    else:
        print("frame ANUAL: None (sin datos)")


if __name__ == "__main__":
    main()
