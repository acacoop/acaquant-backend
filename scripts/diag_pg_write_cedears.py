"""diag_pg_write_cedears.py — ¿el proceso del motor puede escribir mercado.cedears_snapshot?

Read-mostly (escribe + borra UNA fila throwaway "__DIAG__"). Confirma, desde el MISMO
venv/.env que corre engines/motor_cedears, si el camino de escritura SQL del cutover
funciona — SIN tocar el motor en vivo. Diagnóstico del incidente 2026-06-24 (revert del
cutover CedearsSnapshot→SQL: el motor fue matado en pleno arranque en frío, antes de
escribir a SQL → no sabemos si write_native anda).

    python -m scripts.diag_pg_write_cedears
"""
from __future__ import annotations

import logging
import os
import time
from datetime import UTC, datetime

from core import pg_mirror
from core.postgres import get_pool

# Para que cualquier error interno de pg_mirror (que captura y loguea) se vea acá:
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")


def main() -> int:
    print(f"SNAPSHOT_SQL      = {os.getenv('SNAPSHOT_SQL')!r}")
    print(f"MERCADO_SQL_WRITE = {os.getenv('MERCADO_SQL_WRITE')!r}")

    # 1) PG accesible desde este proceso + frescura actual de la tabla.
    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT 1")
            cur.execute("SELECT count(*), max(updated_at) FROM mercado.cedears_snapshot")
            n, ult = cur.fetchone()
        print(f"PG OK · cedears_snapshot: {n} filas · último updated_at={ult}")
        if ult is not None and ult.tzinfo is not None:
            age = (datetime.now(UTC) - ult).total_seconds()
            print(f"  → antigüedad del último write: {age:.0f}s "
                  f"({'FRESCO (motor/sync escribiendo)' if age < 120 else 'STALE'})")
    except Exception as e:
        print(f"❌ PG NO accesible desde este proceso: {type(e).__name__}: {e}")
        print("   → ese es el problema: el motor no puede llegar a Postgres.")
        return 1

    # 2) write_native — exactamente lo que hace el _snapshot_loop cada iteración.
    row = [{"ticker": "__DIAG__",
            "data": {"ticker": "__DIAG__", "diag": True, "last": 1.23},
            "updated_at": datetime.now(UTC)}]
    t0 = time.monotonic()
    escritas = pg_mirror.write_native("mercado.cedears_snapshot", ["ticker"], row)
    dt_ms = (time.monotonic() - t0) * 1000
    print(f"write_native: {escritas} fila(s) en {dt_ms:.0f} ms "
          f"({'OK' if dt_ms < 500 else 'LENTO — posible presión de pool'})")

    # 3) verificar que landeó + limpiar la fila throwaway.
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT data->>'diag' FROM mercado.cedears_snapshot WHERE ticker = '__DIAG__'")
        got = cur.fetchone()
        cur.execute("DELETE FROM mercado.cedears_snapshot WHERE ticker = '__DIAG__'")
        conn.commit()

    if got:
        print("\n✅ write_native FUNCIONA — el camino SQL del motor está SANO.")
        print("   El 'freeze' fue el arranque en frío (~4.5min), no un bug del cutover.")
    else:
        print("\n❌ write_native NO escribió (mirá el error de pg_mirror arriba) — acá está el bug.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
