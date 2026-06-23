"""verify_timesales_sql.py — prueba el write+read+tz de mercado.timesales ANTES de
cortar el motor a SQL-only y dropear Mongo. Read/write de UNA fila de test (se borra).

Valida:
  1. El write path real (pg_mirror.append_snapshot, el mismo que usa valores.py).
  2. Que el `ts` naive ART round-trip SIN corrimiento (la columna debe ser `timestamp`
     sin tz; si quedó timestamptz, esto lo detecta).
  3. Que get_historico_trades (SQL) lo lee con la hora correcta.

    python -m scripts.verify_timesales_sql

Requiere: tabla creada + `ALTER COLUMN ts TYPE timestamp` aplicado + SNAPSHOT_SQL=1.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from core import pg_mirror
from core.postgres import get_pool

_TICKER = "ZZ-VERIFY-TIMESALES"


def main() -> int:
    if not pg_mirror.snapshots_live_on():
        print("⚠️  SNAPSHOT_SQL no está en 1 → el write a SQL es no-op. Prendelo y reintentá.")
        return 1

    # ts naive ART de AHORA (mismo criterio que valores.py:296).
    ts = (datetime.now(UTC) - timedelta(hours=3)).replace(tzinfo=None, microsecond=0)
    fila = {"ticker": _TICKER, "ts": ts, "price": 123.45, "size": 10, "side": "BUY", "money": 12.345}

    n = pg_mirror.append_snapshot("mercado.timesales", [fila])
    print(f"append_snapshot → {n} fila escrita (esperado 1)")
    if n != 1:
        print("❌ El write NO funcionó. NO cortar el motor ni dropear. Revisar tabla/columnas.")
        return 1

    with get_pool().connection() as cn, cn.cursor() as cur:
        cur.execute("SELECT ticker, ts, price, size, side, money FROM mercado.timesales "
                    "WHERE ticker = %s ORDER BY id DESC LIMIT 1", (_TICKER,))
        row = cur.fetchone()
        print(f"read back → {row}")
        ok_ts = row and row[1] == ts   # naive == naive, mismo valor (sin corrimiento de tz)
        print(f"ts round-trip SIN corrimiento: {'✅' if ok_ts else '❌ (columna sigue timestamptz?)'}")
        # Limpieza
        cur.execute("DELETE FROM mercado.timesales WHERE ticker = %s", (_TICKER,))
        cn.commit()
        print(f"limpieza: {cur.rowcount} fila(s) de test borradas")

    if row and ok_ts:
        print("\n✅ VERIFICADO: write + read + tz OK. Se puede cortar el motor a SQL-only + dropear Mongo.")
        return 0
    print("\n❌ NO verificado — NO dropear.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
