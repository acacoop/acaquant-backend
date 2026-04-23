"""Diagnóstico: por qué motor_curvas no enriqueció los docs del backfill.

Compara los tickers del backfill (size=1, side='MID', timestamp pasado)
contra los tickers indexados por motor_curvas desde Trading.Curvas.
Reporta:

- cuántos docs del backfill hay por ticker,
- si cada ticker del backfill matchea un doc en Trading.Curvas,
- si hay diferencias sutiles de string (espacios, caso, etc),
- cuántos del backfill ya tienen duration vs cuántos no.

Uso:
    python -m scripts.diagnose_backfill_enrich
"""
from __future__ import annotations

from collections import Counter

from core.mongo import get_mongo_client


def main() -> int:
    c = get_mongo_client()
    ts_col = c["Trading"]["TimeSales"]
    curvas_col = c["Trading"]["Curvas"]

    # Tickers del backfill: size=1.0, side='MID' (firma del backfill_timesales_csv).
    pipeline = [
        {"$match": {"size": 1.0, "side": "MID"}},
        {"$group": {"_id": "$ticker",
                    "n":             {"$sum": 1},
                    "n_con_dur":     {"$sum": {"$cond": [{"$ifNull": ["$duration", False]}, 1, 0]}},
                    "n_con_tea":     {"$sum": {"$cond": [{"$ifNull": ["$TEA", False]}, 1, 0]}},
                    "min_ts":        {"$min": "$timestamp"},
                    "max_ts":        {"$max": "$timestamp"}}},
        {"$sort": {"_id": 1}},
    ]
    backfill_docs = list(ts_col.aggregate(pipeline))

    if not backfill_docs:
        print("No se encontraron docs del backfill (size=1, side='MID').")
        return 1

    # Tickers indexados por Trading.Curvas
    curvas_tickers = {
        d["ticker"]: d
        for d in curvas_col.find({}, {"_id": 0, "ticker": 1, "ticker_corto": 1, "curva": 1})
        if d.get("ticker")
    }

    print(f"Trading.Curvas: {len(curvas_tickers)} tickers indexados")
    print(f"Backfill encontrado: {len(backfill_docs)} tickers únicos")
    print("=" * 100)
    print(f"{'Ticker (del backfill)':<55}{'docs':>6}{'cDur':>6}{'cTEA':>6}  {'en Curvas?':<14}  rango")
    print("-" * 100)

    total = 0
    total_dur = 0
    total_tea = 0
    sin_match: list[str] = []
    con_match_sin_dur: list[str] = []

    for r in backfill_docs:
        tk = r["_id"]
        en_curvas = tk in curvas_tickers
        ts_min = r["min_ts"].date() if r.get("min_ts") else "?"
        ts_max = r["max_ts"].date() if r.get("max_ts") else "?"
        marca = "OK" if en_curvas else "FALTA"
        print(f"{tk:<55}{r['n']:>6}{r['n_con_dur']:>6}{r['n_con_tea']:>6}  {marca:<14}  {ts_min} → {ts_max}")
        total += r["n"]
        total_dur += r["n_con_dur"]
        total_tea += r["n_con_tea"]
        if not en_curvas:
            sin_match.append(tk)
        elif r["n_con_dur"] < r["n"]:
            con_match_sin_dur.append(tk)

    print("-" * 100)
    print(f"TOTAL backfill:     {total} docs")
    print(f"  con duration:     {total_dur}")
    print(f"  sin duration:     {total - total_dur}")
    print(f"  con TEA:          {total_tea}")
    print()

    if sin_match:
        print("DIAGNOSTICO #1: estos tickers del backfill NO existen en Trading.Curvas")
        print("                → motor_curvas los IGNORA silenciosamente (línea 507-509).")
        for tk in sin_match:
            # Buscar tickers similares en Curvas (por substring del ticker_corto)
            short = tk.split(" - ")[2] if " - " in tk else tk
            similares = [
                k for k, v in curvas_tickers.items()
                if v.get("ticker_corto", "").upper() == short.upper()
                or short.upper() in k.upper()
            ]
            if similares:
                print(f"   '{tk}'")
                print(f"        ↳ posible match real en Curvas:")
                for s in similares:
                    print(f"          '{s}'  (ticker_corto={curvas_tickers[s].get('ticker_corto')!r}, curva={curvas_tickers[s].get('curva')!r})")
                # Diferencia byte-por-byte para detectar espacios raros
                if len(similares) == 1:
                    real = similares[0]
                    print(f"        ↳ diff char-by-char (backfill vs Curvas):")
                    for i in range(max(len(tk), len(real))):
                        a = tk[i] if i < len(tk) else "·"
                        b = real[i] if i < len(real) else "·"
                        marca = " " if a == b else " ←"
                        print(f"           pos {i:>2}: backfill={a!r} ({ord(a) if a != '·' else '-'}) | curvas={b!r} ({ord(b) if b != '·' else '-'}){marca}")
            else:
                print(f"   '{tk}'  → no hay match parecido en Trading.Curvas")
        print()

    if con_match_sin_dur:
        print("DIAGNOSTICO #2: estos tickers SÍ están en Curvas pero algunos docs no se enriquecieron")
        print("                → posible bug en calcular_campos (xirr no converge para fechas históricas)")
        for tk in con_match_sin_dur:
            print(f"   '{tk}'")
        print()

    # Comparación de longitudes / Counter de chars en headers para deteccion masiva de \xa0 etc
    chars_backfill = Counter()
    for r in backfill_docs:
        for ch in r["_id"]:
            chars_backfill[ord(ch)] += 1
    chars_curvas = Counter()
    for tk in curvas_tickers:
        for ch in tk:
            chars_curvas[ord(ch)] += 1
    raros_backfill = {ord_ for ord_ in chars_backfill if ord_ > 127 or ord_ == 9}
    raros_curvas = {ord_ for ord_ in chars_curvas if ord_ > 127 or ord_ == 9}
    if raros_backfill or raros_curvas:
        print(f"Caracteres no-ASCII en tickers (sospechoso): backfill={raros_backfill} curvas={raros_curvas}")

    if not sin_match and not con_match_sin_dur:
        print("Todos los tickers del backfill matchean Curvas y todos los docs ya tienen duration.")
        print("Si igual ves pocos días con datos, el problema es otro (probablemente xirr).")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
