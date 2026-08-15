"""scripts/diag_pnl_pata.py — ¿el PnL valúa con la pata correcta? READ-ONLY.

Nace del hallazgo del 2026-08-15 (`diag_activos` bloque 9): **65 assets tienen
en `instrumento` un símbolo DISTINTO al que usa `mercado.curvas`**, y el patrón
es sistemático — `assets` apunta a la pata en PESOS (`…YM37O…`) y el master a la
pata en DÓLARES (`…YM37D…`). Los 65 tienen tenencia: **24.521 filas**.

Y esa columna NO es decorativa. Dos hechos verificados en el código:

  · `engines/_universo_portfolio.py:57` — `assets.instrumento` es lo que el motor
    de portfolio SUSCRIBE. Por eso ese símbolo está en `portfolio_snapshot`.
  · `api/services/pnl.py:122` — el PnL lo usa como CLAVE para buscar el precio.

**Lo que este diag decide.** No se puede afirmar "está mal" mirando el símbolo:
el PnL trabaja en PESOS (`pnl.py::_pesificar` convierte el cost-basis con el MEP
del boleto), así que la pata en pesos podría ser la CORRECTA para su propósito.
Son dos preguntas distintas, no dos verdades sobre la misma.

**El árbitro es independiente.** `_aplicar_normalizer` dice en su docstring que su
trabajo es *"homogeneizar el precio vivo con `valor_aum`"* — y `valuacion` de
`portafolio.tenencia` la trae Aunesa, no la calculamos nosotros. Entonces:

    valor_calculado = normalizer(precio_de_la_pata, cantidad, cartera)
    ratio = valor_calculado / valuacion_de_aunesa

**La pata correcta es la que da ratio ≈ 1.** Si la de pesos da 1 y la de dólares
da ~1/1400, el sistema está bien y la "divergencia" era cada consumidor eligiendo
lo suyo. Si es al revés, hay PnL mal valuado y es lo primero a arreglar.

Se importa `_aplicar_normalizer` del motor en vez de recopiar la fórmula: si se
recopiara, este diag podría decir que todo está bien usando una regla que el
motor ya no usa.

Uso:
    python -m scripts.diag_pnl_pata
    python -m scripts.diag_pnl_pata --todos   # no solo los 65 divergentes
"""
from __future__ import annotations

import argparse

from api.services.pnl import _aplicar_normalizer
from core.postgres import get_pool

_SEP = "─" * 100


def _q(sql: str, params: tuple = ()) -> list[dict]:
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r, strict=False)) for r in cur.fetchall()]


def _f(v) -> float | None:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _filas(solo_divergentes: bool) -> list[dict]:
    """Última tenencia (aum='si') × el símbolo de CADA fuente × su precio.

    Scopeado a la ÚLTIMA fecha y a `aum='si'` (REGLA #4): no escanea el histórico.
    El precio de las dos patas sale de `portfolio_snapshot`, que es la MISMA
    tabla que lee el PnL — comparar contra otra fuente mediría otra cosa.
    """
    cond = "AND a.instrumento <> c.instrumento" if solo_divergentes else ""
    return _q(f"""
        WITH ult AS (SELECT max(fecha) AS f FROM portafolio.tenencia WHERE aum = 'si')
        SELECT t.unidad, a.ticker, t.cartera, t.tipo_titulo,
               sum(t.cantidad)  AS cantidad,
               sum(t.valuacion) AS valuacion,
               count(*)         AS filas,
               a.instrumento    AS simbolo_assets,
               c.instrumento    AS simbolo_curvas,
               pa.last_price    AS px_assets,
               pc.last_price    AS px_curvas
        FROM portafolio.tenencia t
        JOIN ult ON t.fecha = ult.f
        JOIN portafolio.assets a ON a.unidad = t.unidad
        JOIN mercado.curvas   c ON c.ticker  = a.ticker
        LEFT JOIN portfolio_snapshot pa ON pa.ticker = a.instrumento
        LEFT JOIN portfolio_snapshot pc ON pc.ticker = c.instrumento
        WHERE t.aum = 'si' AND COALESCE(t.cantidad, 0) <> 0
          AND a.instrumento IS NOT NULL AND c.instrumento IS NOT NULL {cond}
        GROUP BY t.unidad, a.ticker, t.cartera, t.tipo_titulo,
                 a.instrumento, c.instrumento, pa.last_price, pc.last_price
        ORDER BY sum(t.valuacion) DESC NULLS LAST
    """)


def _evaluar(filas: list[dict]) -> list[dict]:
    """Por fila: qué ratio da cada pata contra la valuación de Aunesa. PURO."""
    out = []
    for r in filas:
        qty, val = _f(r["cantidad"]), _f(r["valuacion"])
        pa, pc = _f(r["px_assets"]), _f(r["px_curvas"])
        if not qty or not val:
            continue
        va = _aplicar_normalizer(pa, qty, r["cartera"], r["tipo_titulo"]) if pa else None
        vc = _aplicar_normalizer(pc, qty, r["cartera"], r["tipo_titulo"]) if pc else None
        out.append({**r, "valor_assets": va, "valor_curvas": vc,
                    "ratio_assets": (va / val) if va else None,
                    "ratio_curvas": (vc / val) if vc else None})
    return out


def _veredicto(ratio: float | None) -> str:
    if ratio is None:
        return "sin precio"
    if 0.9 <= ratio <= 1.1:
        return "OK"
    if ratio > 100 or ratio < 0.01:
        return "OTRA ESCALA"
    return "difiere"


def main() -> None:
    ap = argparse.ArgumentParser(description="¿El PnL valúa con la pata correcta?")
    ap.add_argument("--todos", action="store_true",
                    help="no solo los divergentes (default: solo esos)")
    args = ap.parse_args()

    print("=" * 100)
    print("¿EL PnL VALÚA CON LA PATA CORRECTA? — árbitro: la valuación de Aunesa")
    print("=" * 100)
    print("  valor = normalizer(precio_de_la_pata, cantidad, cartera)   [la MISMA")
    print("  función del motor, importada — no una copia]")
    print("  ratio = valor / `portafolio.tenencia.valuacion`  →  la pata buena da ≈ 1")

    filas = _evaluar(_filas(not args.todos))
    if not filas:
        print("\n  sin filas para evaluar (¿corrió el backfill de tenencias?)")
        return

    tot = {"assets": {}, "curvas": {}}
    for f in filas:
        for k in ("assets", "curvas"):
            v = _veredicto(f[f"ratio_{k}"])
            tot[k][v] = tot[k].get(v, 0) + 1

    print(f"\n  instrumentos evaluados: {len(filas)}")
    print(f"\n  {'VEREDICTO':<14}{'con la pata de ASSETS':>24}{'con la pata de CURVAS':>24}")
    print("  " + _SEP[:62])
    for v in ("OK", "OTRA ESCALA", "difiere", "sin precio"):
        print(f"  {v:<14}{tot['assets'].get(v, 0):>24}{tot['curvas'].get(v, 0):>24}")
    print("  " + _SEP[:62])

    print(f"\n  DETALLE (por valuación descendente)\n  {_SEP}")
    print(f"  {'TICKER':<9}{'CART':<6}{'VALUACIÓN':>16}{'PX assets':>12}{'ratio':>9}"
          f"{'PX curvas':>12}{'ratio':>9}   VEREDICTO")
    print("  " + _SEP)
    for f in filas[:30]:
        ra, rc = f["ratio_assets"], f["ratio_curvas"]
        fmt = lambda x: f"{x:,.2f}" if isinstance(x, (int, float)) else "--"   # noqa: E731
        rf = lambda x: f"{x:,.2f}" if isinstance(x, (int, float)) else "--"    # noqa: E731
        print(f"  {str(f['ticker'])[:8]:<9}{str(f['cartera'] or '')[:5]:<6}"
              f"{fmt(_f(f['valuacion'])):>16}{fmt(_f(f['px_assets'])):>12}{rf(ra):>9}"
              f"{fmt(_f(f['px_curvas'])):>12}{rf(rc):>9}   "
              f"assets={_veredicto(ra)} · curvas={_veredicto(rc)}")
    if len(filas) > 30:
        print(f"  … y {len(filas) - 30} más")
    print("  " + _SEP)

    ok_a = tot["assets"].get("OK", 0)
    ok_c = tot["curvas"].get("OK", 0)
    print("\n  CÓMO SE LEE:")
    if ok_a > ok_c:
        print(f"    · ASSETS acierta en {ok_a} y CURVAS en {ok_c} → la pata que usa el PnL")
        print("      es la CORRECTA. La divergencia NO es un error: cada consumidor")
        print("      elige la suya (el PnL valúa en pesos, la curva en dólares).")
    elif ok_c > ok_a:
        print(f"    · CURVAS acierta en {ok_c} y ASSETS en {ok_a} → el PnL está usando la")
        print("      pata EQUIVOCADA. Es plata mal valuada y va primero en la lista.")
    else:
        print(f"    · empate ({ok_a} vs {ok_c}) → no concluyente; mirar el detalle a mano.")
    print("    · 'sin precio' NO es un error: fuera de rueda el snapshot está vacío.")
    print("      Si son todos, correr esto DURANTE la rueda (L-V 13-20 UTC).")


if __name__ == "__main__":
    main()
