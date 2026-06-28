"""diag_market_snapshot_freshness.py — verificación post writer-cutover MarketSnapshot→SQL.

Read-only sobre Postgres. Confirma que `mercado.market_snapshot` recibe el pricing
en vivo SQL-native (commit aaabaf2: valores.py + curvas.py escriben SOLO SQL, ya no
Trading.MarketSnapshot en Mongo). Es el GATE para dropear la colección Mongo.

Qué mide (la tabla es COLUMNAR — dos motores escriben columnas distintas sobre la
misma fila/ticker, ver sql/schema.sql:592):
  - valores.py (motor_rofex)  → book/last_price/open/high/low/closing/vwap/total_nominals/updated_at
  - curvas.py  (motor_curvas) → tea/tem/duration/mod_duration/convexity/paridad

Reporta: cobertura (filas totales, con precio, con métricas de curva), frescura
(updated_at más reciente y cuántas filas escritas en los últimos 30s/60s/5min) y
una muestra de las filas más frescas.

OJO: SOLO tiene sentido EN VENTANA DE MERCADO (L-V 13:00-20:00 UTC). Fuera de eso
los motores están stopped por cron y los datos quedan congelados del último cierre
— eso NO es un bug, es esperado.

    python -m scripts.diag_market_snapshot_freshness
"""
from __future__ import annotations

from datetime import UTC, datetime

from psycopg.rows import dict_row

from core.postgres import connect


def main() -> None:
    now = datetime.now(UTC)
    print(f"=== mercado.market_snapshot — frescura ({now:%Y-%m-%d %H:%M:%S} UTC) ===\n")

    dow = now.weekday()  # 0=lun .. 6=dom
    en_ventana = dow < 5 and 13 <= now.hour < 20
    if not en_ventana:
        print("⚠  FUERA DE VENTANA DE MERCADO (L-V 13:00-20:00 UTC). Los motores están")
        print("   stopped por cron → los datos están congelados del último cierre. Esto")
        print("   NO es un bug. Para validar el pricing live, correr en ventana.\n")

    with connect() as conn, conn.cursor(row_factory=dict_row) as cur:
        # ── Cobertura + frescura agregada ──
        cur.execute(
            """
            SELECT
                count(*)                                            AS filas,
                count(last_price)                                   AS con_precio,
                count(tea)                                          AS con_tea,
                count(duration)                                     AS con_duration,
                count(book)                                         AS con_book,
                max(updated_at)                                     AS ultimo_update,
                count(*) FILTER (WHERE updated_at > now() - interval '30 seconds')  AS ult_30s,
                count(*) FILTER (WHERE updated_at > now() - interval '60 seconds')  AS ult_60s,
                count(*) FILTER (WHERE updated_at > now() - interval '5 minutes')   AS ult_5min
            FROM mercado.market_snapshot
            """
        )
        r = cur.fetchone()

        if not r or r["filas"] == 0:
            print("✗ La tabla está VACÍA. El writer-cutover no está escribiendo (o el")
            print("  deploy del commit aaabaf2 todavía no corrió en el Droplet).")
            return

        ultimo = r["ultimo_update"]
        edad = (now - ultimo).total_seconds() if ultimo else None

        print(f"Filas (tickers):       {r['filas']:>6}")
        print(f"  con last_price:      {r['con_precio']:>6}  (escribe motor_rofex / valores.py)")
        print(f"  con book:            {r['con_book']:>6}")
        print(f"  con TEA:             {r['con_tea']:>6}  (escribe motor_curvas / curvas.py)")
        print(f"  con duration:        {r['con_duration']:>6}")
        print(f"\nÚltimo updated_at:     {ultimo}  ({_fmt_edad(edad)})")
        print(f"  filas < 30s:         {r['ult_30s']:>6}")
        print(f"  filas < 60s:         {r['ult_60s']:>6}")
        print(f"  filas < 5min:        {r['ult_5min']:>6}")

        # ── Muestra de las más frescas ──
        cur.execute(
            """
            SELECT ticker, last_price, tea, duration, paridad, updated_at
            FROM mercado.market_snapshot
            ORDER BY updated_at DESC NULLS LAST
            LIMIT 10
            """
        )
        print("\nÚltimas 10 filas actualizadas:")
        print(f"  {'ticker':<32} {'last':>10} {'tea':>8} {'dur':>6} {'parid':>7}  updated_at")
        for row in cur.fetchall():
            print(
                f"  {(row['ticker'] or '')[:32]:<32} "
                f"{_num(row['last_price']):>10} {_num(row['tea']):>8} "
                f"{_num(row['duration']):>6} {_num(row['paridad']):>7}  {row['updated_at']}"
            )

    # ── Veredicto ──
    print("\n=== VEREDICTO ===")
    if not en_ventana:
        print("Fuera de ventana — no se puede juzgar frescura live. Revisar cobertura:")
        print(f"  {'OK' if r['con_precio'] and r['con_tea'] else 'REVISAR'}: "
              "hay precios y métricas de curva del último cierre.")
        print("Volvé a correr el lunes 13-20 UTC para validar el pricing en vivo.")
    elif r["ult_60s"] > 0:
        print(f"✓ PRICING LIVE OK — {r['ult_60s']} filas escritas en el último minuto.")
        print("  Los motores escriben SQL-native. Trading.MarketSnapshot es DROPEABLE")
        print("  tras una verificación visual de las pantallas (renta fija/breakevens/forwards).")
    else:
        print("✗ EN VENTANA pero SIN writes en el último minuto. Posibles causas:")
        print("  - motores stopped/failed (correr /motor-status)")
        print("  - WS de pyRofex caído")
        print("  - el cutover (commit aaabaf2) no está deployado en el Droplet")


def _fmt_edad(seg: float | None) -> str:
    if seg is None:
        return "sin updated_at"
    if seg < 90:
        return f"hace {seg:.0f}s"
    if seg < 5400:
        return f"hace {seg / 60:.0f}min"
    return f"hace {seg / 3600:.1f}h"


def _num(v) -> str:
    return "—" if v is None else f"{float(v):,.2f}"


if __name__ == "__main__":
    main()
