"""options_rollup.py — rollup diario de Opciones.Data (Mongo) → mercado.options_data_hist (SQL).

Una fila por (fecha, symbol) en SQL, con un jsonb `data` que contiene el doc completo:
  fecha, symbol, tipo, strike
  high (max), low (min > 0), last (último tick cronológico)
  ev (max, = acumulado final del día)
  delta, gamma, vega, theta, iv, spot (del último tick)

SQL-NATIVE (cutover 2026-06-24): la fuente `Opciones.Data` (tick stream intradía) SIGUE en
Mongo, pero el rollup ya NO se escribe a Mongo `Opciones.DataHistorica` (migrada → dropeada).
Escribe directo a `mercado.options_data_hist` (write_native, upsert por fecha+symbol;
`fecha` text 'YYYY-MM-DD'). Lo lee api/services/opciones_sql.get_griegas_historico.

Modos:
  --fecha YYYY-MM-DD    procesa solo ese día (upsert idempotente)
  --backfill            procesa todos los días con datos en Opciones.Data
  (sin flag)            procesa el día UTC actual — uso del cron

Cron: 20:15 UTC L-V (17:15 ART).
"""

import argparse
from datetime import date, datetime, timedelta

from core.mongo import get_mongo_client
from core.pg_mirror import write_native


def _dia_utc(d: date):
    """Devuelve (start, end) en UTC para agrupar un día calendario."""
    start = datetime(d.year, d.month, d.day)
    end   = start + timedelta(days=1)
    return start, end


def _pipeline(start, end):
    """Rollup por symbol para el rango [start, end)."""
    return [
        {"$match": {"timestamp": {"$gte": start, "$lt": end}}},
        {"$sort":  {"timestamp": 1}},  # para que $last tome el tick final del día
        {"$group": {
            "_id":    "$symbol",
            "tipo":   {"$first": "$tipo"},
            "strike": {"$first": "$strike"},
            "high":   {"$max": "$high"},
            "low":    {"$min": {"$cond": [{"$gt": ["$low", 0]}, "$low", None]}},
            "last":   {"$last":  "$last"},
            "ev":     {"$max":   "$ev"},
            "delta":  {"$last":  "$delta"},
            "gamma":  {"$last":  "$gamma"},
            "vega":   {"$last":  "$vega"},
            "theta":  {"$last":  "$theta"},
            "iv":     {"$last":  "$iv"},
            "spot":   {"$last":  "$spot"},
        }},
    ]


def procesar_dia(client, d: date):
    start, end = _dia_utc(d)
    src = client["Opciones"]["Data"]
    fecha_str = d.strftime("%Y-%m-%d")

    rows = list(src.aggregate(_pipeline(start, end)))
    if not rows:
        print(f"  {fecha_str}: sin datos")
        return 0

    # SQL-NATIVE: cada fila lleva el doc completo dentro del jsonb `data` (mismo shape que
    # producía el sync desde Mongo: fecha+symbol redundan adentro, los lee opciones_sql).
    pg_rows = []
    for r in rows:
        doc = {
            "fecha":  fecha_str,
            "symbol": r["_id"],
            "tipo":   r.get("tipo"),
            "strike": r.get("strike"),
            "high":   r.get("high"),
            "low":    r.get("low"),
            "last":   r.get("last"),
            "ev":     r.get("ev"),
            "delta":  r.get("delta"),
            "gamma":  r.get("gamma"),
            "vega":   r.get("vega"),
            "theta":  r.get("theta"),
            "iv":     r.get("iv"),
            "spot":   r.get("spot"),
        }
        pg_rows.append({"fecha": fecha_str, "symbol": doc["symbol"], "data": doc})

    write_native("mercado.options_data_hist", ["fecha", "symbol"], pg_rows)
    print(f"  {fecha_str}: {len(rows)} symbols")
    return len(rows)


def fechas_con_datos(client):
    """Devuelve las fechas UTC (date) distintas que tienen al menos un doc en Opciones.Data."""
    pipeline = [
        {"$group": {"_id": {
            "y": {"$year":       "$timestamp"},
            "m": {"$month":      "$timestamp"},
            "d": {"$dayOfMonth": "$timestamp"},
        }}},
        {"$sort": {"_id.y": 1, "_id.m": 1, "_id.d": 1}},
    ]
    out = []
    for r in client["Opciones"]["Data"].aggregate(pipeline):
        i = r["_id"]
        out.append(date(i["y"], i["m"], i["d"]))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fecha", help="Procesar solo YYYY-MM-DD (upsert idempotente)")
    ap.add_argument("--backfill", action="store_true",
                    help="Procesar todos los días con datos en Opciones.Data")
    args = ap.parse_args()

    client = get_mongo_client()

    if args.backfill:
        fechas = fechas_con_datos(client)
        print(f"Backfill: {len(fechas)} días encontrados")
        total = 0
        for d in fechas:
            total += procesar_dia(client, d)
        print(f"Backfill completo: {total} rows upserted")
        return

    if args.fecha:
        d = datetime.strptime(args.fecha, "%Y-%m-%d").date()
    else:
        d = datetime.utcnow().date()

    procesar_dia(client, d)


if __name__ == "__main__":
    main()
