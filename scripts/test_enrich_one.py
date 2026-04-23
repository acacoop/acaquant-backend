"""Toma un doc histórico del backfill (GD30D del 2026-01-02) y corre
calcular_campos paso a paso, imprimiendo dónde falla.

Aísla el bug: si retorna None, sabemos exactamente qué línea / por qué.
Si retorna dict válido, el bug está en el loop del motor (no en la
función).

Uso:
    python -m scripts.test_enrich_one
    python -m scripts.test_enrich_one --ticker-corto GD30D --fecha 2026-01-02
"""
from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime, time

from core.mongo import get_mongo_client
from engines.curvas import (
    calcular_campos,
    cargar_cer,
    cargar_dias_habiles,
    cargar_indexado_por_ticker,
    cargar_mep_actual,
    precio_soberano_a_usd,
    siguiente_dia_habil,
)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--ticker-corto", default="GD30D")
    p.add_argument("--fecha", default=None,
                   help="YYYY-MM-DD del trade a testear. Default: el más viejo del backfill.")
    args = p.parse_args()

    c = get_mongo_client()
    ts_col = c["Trading"]["TimeSales"]

    # 1) Resolver ticker completo
    curva_doc = c["Trading"]["Curvas"].find_one(
        {"ticker_corto": args.ticker_corto},
        {"_id": 0, "ticker": 1, "ticker_corto": 1, "curva": 1,
         "fecha_vencimiento": 1, "valor_nominal": 1, "flujos": 1},
    )
    if not curva_doc:
        print(f"FAIL: '{args.ticker_corto}' no está en Trading.Curvas.")
        return 1
    ticker_full = curva_doc["ticker"]
    print(f"Ticker (Curvas): {ticker_full!r}")
    print(f"Curva:           {curva_doc.get('curva')}")
    print(f"Vencimiento:     {curva_doc.get('fecha_vencimiento')}")
    print(f"VN:              {curva_doc.get('valor_nominal')}")
    print(f"Flujos:          {len(curva_doc.get('flujos') or [])} flujos definidos")

    # 2) Buscar doc del backfill
    filtro = {"ticker": ticker_full, "size": 1.0, "side": "MID"}
    if args.fecha:
        d = datetime.strptime(args.fecha, "%Y-%m-%d").date()
        filtro["timestamp"] = {
            "$gte": datetime.combine(d, time(0, 0), tzinfo=UTC),
            "$lt":  datetime.combine(d, time(23, 59, 59), tzinfo=UTC),
        }
    doc = ts_col.find_one(filtro, sort=[("timestamp", 1)])
    if not doc:
        print(f"FAIL: no se encontró doc del backfill para {args.ticker_corto} {args.fecha or '(cualquier fecha)'}.")
        return 1
    print()
    print("Doc del backfill que vamos a procesar:")
    print(f"  timestamp: {doc.get('timestamp')}")
    print(f"  price:     {doc.get('price')}")
    print(f"  size:      {doc.get('size')}")
    print(f"  side:      {doc.get('side')}")
    print(f"  duration:  {doc.get('duration')}  ← {'YA ENRIQUECIDO' if 'duration' in doc else 'sin duration'}")
    print(f"  TEA:       {doc.get('TEA')}")

    # 3) Cargar dependencias del motor (mismas funciones)
    print()
    print("Cargando dependencias del motor (CER, DiasHabiles, MEP)...")
    curvas_idx = cargar_indexado_por_ticker()
    cer_dict = cargar_cer(c)
    dias_habiles = cargar_dias_habiles(c)
    mep_actual = cargar_mep_actual(c)
    print(f"  Curvas indexadas: {len(curvas_idx)} (¿este ticker está? {ticker_full in curvas_idx})")
    print(f"  CER fechas:       {len(cer_dict)}")
    print(f"  DiasHabiles:      {len(dias_habiles)}  (rango {dias_habiles[0] if dias_habiles else '?'} → {dias_habiles[-1] if dias_habiles else '?'})")
    print(f"  MEP actual:       {mep_actual}")

    # 4) Replicar el flujo del motor para soberanos
    fecha_trade = doc["timestamp"].date()
    print()
    print(f"Fecha del trade: {fecha_trade}")
    print(f"  ¿Está en DiasHabiles? {fecha_trade.isoformat() in dias_habiles}")
    sig = siguiente_dia_habil(dias_habiles, fecha_trade)
    print(f"  siguiente_dia_habil → {sig}")

    precio_usd = precio_soberano_a_usd(doc["price"], ticker_full, mep_actual)
    print(f"  precio_soberano_a_usd({doc['price']}, '{ticker_full}', {mep_actual}) → {precio_usd}")

    # 5) Llamar calcular_campos directamente
    print()
    print("Llamando calcular_campos...")
    instrumento = curvas_idx.get(ticker_full)
    if not instrumento:
        print(f"  FAIL: el ticker no está en curvas_idx (cargar_indexado_por_ticker).")
        print(f"  → motor_curvas haría 'continue' silenciosamente.")
        return 1

    resultado = calcular_campos(doc, instrumento, cer_dict, dias_habiles, mep_actual)
    print(f"  → {resultado}")
    if resultado is None:
        print()
        print("BUG CONFIRMADO: calcular_campos retorna None para este doc histórico.")
        print("El doc queda eternamente en la cola (duration: $exists: false).")
        return 1

    print()
    print("OK: calcular_campos devuelve resultado válido. El bug está en otra parte.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
