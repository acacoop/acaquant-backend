"""
check_cer_valuacion.py — Muestra el CER utilizado para valuar cada bono CER.

Para cada bono CER en Trading.Curvas busca el último trade enriquecido en
TimeSales y reconstruye qué fecha/valor de CER se usó (settlement − 10 días hábiles).

Uso:
    python check_cer_valuacion.py
"""

from datetime import date, timedelta

from core.mongo import get_mongo_client


def get_cer_en_fecha(cer_dict, fecha_date):
    for i in range(7):
        key = (fecha_date - timedelta(days=i)).isoformat()
        if key in cer_dict:
            return key, cer_dict[key]
    return None, None


def get_cer_liquidacion(cer_dict, dias_habiles, fecha_str, n=10):
    """Retrocede n días hábiles desde fecha_str y retorna (fecha_cer, valor_cer)."""
    idx = None
    for i, f in enumerate(dias_habiles):
        if f <= fecha_str:
            idx = i
    if idx is None or idx < n:
        return None, None
    fecha_n = date.fromisoformat(dias_habiles[idx - n])
    return get_cer_en_fecha(cer_dict, fecha_n)


def siguiente_dia_habil(dias_habiles, fecha_date):
    fecha_str = fecha_date.isoformat()
    for f in dias_habiles:
        if f > fecha_str:
            return f
    return None


def main():
    client = get_mongo_client()
    db = client["Trading"]

    # Cargar bonos CER
    curvas = list(db["Curvas"].find({"curva": "cer"}, {
        "ticker": 1, "ticker_corto": 1, "cer_emision": 1,
        "valor_nominal": 1, "fecha_vencimiento": 1
    }))
    if not curvas:
        print("No se encontraron instrumentos CER en Trading.Curvas")
        return

    tickers = [c["ticker"] for c in curvas]

    # Cargar CER
    cer_docs = list(db["CER"].find({}, {"fecha": 1, "valor": 1}))
    cer_dict = {d["fecha"]: float(d["valor"]) for d in cer_docs}
    cer_fechas_sorted = sorted(cer_dict.keys())

    # Cargar días hábiles
    dh_docs = list(db["DiasHabiles"].find({}, {"fecha": 1, "_id": 0}))
    dias_habiles = sorted(d["fecha"] for d in dh_docs)

    print(f"CER: {len(cer_dict)} fechas | Último: {cer_fechas_sorted[-1]} = {cer_dict[cer_fechas_sorted[-1]]:.6f}")
    print(f"Días hábiles cargados: {len(dias_habiles)}")
    print(f"Bonos CER: {len(curvas)}\n")

    # Por cada bono, buscar el último trade enriquecido
    col_ts = db["TimeSales"]
    instrumento_map = {c["ticker"]: c for c in curvas}

    filas = []
    for ticker, instr in sorted(instrumento_map.items()):
        doc = col_ts.find_one(
            {"ticker": ticker, "duration": {"$exists": True}},
            sort=[("timestamp", -1)]
        )
        if not doc:
            filas.append({
                "ticker": ticker,
                "ticker_corto": instr.get("ticker_corto", ""),
                "trade_ts": None,
                "settlement": None,
                "cer_fecha": None,
                "cer_valor": None,
                "cer_emision": instr.get("cer_emision"),
                "ratio": None,
                "paridad": None,
            })
            continue

        ts = doc["timestamp"]
        fecha_trade = ts.date() if hasattr(ts, "date") else date.fromisoformat(str(ts)[:10])

        settlement_str = siguiente_dia_habil(dias_habiles, fecha_trade)
        if not settlement_str:
            cer_fecha, cer_valor = None, None
        else:
            cer_fecha, cer_valor = get_cer_liquidacion(cer_dict, dias_habiles, settlement_str)

        cer_emision = instr.get("cer_emision")
        ratio = (cer_valor / cer_emision) if (cer_valor and cer_emision) else None

        filas.append({
            "ticker": ticker,
            "ticker_corto": instr.get("ticker_corto", ""),
            "trade_ts": ts.strftime("%Y-%m-%d %H:%M:%S") if hasattr(ts, "strftime") else str(ts),
            "settlement": settlement_str,
            "cer_fecha": cer_fecha,
            "cer_valor": cer_valor,
            "cer_emision": cer_emision,
            "ratio": ratio,
            "paridad": doc.get("paridad"),
        })

    # Imprimir tabla
    header = f"{'Ticker':<20} {'Corto':<10} {'Último trade':<20} {'Settlement':<12} {'CER fecha':<12} {'CER valor':>12} {'CER emisión':>12} {'Ratio':>8} {'Paridad':>9}"
    print(header)
    print("-" * len(header))

    for f in filas:
        if f["trade_ts"] is None:
            print(f"{f['ticker']:<20} {f['ticker_corto']:<10}  -- sin trades enriquecidos --")
            continue
        cer_val_str = f"{f['cer_valor']:.6f}" if f["cer_valor"] else "N/A"
        cer_emi_str = f"{f['cer_emision']:.6f}" if f["cer_emision"] else "N/A"
        ratio_str   = f"{f['ratio']:.6f}"        if f["ratio"]      else "N/A"
        paridad_str = f"{f['paridad']:.2f}"       if f["paridad"]    else "N/A"
        print(
            f"{f['ticker']:<20} {f['ticker_corto']:<10} {f['trade_ts']:<20} "
            f"{f['settlement'] or 'N/A':<12} {f['cer_fecha'] or 'N/A':<12} "
            f"{cer_val_str:>12} {cer_emi_str:>12} {ratio_str:>8} {paridad_str:>9}"
        )

    print("\nNota: ratio = CER_liq / CER_emision | paridad = precio / (VN × ratio) × 100")


if __name__ == "__main__":
    main()
