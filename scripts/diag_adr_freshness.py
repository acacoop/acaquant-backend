"""diag_adr_freshness.py — diagnostica el desalce de la vista ADR del Scanner.

Para cada CEDEAR activo muestra:
  - updated_at de Trading.AdrSnapshot + antigüedad (edad en min/horas).
  - última fecha de Trading.PreciosAcciones (cierre EOD usado de fallback).
  - flag STALE si el snapshot no se actualizó hoy (UTC).

Con --probe, además pega a Finnhub para cada underlying STALE y reporta si
HOY devuelve quote — así distinguimos "Finnhub falla para este ticker" de
"el cron no llegó a correr / quedó a mitad".

Uso (desde la raíz, en el Droplet):
    python -m scripts.diag_adr_freshness
    python -m scripts.diag_adr_freshness --probe
"""
from __future__ import annotations

import sys
from datetime import UTC, datetime

from core.mongo import get_mongo_client


def _edad_str(updated_at: datetime | None, now: datetime) -> str:
    if not isinstance(updated_at, datetime):
        return "SIN updated_at"
    if updated_at.tzinfo is None:
        updated_at = updated_at.replace(tzinfo=UTC)
    secs = (now - updated_at).total_seconds()
    if secs < 3600:
        return f"{secs / 60:.0f} min"
    if secs < 86400:
        return f"{secs / 3600:.1f} h"
    return f"{secs / 86400:.1f} d"


def run(probe: bool = False) -> None:
    db = get_mongo_client()["Trading"]
    now = datetime.now(UTC)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    master = list(db["Cedears"].find(
        {"activo": True}, {"_id": 0, "ticker_corto": 1, "underlying": 1}
    ))
    # underlying único → ticker_corto(s) que lo usan
    corto_to_under = {
        m["ticker_corto"]: (m.get("underlying") or m["ticker_corto"])
        for m in master
    }
    underlyings = sorted(set(corto_to_under.values()))

    snaps = {
        s["ticker"]: s
        for s in db["AdrSnapshot"].find(
            {}, {"_id": 0, "ticker": 1, "c": 1, "pc": 1, "updated_at": 1}
        )
    }
    # última fecha EOD por underlying
    eod_fecha: dict[str, datetime | None] = {}
    for u in underlyings:
        d = db["PreciosAcciones"].find_one(
            {"ticker": u}, {"_id": 0, "fecha": 1}, sort=[("fecha", -1)]
        )
        eod_fecha[u] = d.get("fecha") if d else None

    print(f"\nADR freshness — {now.isoformat()}  (today_start={today_start.date()})")
    print(f"{'UNDERLYING':<10} {'SNAP_AGE':<12} {'STALE':<6} {'c':>10} {'EOD_FECHA':<12} {'CORTOS'}")
    print("-" * 78)

    stale: list[str] = []
    sin_snap: list[str] = []
    for u in underlyings:
        s = snaps.get(u)
        cortos = ",".join(sorted(k for k, v in corto_to_under.items() if v == u))
        if not s:
            sin_snap.append(u)
            eod = eod_fecha.get(u)
            eod_s = eod.date().isoformat() if isinstance(eod, datetime) else "--"
            print(f"{u:<10} {'SIN SNAP':<12} {'--':<6} {'--':>10} {eod_s:<12} {cortos}")
            continue
        ua = s.get("updated_at")
        if isinstance(ua, datetime) and ua.tzinfo is None:
            ua = ua.replace(tzinfo=UTC)
        es_stale = not (isinstance(ua, datetime) and ua >= today_start)
        if es_stale:
            stale.append(u)
        eod = eod_fecha.get(u)
        eod_s = eod.date().isoformat() if isinstance(eod, datetime) else "--"
        c_val = s.get("c")
        c_s = f"{c_val:.2f}" if isinstance(c_val, (int, float)) else "--"
        print(f"{u:<10} {_edad_str(ua, now):<12} {('STALE' if es_stale else 'ok'):<6} "
              f"{c_s:>10} {eod_s:<12} {cortos}")

    print("-" * 78)
    print(f"Total underlyings: {len(underlyings)} · STALE (no hoy): {len(stale)} · "
          f"SIN SNAP: {len(sin_snap)}")
    if stale:
        print(f"STALE: {', '.join(stale)}")
    if sin_snap:
        print(f"SIN SNAP: {', '.join(sin_snap)}")

    if probe and (stale or sin_snap):
        print("\n--probe: pegando a Finnhub para los STALE / SIN SNAP…")
        from core.finnhub import FinnhubError, quote
        for u in stale + sin_snap:
            try:
                q = quote(u)
                print(f"  {u:<10} c={q.get('c')} pc={q.get('pc')} t={q.get('t')}")
            except FinnhubError as e:
                print(f"  {u:<10} FINNHUB FAIL: {e}")


if __name__ == "__main__":
    run(probe="--probe" in sys.argv)
