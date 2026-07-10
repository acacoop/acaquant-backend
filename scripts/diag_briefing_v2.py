"""scripts/diag_briefing_v2.py — ¿qué datos hay para el Briefing v2? (QuantAI P1)

Diagnóstico READ-ONLY que mide las TRES fuentes nuevas que la mesa pidió sumar
al briefing, antes de construir nada (REGLA #2 — medir primero):

  1. FUTUROS ampliados (home.market_quotes, grupo 'Futuros') + ¿tienen ancla de
     semana/mes? (anchor_7d / anchor_mtd). Confirma si se puede mostrar "cómo
     viene en la semana / en el mes" o si esos futuros están sin ancla.
  2. CALENDARIO económico (home.market_calendar): cuánto hay, rango de fechas, y
     los eventos de la ventana [ayer .. +7 días] → ¿hay algo para hoy?
  3. ACREENCIAS de HOY (operaciones.acreencias, ya cruzado con cartera): resumen
     por día en la ventana y detalle de los cobros con fecha_pago = hoy. OJO: el
     motor proyecta solo flujos > hoy, así que esto revela si "vence hoy" quedó
     dentro o fuera de la tabla.

Correrlo idealmente ~10:00 ART:
    python -m scripts.diag_briefing_v2
"""
from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from core.postgres import connect

_ART = ZoneInfo("America/Argentina/Buenos_Aires")


def _n(v, dec: int = 2) -> str:
    if v is None:
        return "—"
    try:
        return f"{float(v):,.{dec}f}"
    except (TypeError, ValueError):
        return str(v)


def _ret(last, anchor) -> str:
    if last is None or not anchor:
        return "  s/ancla"
    try:
        v = (float(last) / float(anchor) - 1) * 100
        return f"{'+' if v >= 0 else ''}{v:.2f}%"
    except (TypeError, ValueError, ZeroDivisionError):
        return "  err"


def main() -> None:
    hoy_art = datetime.now(_ART).date()
    hoy = hoy_art.isoformat()
    ayer = (hoy_art - timedelta(days=2)).isoformat()
    mas10 = (hoy_art + timedelta(days=10)).isoformat()
    print(f"HOY (ART): {hoy}\n")

    with connect() as conn, conn.cursor() as cur:
        # ── 1. FUTUROS + anclas ──────────────────────────────────
        print("── 1. FUTUROS (home.market_quotes, grupo 'Futuros') ─────────────")
        cur.execute(
            "SELECT symbol, data FROM home.market_quotes "
            "WHERE data->>'type' = 'future' OR data->>'grupo' = 'Futuros' "
            "ORDER BY symbol"
        )
        filas = cur.fetchall()
        if not filas:
            print("  ✗ SIN futuros — ¿corre jobs.market_quotes?")
        print(f"  {'símbolo':<12} {'last':>12} {'1d':>9} {'sem(7d)':>10} {'mes(MTD)':>10}  ancla_upd")
        con_ancla = sin_ancla = 0
        for sym, d in filas:
            a7, amtd = d.get("anchor_7d"), d.get("anchor_mtd")
            if a7 or amtd:
                con_ancla += 1
            else:
                sin_ancla += 1
            pctd = d.get("pct_day")
            pctd_s = f"{'+' if (pctd or 0) >= 0 else ''}{pctd:.2f}%" if pctd is not None else "—"
            upd = d.get("anchors_updated_at") or "—"
            print(f"  {sym:<12} {_n(d.get('last')):>12} {pctd_s:>9} "
                  f"{_ret(d.get('last'), a7):>10} {_ret(d.get('last'), amtd):>10}  {str(upd)[:19]}")
        print(f"  → {con_ancla} con ancla semana/mes · {sin_ancla} SIN ancla "
              f"(esos muestran '—' en semana/mes hasta sumarlos a jobs.market_anchors)")

        # ── 2. CALENDARIO económico ──────────────────────────────
        print("\n── 2. CALENDARIO (home.market_calendar) ─────────────────────────")
        cur.execute("SELECT count(*), min(evt_ts), max(evt_ts) FROM home.market_calendar")
        total, mn, mx = cur.fetchone()
        print(f"  filas totales: {total} · rango: {str(mn)[:16]} .. {str(mx)[:16]}")
        if not total:
            print("  ✗ VACÍA — ¿corre jobs.economic_calendar? Sin esto no hay bloque Agenda.")
        else:
            cur.execute(
                "SELECT evt_ts, country, event, impact FROM home.market_calendar "
                "WHERE evt_ts >= now() - interval '1 day' "
                "  AND evt_ts <  now() + interval '8 days' "
                "ORDER BY evt_ts LIMIT 60"
            )
            evs = cur.fetchall()
            print(f"  eventos ventana [ayer .. +7d]: {len(evs)}")
            for ts, country, event, impact in evs:
                imp = "★" * (impact or 0) if impact else ""
                marca = "  ← HOY" if ts.astimezone(_ART).date() == hoy_art else ""
                print(f"    {ts.astimezone(_ART):%d/%m %H:%M}  [{country or '—':<3}] "
                      f"{(event or '')[:48]:<48} {imp}{marca}")

        # ── 3. ACREENCIAS de hoy (ya cruzado con cartera) ────────
        print("\n── 3. ACREENCIAS (operaciones.acreencias — ya es 'en cartera') ──")
        cur.execute("SELECT count(*), max(data->>'snapshot'), max(data->>'generado_at') "
                    "FROM operaciones.acreencias")
        cnt, snap, gen = cur.fetchone()
        print(f"  filas totales: {cnt} · snapshot tenencia: {snap} · generado: {str(gen)[:19]}")
        if cnt:
            print(f"\n  Resumen por fecha_pago [{ayer} .. {mas10}]:")
            cur.execute(
                "SELECT fecha_pago, moneda, count(*), sum(monto) "
                "FROM operaciones.acreencias WHERE fecha_pago BETWEEN %s AND %s "
                "GROUP BY fecha_pago, moneda ORDER BY fecha_pago, moneda",
                (ayer, mas10),
            )
            rows = cur.fetchall()
            if not rows:
                print("    (nada en la ventana)")
            for fp, mon, c, tot in rows:
                marca = "  ← HOY" if fp == hoy else ""
                print(f"    {fp}  {mon or '—':<4} {c:>4} pagos  total {_n(tot):>16}{marca}")

            print(f"\n  Detalle de cobros con fecha_pago = HOY ({hoy}):")
            cur.execute(
                "SELECT ticker, moneda, monto, data->>'emisor', data->>'cliente', id_cuenta "
                "FROM operaciones.acreencias WHERE fecha_pago = %s ORDER BY monto DESC NULLS LAST",
                (hoy,),
            )
            hoy_rows = cur.fetchall()
            if not hoy_rows:
                print("    (NINGUNO hoy — recordá: el motor proyecta solo flujos > hoy;")
                print("     si vence algo HOY, hay que calcularlo aparte desde el calendario)")
            for tk, mon, monto, emisor, cliente, idc in hoy_rows[:40]:
                print(f"    {tk or '—':<10} {mon or '—':<4} {_n(monto):>14}  "
                      f"{(emisor or '')[:22]:<22} {(cliente or idc or '')[:26]}")
            if len(hoy_rows) > 40:
                print(f"    … +{len(hoy_rows) - 40} más")

    print("\nListo. Pegale este output a Claude.")


if __name__ == "__main__":
    main()
