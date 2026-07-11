"""diag_pivots_rv.py — diagnóstico READ-ONLY de los pivots de Renta Variable.

Síntoma reportado (2026-07-11): en ZONAS muchos papeles muestran TODOS los
pivots del mismo lado ("vs LAST" todo positivo o todo negativo), sobre todo
en el frame ANUAL.

Hipótesis a verificar (en orden):
  H1. Splits sin re-ajustar: la ingesta guarda precios crudos de Yahoo
      (auto_adjust=False) y solo upsertea los últimos 5 días → si un papel
      splitteó después del backfill, su historia vieja quedó en la escala
      pre-split. La vela anual 2025 sale en otra escala que el precio actual.
  H2. Cobertura incompleta de 2025 (vela anual armada con pocos días).
  H3. Es legítimo: papeles que corrieron tanto que están fuera del rango
      [S3, R3] del año pasado.

Qué mide (solo SELECTs, nada se escribe):
  1. Cobertura por underlying en mercado.precios_acciones (velas 2025, rango).
  2. Discontinuidades día-a-día del close (salto >45% = candidato a split).
  3. Frame ANUAL recalculado: cuántos papeles quedan con el last fuera de
     [S3, R3] y si coinciden con los papeles con discontinuidad.
  4. Opcional --yahoo-check N: para los N peores, compara el close guardado de
     un día de 2025 contra lo que Yahoo devuelve HOY para ese mismo día — si
     difieren, Yahoo re-ajustó la historia y nuestra tabla quedó vieja (H1
     confirmada sin dudas).

Uso (Droplet):
    python -m scripts.diag_pivots_rv
    python -m scripts.diag_pivots_rv --yahoo-check 5
"""
from __future__ import annotations

import argparse
import time
from datetime import UTC, datetime

from core.postgres import get_pool
from quant.pivot_points import calcular

SALTO_SPLIT = 0.45  # |close/close_prev − 1| > 45% en un día = candidato a split


def _underlyings() -> list[str]:
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT upper(COALESCE(underlying, ticker_corto)) "
            "FROM mercado.cedears WHERE activo IS TRUE"
        )
        return sorted(r[0] for r in cur.fetchall())


def _cobertura(tickers: list[str]) -> dict[str, dict]:
    """Por ticker: rango de fechas, velas totales y velas/OHLC del año previo."""
    anio_prev = datetime.now(UTC).year - 1
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT ticker, min(fecha), max(fecha), count(*),
                   count(*)    FILTER (WHERE extract(year FROM fecha) = %(a)s),
                   max(high)   FILTER (WHERE extract(year FROM fecha) = %(a)s),
                   min(low)    FILTER (WHERE extract(year FROM fecha) = %(a)s)
            FROM mercado.precios_acciones
            WHERE ticker = ANY(%(t)s)
            GROUP BY ticker
            """,
            {"a": anio_prev, "t": tickers},
        )
        out = {
            r[0]: {"desde": r[1], "hasta": r[2], "n_total": r[3],
                   "n_prev": r[4], "h_prev": r[5], "l_prev": r[6]}
            for r in cur.fetchall()
        }
        # close del último día del año previo + último close de la serie
        cur.execute(
            """
            SELECT DISTINCT ON (ticker) ticker, close
            FROM mercado.precios_acciones
            WHERE ticker = ANY(%(t)s) AND extract(year FROM fecha) = %(a)s
            ORDER BY ticker, fecha DESC
            """,
            {"a": anio_prev, "t": tickers},
        )
        for tk, close in cur.fetchall():
            out.setdefault(tk, {})["c_prev"] = float(close) if close is not None else None
        cur.execute(
            """
            SELECT DISTINCT ON (ticker) ticker, close, fecha
            FROM mercado.precios_acciones
            WHERE ticker = ANY(%(t)s)
            ORDER BY ticker, fecha DESC
            """,
            {"t": tickers},
        )
        for tk, close, fecha in cur.fetchall():
            out.setdefault(tk, {})["last_eod"] = float(close) if close is not None else None
            out[tk]["last_eod_fecha"] = fecha
    return out


def _discontinuidades(tickers: list[str]) -> dict[str, list[tuple]]:
    """Saltos día-a-día del close > SALTO_SPLIT (candidatos a split/re-ajuste)."""
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT ticker, fecha, close, prev FROM (
                SELECT ticker, fecha, close,
                       lag(close) OVER (PARTITION BY ticker ORDER BY fecha) AS prev
                FROM mercado.precios_acciones
                WHERE ticker = ANY(%(t)s)
            ) s
            WHERE prev > 0 AND close > 0
              AND abs(close / prev - 1) > %(salto)s
            ORDER BY ticker, fecha
            """,
            {"t": tickers, "salto": SALTO_SPLIT},
        )
        out: dict[str, list[tuple]] = {}
        for tk, fecha, close, prev in cur.fetchall():
            out.setdefault(tk, []).append((fecha, float(prev), float(close)))
    return out


def _last_live(tickers: list[str]) -> dict[str, float]:
    """Precio live del ADR (mismo override que usa el service de pivots)."""
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT ticker, data->>'c' FROM mercado.adr_snapshot WHERE ticker = ANY(%s)",
            (tickers,),
        )
        return {r[0]: float(r[1]) for r in cur.fetchall() if r[1]}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--yahoo-check", type=int, default=0, metavar="N",
                    help="comparar N peores tickers contra Yahoo (histórico re-ajustado)")
    args = ap.parse_args()

    tickers = _underlyings()
    print(f"universo: {len(tickers)} underlyings activos\n")

    cob = _cobertura(tickers)
    disc = _discontinuidades(tickers)
    live = _last_live(tickers)

    anio_prev = datetime.now(UTC).year - 1
    sin_data = [t for t in tickers if t not in cob]
    incompletos = {t: c for t, c in cob.items() if (c.get("n_prev") or 0) < 200}

    # ── 1. cobertura ──
    print(f"─── COBERTURA {anio_prev} (vela anual) ───")
    print(f"sin data en precios_acciones: {len(sin_data)} {sin_data[:10]}")
    print(f"con año {anio_prev} incompleto (<200 velas): {len(incompletos)}")
    for t, c in sorted(incompletos.items(), key=lambda kv: kv[1].get("n_prev") or 0)[:15]:
        print(f"  {t:8s} velas_{anio_prev}={c.get('n_prev')}  serie {c.get('desde')} → {c.get('hasta')}")

    # ── 2. discontinuidades ──
    print(f"\n─── DISCONTINUIDADES (salto diario >{SALTO_SPLIT:.0%} — candidatos a split) ───")
    print(f"tickers con al menos un salto: {len(disc)}")
    for t, saltos in sorted(disc.items()):
        for fecha, prev, close in saltos[:2]:
            print(f"  {t:8s} {fecha}  close {prev:,.2f} → {close:,.2f}  (×{close / prev:.3f})")

    # ── 3. frame anual: ¿cuántos quedan con todos los pivots de un lado? ──
    fuera: list[tuple] = []
    for t, c in cob.items():
        h, l, cp = c.get("h_prev"), c.get("l_prev"), c.get("c_prev")
        last = live.get(t) or c.get("last_eod")
        if not (h and l and cp and last):
            continue
        lv = calcular(high=float(h), low=float(l), close=float(cp))
        if last > lv["r3"] or last < lv["s3"]:
            lado = "ARRIBA de R3 (vs LAST todo negativo)" if last > lv["r3"] \
                else "DEBAJO de S3 (vs LAST todo positivo)"
            desvio = last / lv["r3"] - 1 if last > lv["r3"] else last / lv["s3"] - 1
            fuera.append((abs(desvio), t, lado, last, lv["s3"], lv["r3"],
                          t in disc, (c.get("n_prev") or 0) < 200))

    fuera.sort(reverse=True)
    print(f"\n─── FRAME ANUAL: last fuera de [S3, R3] → {len(fuera)} de {len(cob)} papeles ───")
    print(f"{'':8s} {'lado':38s} {'last':>10s} {'S3':>10s} {'R3':>10s} split? incompleto?")
    for _desvio, t, lado, last, s3, r3, tiene_salto, incompleto in fuera[:20]:
        print(f"  {t:8s} {lado:38s} {last:>10,.2f} {s3:>10,.2f} {r3:>10,.2f}"
              f"   {'SÍ' if tiene_salto else 'no':3s}    {'SÍ' if incompleto else 'no'}")
    con_salto = sum(1 for f in fuera if f[6])
    print(f"\nde los {len(fuera)} fuera de rango: {con_salto} tienen discontinuidad tipo split, "
          f"{sum(1 for f in fuera if f[7])} tienen {anio_prev} incompleto")

    # ── 4. opcional: contraste contra Yahoo ──
    if args.yahoo_check and fuera:
        from core.yahoo import stock_candle
        print(f"\n─── CONTRASTE YAHOO (¿re-ajustó la historia?) — top {args.yahoo_check} ───")
        desde = int(datetime(anio_prev, 6, 2, tzinfo=UTC).timestamp())
        hasta = int(datetime(anio_prev, 6, 14, tzinfo=UTC).timestamp())
        for _, t, *_resto in fuera[: args.yahoo_check]:
            with get_pool().connection() as conn, conn.cursor() as cur:
                cur.execute(
                    "SELECT fecha, close FROM mercado.precios_acciones "
                    "WHERE ticker = %s AND fecha BETWEEN %s AND %s ORDER BY fecha LIMIT 1",
                    (t, datetime(anio_prev, 6, 2).date(), datetime(anio_prev, 6, 13).date()),
                )
                row = cur.fetchone()
            if not row:
                print(f"  {t:8s} sin vela guardada en jun-{anio_prev} — skip")
                continue
            fecha_db, close_db = row[0], float(row[1])
            r = stock_candle(t, "D", desde, hasta)
            if r.get("s") != "ok" or not r.get("c"):
                print(f"  {t:8s} Yahoo sin data ({r.get('s')}) — skip")
                continue
            close_y = r["c"][0]
            marca = "≠ RE-AJUSTADO" if abs(close_db / close_y - 1) > 0.10 else "= consistente"
            print(f"  {t:8s} {fecha_db}: guardado {close_db:,.2f} vs Yahoo hoy {close_y:,.2f}  {marca}")
            time.sleep(1)

    print("\nLectura: si los 'fuera de rango' coinciden con split?=SÍ o con el contraste "
          "'≠ RE-AJUSTADO', es H1 (historia sin re-ajustar) y el fix va por re-backfill "
          "scopeado por ticker + detección de re-ajuste en el job diario. Si no coinciden "
          "y la cobertura está completa, es H3 (movimiento legítimo).")


if __name__ == "__main__":
    main()
