"""diag_eikon_snapshot.py — verifica la PRUEBA del feed Eikon (read-only, one-shot).

Muestra qué llegó a `mercado.eikon_snapshot` (filas, frescura, sample) y cuántos
CEDEARs tienen RIC cargado en el catálogo. Correr en el Droplet mientras el feed
(`scripts/eikon_feed_simple.py`, PC oficina) está prendido:

    python -m scripts.diag_eikon_snapshot

Se borra cuando la prueba cierre (REGLA #5).
"""
from __future__ import annotations

from core.postgres import get_pool


def main() -> None:
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT count(*), min(updated_at), max(updated_at) FROM mercado.eikon_snapshot")
        n, ts_min, ts_max = cur.fetchone()
        print(f"mercado.eikon_snapshot: {n} filas · updated_at [{ts_min} → {ts_max}]")

        cur.execute(
            "SELECT count(*) FILTER (WHERE ric IS NOT NULL AND ric <> ''), count(*) "
            "FROM mercado.cedears WHERE activo IS TRUE")
        con_ric, activos = cur.fetchone()
        print(f"mercado.cedears activos: {activos} · con RIC: {con_ric}")

        # TODOS los RICs cargados, tal cual los toma el feed (underlying → ric).
        cur.execute(
            "SELECT upper(COALESCE(underlying, ticker_corto)) AS und, max(NULLIF(ric,'')) "
            "FROM mercado.cedears WHERE activo IS TRUE GROUP BY 1 ORDER BY 1")
        print("\nuniverso del feed (underlying → RIC):")
        for und, ric in cur.fetchall():
            print(f"  {und:<8} → {ric or '(sin ric)'}")

        cur.execute(
            "SELECT ticker, ric, data->>'last', data->>'var_pct', data->>'hora', updated_at "
            "FROM mercado.eikon_snapshot ORDER BY updated_at DESC LIMIT 10")
        rows = cur.fetchall()
        if rows:
            print("\núltimos 10 quotes:")
            for t, ric, last, var, hora, ts in rows:
                print(f"  {t:<6} {ric or '—':<10} last={last or '—':<10} "
                      f"var%={var or '—':<8} hora={hora or '—':<10} {ts}")
        else:
            print("\n(sin quotes todavía — ¿el feed está corriendo en la PC de oficina?)")


if __name__ == "__main__":
    main()
