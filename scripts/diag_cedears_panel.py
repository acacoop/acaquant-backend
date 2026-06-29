"""scripts/diag_cedears_panel.py — universo (72) vs huérfanos del snapshot (read-only).

El panel muestra `mercado.cedears WHERE activo IS TRUE` (master). El snapshot tiene más
filas (tickers tracked en el pasado, ahora fuera del master). Este diag lista AMBOS para
ver qué está en el universo y qué se removió (donde deberían estar ASTS/SPCX si se cayeron).

    python -m scripts.diag_cedears_panel
"""
from __future__ import annotations

from core.postgres import get_pool


def main() -> int:
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT ticker, ticker_corto, activo, rubro FROM mercado.cedears ORDER BY ticker_corto")
        master = cur.fetchall()
        cur.execute("SELECT ticker FROM mercado.cedears_snapshot")
        snap_tickers = {r[0] for r in cur.fetchall()}

    master_tickers = {m[0] for m in master}
    activos = [m for m in master if m[2] is True]
    inactivos = [m for m in master if m[2] is not True]
    huerfanos = sorted(snap_tickers - master_tickers)  # en snapshot, NO en master

    print(f"MASTER mercado.cedears: {len(master)}  (activo=True: {len(activos)}, "
          f"activo!=True: {len(inactivos)})")
    print(f"SNAPSHOT filas: {len(snap_tickers)}  →  huérfanas (no en master): {len(huerfanos)}\n")

    print("UNIVERSO ACTIVO (ticker_corto / rubro):")
    print("  " + ", ".join(f"{m[1]}{'' if m[3] else '·sin-rubro'}" for m in activos))

    if inactivos:
        print(f"\nINACTIVOS en master (activo!=True) — NO los muestra el panel ({len(inactivos)}):")
        print("  " + ", ".join(m[1] or m[0] for m in inactivos))

    print(f"\nHUÉRFANOS en snapshot (tracked antes, fuera del master ahora) ({len(huerfanos)}):")
    print("  " + ", ".join(huerfanos))

    # Flag explícito de los que preguntó el user.
    for q in ("ASTS", "SPCX", "SPCE"):
        en_master = [m[0] for m in master if (m[1] or "").upper() == q]
        en_huerf = [h for h in huerfanos if q in h.upper()]
        print(f"\n  {q}: en master={en_master or '—'}  | huérfano snapshot={en_huerf or '—'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
