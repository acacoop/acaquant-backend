"""
check_tasa_fija.py — Diagnóstico de instrumentos tasa_fija.

Para cada ticker_corto en Trading.Curvas (curva=tasa_fija) muestra:
  ✅ aparece en la vista  (tiene Assets + posición en AuM)
  ⚠️  sin posición        (tiene Assets pero no está en AuM)
  ❌ sin Assets           (no hay doc en Valuaciones.Assets con TICKER == ticker_corto)

Uso:
    python check_tasa_fija.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.mongo import get_mongo_client


def main():
    client = get_mongo_client()
    db_trading = client["Trading"]
    db_val     = client["Valuaciones"]

    # 1. Todos los tasa_fija de Curvas
    curvas = list(db_trading["Curvas"].find(
        {"curva": "tasa_fija"},
        {"_id": 0, "ticker_corto": 1, "fecha_vencimiento": 1}
    ))
    if not curvas:
        print("No hay instrumentos con curva=tasa_fija en Trading.Curvas.")
        return

    # 2. Assets: TICKER -> unidad
    assets_docs = list(db_val["Assets"].find({}, {"_id": 0, "TICKER": 1, "unidad": 1}))
    ticker_to_unidades = {}
    for a in assets_docs:
        t = a.get("TICKER", "")
        if t:
            ticker_to_unidades.setdefault(t, []).append(a["unidad"])

    # 3. AuM: unidades con posición (ultima fecha)
    ultima_fecha = db_val["AuM"].find_one(sort=[("fecha_snapshot", -1)], projection={"fecha_snapshot": 1})
    fecha_max = ultima_fecha["fecha_snapshot"] if ultima_fecha else None
    aum_docs = list(db_val["AuM"].find(
        {"fecha_snapshot": fecha_max},
        {"_id": 0, "unidad": 1, "valuacion": 1, "cuenta": 1}
    )) if fecha_max else []
    unidades_con_posicion = {d["unidad"] for d in aum_docs if (d.get("valuacion") or 0) != 0}

    print(f"\nFecha snapshot: {fecha_max}")
    print(f"Instrumentos tasa_fija en Curvas: {len(curvas)}\n")
    print(f"{'Ticker':<12}  {'Estado'}")
    print("-" * 50)

    ok = sin_pos = sin_assets = 0
    for c in sorted(curvas, key=lambda x: x.get("ticker_corto", "")):
        tc = c["ticker_corto"]
        unidades = ticker_to_unidades.get(tc, [])

        if not unidades:
            estado = "❌  sin Assets"
            sin_assets += 1
        elif any(u in unidades_con_posicion for u in unidades):
            estado = "✅  en vista"
            ok += 1
        else:
            estado = "⚠️  sin posición en AuM"
            sin_pos += 1

        print(f"{tc:<12}  {estado}")

    print("-" * 50)
    print(f"✅ En vista: {ok}   ⚠️ Sin posición: {sin_pos}   ❌ Sin Assets: {sin_assets}\n")

if __name__ == "__main__":
    main()
