"""Backfill histórico de Trading.ForwardsHistorico.

Reconstruye, para cada día del rango, la matriz NxN de tasas forward de
una curva tomando el último trade enriquecido (con TEA + duration) por
ticker en ese día. Usa la misma `engines.forwards.calcular_matriz()` que
el motor live, así garantizamos paridad de cálculo.

Idempotente: upsert por (curva, fecha). Re-correr no duplica ni pisa
fechas de hoy con datos parciales (`--saltear-hoy` activo por default).

Uso típico (después del backfill_timesales_csv para soberanos):

    python -m jobs.backfill_forwards --curva soberanos \\
        --desde 2026-01-02 --hasta 2026-04-21

    # Sin --desde / --hasta usa el rango disponible en TimeSales (con TEA)
    python -m jobs.backfill_forwards --curva soberanos

    # Preview sin escribir
    python -m jobs.backfill_forwards --curva soberanos --dry
"""
from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta

from core.mongo import get_mongo_client
from engines._curvas_loader import cargar_por_curva
from engines.forwards import calcular_matriz


def _parse_fecha(s: str) -> date | None:
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def _ultimas_teas_por_dia(client, tickers: list[str], desde: date, hasta: date) -> dict[date, dict[str, dict]]:
    """Para cada día y ticker, último (TEA, duration) <= ese día.

    Devuelve {fecha: {ticker_full: {TEA, duration}}}.

    NOTA: usa el último trade DE ESE DÍA. Si un bono no operó ese día,
    no aparece en el snapshot. Eso es consistente con cómo guarda
    ForwardsHistorico durante el día (snapshot con lo que hay).
    """
    inicio = datetime.combine(desde, datetime.min.time(), tzinfo=UTC)
    fin = datetime.combine(hasta + timedelta(days=1), datetime.min.time(), tzinfo=UTC)
    pipeline = [
        {"$match": {
            "ticker":    {"$in": tickers},
            "TEA":       {"$exists": True},
            "duration":  {"$exists": True},
            "timestamp": {"$gte": inicio, "$lt": fin},
        }},
        {"$addFields": {
            "fecha": {"$dateToString": {"format": "%Y-%m-%d", "date": "$timestamp"}},
        }},
        {"$sort": {"timestamp": -1}},
        {"$group": {
            "_id":      {"ticker": "$ticker", "fecha": "$fecha"},
            "TEA":      {"$first": "$TEA"},
            "duration": {"$first": "$duration"},
        }},
    ]
    out: dict[date, dict[str, dict]] = defaultdict(dict)
    for r in client["Trading"]["TimeSales"].aggregate(pipeline):
        f = _parse_fecha(r["_id"]["fecha"])
        if f is None:
            continue
        out[f][r["_id"]["ticker"]] = {"TEA": r["TEA"], "duration": r["duration"]}
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--curva", required=True,
                        help="Curva a backfillear (cer | tasa_fija | soberanos | tamar)")
    parser.add_argument("--desde", help="YYYY-MM-DD (inclusivo)")
    parser.add_argument("--hasta", help="YYYY-MM-DD (inclusivo)")
    parser.add_argument("--incluir-hoy", action="store_true",
                        help="Por default no se reescribe la fecha de hoy "
                             "(la maneja el motor live).")
    parser.add_argument("--dry", action="store_true")
    args = parser.parse_args()

    client = get_mongo_client()

    grupos = cargar_por_curva()
    if args.curva not in grupos:
        raise SystemExit(
            f"Curva '{args.curva}' no existe. Disponibles: {list(grupos.keys())}"
        )
    instrumentos = grupos[args.curva]
    tickers_full = [i["ticker"] for i in instrumentos if i.get("ticker")]
    if not tickers_full:
        raise SystemExit(f"Curva '{args.curva}' sin tickers en Trading.Curvas.")

    desde = _parse_fecha(args.desde) if args.desde else None
    hasta = _parse_fecha(args.hasta) if args.hasta else None

    # Si no se pasó rango, autodescubrir desde TimeSales con TEA+duration.
    if not desde or not hasta:
        bounds = list(client["Trading"]["TimeSales"].aggregate([
            {"$match": {"ticker": {"$in": tickers_full},
                        "TEA": {"$exists": True},
                        "duration": {"$exists": True}}},
            {"$group": {"_id": None,
                        "min": {"$min": "$timestamp"},
                        "max": {"$max": "$timestamp"}}},
        ]))
        if not bounds:
            raise SystemExit(f"No hay trades enriquecidos para '{args.curva}'.")
        b = bounds[0]
        desde = desde or b["min"].date()
        hasta = hasta or b["max"].date()

    if desde > hasta:
        raise SystemExit(f"--desde {desde} > --hasta {hasta}")

    hoy = datetime.now(UTC).date()
    print(f"Curva:     {args.curva}")
    print(f"Tickers:   {len(tickers_full)} en Trading.Curvas")
    print(f"Rango:     {desde} → {hasta}")
    print(f"Saltea hoy: {'no' if args.incluir_hoy else 'sí'}")

    teas_por_dia = _ultimas_teas_por_dia(client, tickers_full, desde, hasta)
    if not teas_por_dia:
        raise SystemExit("No hay TEAs por día — ¿falta el enrichment de motor_curvas?")

    fechas_ord = sorted(teas_por_dia.keys())
    print(f"Días con datos: {len(fechas_ord)} ({fechas_ord[0]} → {fechas_ord[-1]})\n")

    col_hist = client["Trading"]["ForwardsHistorico"]
    nuevos = 0
    pisados = 0
    saltados_pocos = 0
    saltados_hoy = 0

    for fecha in fechas_ord:
        if not args.incluir_hoy and fecha == hoy:
            saltados_hoy += 1
            continue

        tasas_tea = teas_por_dia[fecha]
        ordered, tasas, matrix = calcular_matriz(instrumentos, tasas_tea)
        if len(ordered) < 2:
            # Con un solo bono no tiene sentido escribir histórico de
            # forwards (no hay matriz). Saltamos silenciosamente.
            saltados_pocos += 1
            continue

        ts_dia = datetime.combine(fecha, datetime.min.time(), tzinfo=UTC).replace(
            hour=20, minute=0, second=0,
        )
        fecha_str = fecha.isoformat()
        doc = {
            "curva":      args.curva,
            "fecha":      fecha_str,
            "updated_at": ts_dia,
            "tickers":    ordered,
            "tasas":      tasas,
            "matrix":     matrix,
        }

        if args.dry:
            nuevos += 1
            if nuevos <= 3:
                print(f"  {fecha_str} → {len(ordered)} bonos · matrix {len(matrix)}x{len(ordered)}")
            continue

        res = col_hist.update_one(
            {"curva": args.curva, "fecha": fecha_str},
            {"$set": doc},
            upsert=True,
        )
        if res.upserted_id is not None:
            nuevos += 1
        elif res.modified_count > 0:
            pisados += 1

    print()
    print(f"OK: {nuevos} nuevos · {pisados} pisados · "
          f"{saltados_pocos} saltados (<2 bonos) · {saltados_hoy} saltados hoy")
    if args.dry:
        print("(--dry: no se escribió en Mongo)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
