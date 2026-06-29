"""scripts/diag_cedears_snapshot.py — cuáles de los 72 CEDEARs traen datos (read-only).

El universo (Mongo=SQL=72) está OK; el problema es que la suscripción WS no trae datos
para muchos. Este diag mira mercado.cedears_snapshot y reporta, de los 72: cuántos tienen
precio real (last/bid/offer), cuáles NO (los que "faltan"), y la frescura (updated_at).

    python -m scripts.diag_cedears_snapshot
"""
from __future__ import annotations

from datetime import UTC, datetime

from core.postgres import get_pool


def _num(v):
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def main() -> int:
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT ticker FROM mercado.cedears")
        universo = {r[0] for r in cur.fetchall()}
        cur.execute("SELECT ticker, data, updated_at FROM mercado.cedears_snapshot")
        snaps = cur.fetchall()

    con_precio, sin_precio, stale = [], [], []
    ahora = datetime.now(UTC)
    en_snap = set()
    for ticker, data, updated_at in snaps:
        en_snap.add(ticker)
        d = data or {}
        last = _num(d.get("last"))
        bid = _num(d.get("bid"))
        offer = _num(d.get("offer"))
        tiene = any(x and x > 0 for x in (last, bid, offer))
        edad_s = (ahora - updated_at).total_seconds() if updated_at else None
        (con_precio if tiene else sin_precio).append(ticker)
        if edad_s is not None and edad_s > 120:
            stale.append((ticker, round(edad_s)))

    sin_fila = sorted(universo - en_snap)

    print(f"Universo (mercado.cedears): {len(universo)}")
    print(f"Filas en snapshot:          {len(snaps)}")
    print(f"  CON precio (last/bid/offer>0): {len(con_precio)}")
    print(f"  SIN precio (vacíos):           {len(sin_precio)}")
    print(f"  sin fila en snapshot:          {len(sin_fila)}")
    print(f"  con updated_at > 120s (stale): {len(stale)}")
    print(f"\nSIN precio ({len(sin_precio)}):\n  {sorted(sin_precio)}")
    if sin_fila:
        print(f"\nSIN fila en snapshot ({len(sin_fila)}):\n  {sin_fila}")
    if stale:
        print(f"\nSTALE (ticker, edad_s) primeras 20:\n  {stale[:20]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
