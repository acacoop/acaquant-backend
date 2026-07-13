"""cleanup_calendar_news.py — limpieza de home.market_calendar y home.news_headlines.

DRY-RUN por default (no borra). Con --commit ejecuta.

  market_calendar: deja SOLO AR/US/BR + impacto 3 y eventos NO viejos; borra el
                   resto (otros países, bajo impacto, eventos pasados).
  news_headlines:  borra todo lo anterior a 48hs (TTL) — no sirve viejo.

Tablas chicas (single DELETE, sin batch). Idempotente. REGLA #4.

Correr:  python -m scripts.cleanup_calendar_news            # DRY-RUN
         python -m scripts.cleanup_calendar_news --commit   # ejecuta
"""
from __future__ import annotations

import argparse

from psycopg.rows import dict_row

from core.postgres import get_pool

COUNTRIES = ("AR", "US", "BR")
IMPACT_MIN = 3
NEWS_TTL_HORAS = 48
# Eventos pasados (ya sucedidos) más viejos que esto se borran del calendario.
CAL_VIEJOS_DIAS = 2


def _q(cur, sql, params=()):
    cur.execute(sql, params)
    return cur.fetchall()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--commit", action="store_true", help="ejecuta los DELETE (default: dry-run)")
    args = ap.parse_args()

    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        # ── market_calendar: diagnóstico ──
        print("== market_calendar — países presentes (top) ==")
        for r in _q(cur, "SELECT country, count(*) AS n FROM home.market_calendar "
                         "GROUP BY country ORDER BY n DESC LIMIT 20"):
            keep = "  <-- SE MANTIENE" if r["country"] in COUNTRIES else ""
            print(f"  {r['country']!s:<6} {r['n']:>6}{keep}")

        rango = _q(cur, "SELECT min(evt_ts) AS mn, max(evt_ts) AS mx, count(*) AS n "
                        "FROM home.market_calendar")[0]
        print(f"\n  rango evt_ts: {rango['mn']} → {rango['mx']}  · total {rango['n']} filas")

        # Qué borraría del calendario (noise + viejos).
        cond_cal = (
            "country <> ALL(%s) OR impact IS NULL OR impact < %s "
            "OR evt_ts < now() - make_interval(days => %s)"
        )
        p_cal = (list(COUNTRIES), IMPACT_MIN, CAL_VIEJOS_DIAS)
        n_cal = _q(cur, f"SELECT count(*) AS n FROM home.market_calendar WHERE {cond_cal}", p_cal)[0]["n"]
        n_cal_keep = rango["n"] - n_cal
        print(f"\n  → borraría {n_cal} filas del calendario (quedan {n_cal_keep}: "
              f"AR/US/BR, impacto {IMPACT_MIN}, no más viejos que {CAL_VIEJOS_DIAS}d).")

        # ── news_headlines: TTL 48hs ──
        n_news = _q(cur, "SELECT count(*) AS n FROM home.news_headlines "
                         "WHERE fecha_publicacion IS NULL "
                         "OR fecha_publicacion < now() - make_interval(hours => %s)",
                    (NEWS_TTL_HORAS,))[0]["n"]
        n_news_tot = _q(cur, "SELECT count(*) AS n FROM home.news_headlines")[0]["n"]
        print(f"\n== news_headlines — TTL {NEWS_TTL_HORAS}hs ==")
        print(f"  → borraría {n_news} de {n_news_tot} noticias (anteriores a {NEWS_TTL_HORAS}hs / sin fecha).")

        if not args.commit:
            print("\n(DRY-RUN — nada borrado. Corré con --commit para ejecutar.)")
            return

        cur.execute(f"DELETE FROM home.market_calendar WHERE {cond_cal}", p_cal)
        d_cal = cur.rowcount
        cur.execute("DELETE FROM home.news_headlines WHERE fecha_publicacion IS NULL "
                    "OR fecha_publicacion < now() - make_interval(hours => %s)", (NEWS_TTL_HORAS,))
        d_news = cur.rowcount
        conn.commit()
        print(f"\n✅ BORRADO: {d_cal} del calendario · {d_news} de news. "
              "Corré VACUUM (o esperá el autovacuum) para recuperar el espacio físico.")


if __name__ == "__main__":
    main()
