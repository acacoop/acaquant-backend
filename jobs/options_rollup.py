"""options_rollup.py — rollup diario de mercado.options_data (SQL) → mercado.options_data_hist (SQL).

Una fila por (fecha, symbol) en SQL, con un jsonb `data` que contiene el doc completo:
  fecha, symbol, tipo, strike
  high (max), low (min > 0), last (último tick cronológico)
  ev (max, = acumulado final del día)
  delta, gamma, vega, theta, iv, spot (del último tick)

SQL-NATIVE (decomiso Mongo): la fuente `Opciones.Data` (Mongo) fue dropeada — el motor
`engines/options.py` escribe los ticks a `mercado.options_data` (append). Este rollup lee
ESA tabla y escribe `mercado.options_data_hist` (write_native, upsert por fecha+symbol;
`fecha` text 'YYYY-MM-DD'). Lo lee api/services/opciones_sql.get_griegas_historico.

Modos:
  --fecha YYYY-MM-DD    procesa solo ese día (upsert idempotente)
  --backfill            procesa todos los días con datos en mercado.options_data
  (sin flag)            procesa el día UTC actual — uso del cron

Cron: 20:15 UTC L-V (17:15 ART).
"""

import argparse
from datetime import date, datetime, timedelta

from core.pg_mirror import write_native
from core.postgres import get_pool


def _dia_utc(d: date):
    """Devuelve (start, end) naive para agrupar un día calendario (ts es naive ART)."""
    start = datetime(d.year, d.month, d.day)
    end   = start + timedelta(days=1)
    return start, end


# Agregación equivalente al $group Mongo, en SQL sobre el jsonb `data`:
#   tipo/strike = $first (primer tick del día) · high/ev = $max · low = $min (>0)
#   last/delta/gamma/vega/theta/iv/spot = $last (último tick cronológico)
# array_agg(... ORDER BY ts)[1] = first ; (... ORDER BY ts DESC)[1] = last.
_ROLLUP_SQL = """
    SELECT
        symbol,
        (array_agg((data->>'tipo')              ORDER BY ts))[1]       AS tipo,
        (array_agg((data->>'strike')::float8    ORDER BY ts))[1]       AS strike,
        max((data->>'high')::float8)                                    AS high,
        min((data->>'low')::float8) FILTER (WHERE (data->>'low')::float8 > 0) AS low,
        (array_agg((data->>'last')::float8       ORDER BY ts DESC))[1]  AS last,
        max((data->>'ev')::float8)                                      AS ev,
        (array_agg((data->>'delta')::float8      ORDER BY ts DESC))[1]  AS delta,
        (array_agg((data->>'gamma')::float8      ORDER BY ts DESC))[1]  AS gamma,
        (array_agg((data->>'vega')::float8       ORDER BY ts DESC))[1]  AS vega,
        (array_agg((data->>'theta')::float8      ORDER BY ts DESC))[1]  AS theta,
        (array_agg((data->>'iv')::float8         ORDER BY ts DESC))[1]  AS iv,
        (array_agg((data->>'spot')::float8       ORDER BY ts DESC))[1]  AS spot
    FROM mercado.options_data
    WHERE ts >= %s AND ts < %s
    GROUP BY symbol
"""

_CAMPOS = ("tipo", "strike", "high", "low", "last", "ev",
           "delta", "gamma", "vega", "theta", "iv", "spot")


def procesar_dia(d: date) -> int:
    start, end = _dia_utc(d)
    fecha_str = d.strftime("%Y-%m-%d")

    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(_ROLLUP_SQL, (start, end))
        cols = [c.name for c in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]

    if not rows:
        print(f"  {fecha_str}: sin datos")
        return 0

    # Cada fila lleva el doc completo dentro del jsonb `data` (fecha+symbol redundan
    # adentro, los lee opciones_sql.get_griegas_historico).
    pg_rows = []
    for r in rows:
        doc = {"fecha": fecha_str, "symbol": r["symbol"]}
        doc.update({c: r.get(c) for c in _CAMPOS})
        pg_rows.append({"fecha": fecha_str, "symbol": r["symbol"], "data": doc})

    write_native("mercado.options_data_hist", ["fecha", "symbol"], pg_rows)
    print(f"  {fecha_str}: {len(rows)} symbols")
    return len(rows)


def fechas_con_datos() -> list[date]:
    """Fechas (date) distintas con al menos un tick en mercado.options_data (ts naive)."""
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT date_trunc('day', ts)::date AS d "
            "FROM mercado.options_data ORDER BY d"
        )
        return [r[0] for r in cur.fetchall()]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fecha", help="Procesar solo YYYY-MM-DD (upsert idempotente)")
    ap.add_argument("--backfill", action="store_true",
                    help="Procesar todos los días con datos en mercado.options_data")
    args = ap.parse_args()

    from core.job_runs import JobRunLogger
    with JobRunLogger("options_rollup") as jr:
        return _correr(jr, args)


def _correr(jr, args):
    if args.backfill:
        fechas = fechas_con_datos()
        print(f"Backfill: {len(fechas)} días encontrados")
        total = 0
        for d in fechas:
            total += procesar_dia(d)
        jr.set_stat("backfill_rows", total)
        print(f"Backfill completo: {total} rows upserted")
        return

    if args.fecha:
        d = datetime.strptime(args.fecha, "%Y-%m-%d").date()
    else:
        d = datetime.utcnow().date()

    procesar_dia(d)


if __name__ == "__main__":
    main()
