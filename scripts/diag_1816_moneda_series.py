"""READ-ONLY. ¿A qué dólar están las series de RESEARCH → RENTA FIJA ARGENTINA?

Herramienta: diag · Primero la base (gratis), después la API (cuesta créditos).

EL PUNTO
========

Los cuadrantes SPREAD A−B y COMPARAR leen `research.mkt_1816_series`, que llena
`jobs/mercado_1816_series`. Ese job pide las series así:

    mercado_1816.series(lote, _CAMPOS, desde, hasta)     # jobs/mercado_1816_series.py:160

…sin pasar `moneda`, o sea con el **default de la API, `ars`**. Y el spec de 1816
dice —textual, transcripto en `core/mercado_1816.py` arriba de `MONEDAS`— que con
`ars`, *«para instrumentos pagaderos en moneda distinta a ARS, para calcular
indicadores las cotizaciones se dividen por CCL»*.

**Toda esta plataforma divide por MEP**, no por CCL (`engines/curvas.py::
precio_soberano_a_usd`). O sea que la sospecha es concreta: la TEA, la paridad y
el precio de los HARD DOLLAR (Bonares, Globales, BOPREALes) que se grafican en
Spread y Comparar estarían a CCL, y el spread contra un bono en pesos —o contra
nuestra propia pantalla de Renta Fija— mezcla dos tipos de cambio.

No es teoría: ya se midió una vez en otro pipeline (`agente/alta.py::
moneda_cotejo_1816`, 2026-08-17). Para GD46, 1816 daba paridad **0,7278 en `ars`
contra 0,7556 en `mep`** — 4,07% de diferencia contra 0,24%, y 202 bps de TEA.
Lo que este diag contesta es si ESE mismo problema está en las series históricas.

PASO 1 — GRATIS (solo Postgres). Tres preguntas:
   a) ¿Qué `moneda` quedó grabada en las filas de `mkt_1816_series`?
   b) ¿Qué bonos del watch son HARD DOLLAR (pagan en USD) y por lo tanto están
      afectados, y cuáles son en pesos (donde `ars` es lo correcto)?
   c) ¿Cuántas filas de cada uno? = el tamaño de lo que habría que rebajar.

PASO 2 — CUESTA CRÉDITOS, por eso es opt-in (`--pedir`). Le pide a 1816 los
MISMOS campos, del MISMO día, para los mismos tickers, una vez en `ars` y otra en
`mep`, y muestra la diferencia. Si las dos columnas dan igual, la hipótesis se
cae y no hay nada que arreglar. Costo: tickers × campos × 2 (default 3×3×2 = 18).

Uso:
    python -m scripts.diag_1816_moneda_series                    # solo base
    python -m scripts.diag_1816_moneda_series --pedir            # + comparar ars vs mep
    python -m scripts.diag_1816_moneda_series --pedir AL30 GD30 GD46
"""
from __future__ import annotations

import sys

from core.postgres import get_job_pool as get_pool

_CAMPOS = ["tea", "paridad", "precioClean"]
_MAX_PEDIR = 6          # tope defensivo: cada ticker son 3 campos × 2 monedas


def _q(sql: str, params: tuple = ()) -> list[dict]:
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params or None)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r, strict=False)) for r in cur.fetchall()]


def _f(v) -> str:
    return "—" if v is None else f"{float(v):.6f}"


def _paso1() -> list[str]:
    """Imprime el estado en la base. Devuelve los tickers HARD DOLLAR."""
    print(f"\n{'=' * 78}\n 1) QUÉ HAY GRABADO EN research.mkt_1816_series\n{'=' * 78}\n")

    filas = _q("""
        SELECT moneda, fuente, plazo, convencion_tna,
               count(*) AS filas, count(DISTINCT ticker) AS tickers,
               min(fecha) AS desde, max(fecha) AS hasta
        FROM research.mkt_1816_series
        GROUP BY moneda, fuente, plazo, convencion_tna
        ORDER BY filas DESC
    """)
    if not filas:
        print("  ✖ la tabla está VACÍA — no hay series bajadas.\n")
        return []
    print(f"  {'MONEDA':>8} {'FUENTE':>8} {'PLAZO':>5} {'CONV':>10} "
          f"{'FILAS':>9} {'TICKERS':>8}  RANGO")
    for f in filas:
        print(f"  {f['moneda']!s:>8} {f['fuente']!s:>8} {f['plazo']!s:>5} "
              f"{str(f['convencion_tna'])[:10]:>10} {f['filas']:>9} {f['tickers']:>8}"
              f"  {f['desde']} → {f['hasta']}")
    print("\n  Ojo con esta columna: `moneda` es lo que la API DEVOLVIÓ en la\n"
          "  respuesta, no lo que nosotros pedimos. Si dice `ccl` está confesando\n"
          "  la conversión; si dice `ars` sigue sin desmentirla — el spec dice que\n"
          "  con `ars` igual divide por CCL a los que pagan en dólares. Lo que\n"
          "  cierra la pregunta de verdad es el PASO 2.\n")

    print(f"\n{'=' * 78}\n 2) EL WATCH: quién está afectado y quién no\n{'=' * 78}\n")
    uni = _q("""
        SELECT w.ticker,
               coalesce(i.curva, w.curva, '?')   AS curva,
               i.moneda_denom, i.moneda_pago,
               count(s.*)                        AS filas
        FROM research.mkt_1816_watch w
        LEFT JOIN research.mkt_1816_instrumentos i ON i.ticker = w.ticker
        LEFT JOIN research.mkt_1816_series s       ON s.ticker = w.ticker
        WHERE w.activo
        GROUP BY w.ticker, i.curva, w.curva, i.moneda_denom, i.moneda_pago
        ORDER BY i.moneda_pago NULLS FIRST, curva, w.ticker
    """)
    if not uni:
        print("  ✖ `research.mkt_1816_watch` está vacío (el job estaría usando el "
              "seed).\n     Corré `python -m jobs.mercado_1816_discovery --apply`.\n")
        return []

    print(f"  {'TICKER':<10} {'CURVA':<28} {'DENOM':>6} {'PAGO':>6} {'FILAS':>8}   ¿A QUÉ DÓLAR?")
    hd: list[str] = []
    sin_ficha: list[str] = []
    for r in uni:
        pago = (r["moneda_pago"] or "").strip().upper()
        if not pago:
            veredicto, sin_ficha_flag = "?? sin ficha en el catálogo", True
        elif pago in ("USD", "USD_C", "DOL", "U$S"):
            veredicto, sin_ficha_flag = "⚠ CCL (debería ser MEP)", False
            hd.append(r["ticker"])
        else:
            veredicto, sin_ficha_flag = "ok — paga en pesos", False
        if sin_ficha_flag:
            sin_ficha.append(r["ticker"])
        print(f"  {r['ticker']:<10} {str(r['curva'])[:28]:<28} "
              f"{r['moneda_denom'] or '—'!s:>6} {r['moneda_pago'] or '—'!s:>6} "
              f"{r['filas']:>8}   {veredicto}")

    print(f"\n  → {len(hd)} de {len(uni)} bonos del watch PAGAN EN DÓLARES: "
          f"son los afectados.")
    afectadas = sum(r["filas"] for r in uni
                    if (r["moneda_pago"] or "").strip().upper() in ("USD", "USD_C", "DOL", "U$S"))
    print(f"  → {afectadas} filas de serie colgadas de ellos (el tamaño del rebajado).")
    if sin_ficha:
        print(f"  → ⚠ {len(sin_ficha)} sin ficha en `mkt_1816_instrumentos` "
              f"({', '.join(sin_ficha[:8])}{'…' if len(sin_ficha) > 8 else ''}):\n"
              f"     de esos no se puede DERIVAR la moneda. Falta correr\n"
              f"     `python -m jobs.mercado_1816_discovery --apply --catalogo`.")
    print("\n  Los dólar-linked son el caso al que NO hay que tocarle nada: están\n"
          "  denominados en USD pero pagan en pesos, y 1816 no publica `mep` para\n"
          "  ellos (medido 2026-08-17 con D30O6: devolvió todo None).\n")
    return hd


def _paso2(tickers: list[str]) -> int:
    """Le pregunta lo MISMO a 1816 en `ars` y en `mep`, del mismo día."""
    from core import mercado_1816

    print(f"\n{'=' * 78}\n 3) LA MISMA PREGUNTA EN ars Y EN mep\n{'=' * 78}\n")
    if not mercado_1816.disponible():
        print("  ✖ falta MERCADO_1816_API_KEY en este entorno — no puedo preguntar.\n")
        return 1
    tickers = tickers[:_MAX_PEDIR]
    print(f"  Pidiendo {len(tickers)} ticker(s) × {len(_CAMPOS)} campos × 2 monedas "
          f"= ~{len(tickers) * len(_CAMPOS) * 2} créditos.\n")

    # La rueda se resuelve UNA vez (con `ars`) y la segunda llamada se ata a ESA
    # fecha. Dejar que cada una elija la suya compararía dos días distintos y la
    # diferencia no querría decir nada.
    try:
        r_ars = mercado_1816.indicadores_vigentes(tickers, list(_CAMPOS), moneda="ars")
    except Exception as e:
        print(f"  ✖ 1816 no contestó en `ars`: {e}\n")
        return 1
    if not r_ars:
        print("  ✖ ninguna rueda trajo datos en `ars` — probá otro día.\n")
        return 1
    fecha = r_ars.get("fechaOperacion")
    try:
        r_mep = mercado_1816.indicadores(tickers, list(_CAMPOS),
                                         moneda="mep", fecha_operacion=fecha)
    except Exception as e:
        print(f"  ✖ 1816 no contestó en `mep`: {e}\n")
        return 1

    a = (r_ars or {}).get("instrumentos") or {}
    m = (r_mep or {}).get("instrumentos") or {}
    print(f"  Rueda comparada: {fecha}   "
          f"(la API dice moneda={r_ars.get('moneda')!r} / {r_mep.get('moneda')!r})\n")
    print(f"  {'TICKER':<10} {'CAMPO':<12} {'ars (=CCL)':>14} {'mep':>14} {'DIFERENCIA':>14}")
    hubo_dif = False
    for tk in tickers:
        va, vm = a.get(tk) or {}, m.get(tk) or {}
        for c in _CAMPOS:
            x, y = va.get(c), vm.get(c)
            if x is None and y is None:
                dif = "sin dato"
            elif x is None or y is None:
                dif = "falta un lado"
            else:
                d = float(y) - float(x)
                # tea/paridad vienen en FRACCIÓN (0,0925 = 9,25%) → bps para la
                # tasa, puntos de % para la paridad. El precio va en su unidad.
                if c == "tea":
                    dif = f"{d * 10000:+.0f} bps"
                elif c == "paridad":
                    dif = f"{d * 100:+.2f} pp"
                else:
                    dif = f"{d:+.4f}"
                if abs(d) > 1e-9:
                    hubo_dif = True
            print(f"  {tk:<10} {c:<12} {_f(x):>14} {_f(y):>14} {dif:>14}")
    print()
    if hubo_dif:
        print("  ✅ CONFIRMADO: 1816 devuelve números DISTINTOS según la moneda, y lo\n"
              "     que hoy se guarda —y por lo tanto lo que grafican SPREAD y\n"
              "     COMPARAR— es la columna de la izquierda: el dólar de ELLOS (CCL),\n"
              "     no el MEP con el que trabaja el resto de la plataforma.\n")
    else:
        print("  ⓘ Las dos columnas dan IGUAL en esta rueda: acá la hipótesis NO se\n"
              "     confirma. Antes de tocar nada, repetir con un hard dollar que\n"
              "     haya operado (si el papel no operó, 1816 modela y puede coincidir).\n")
    return 0


def main() -> int:
    args = [a for a in sys.argv[1:]]
    pedir = "--pedir" in args
    pedidos = [a.upper() for a in args if not a.startswith("-")]

    hd = _paso1()

    if not pedir:
        print("  (PASO 2 no corrió: es opt-in porque cuesta créditos.\n"
              "   Para cerrar la pregunta: `python -m scripts.diag_1816_moneda_series --pedir`)\n")
        return 0
    objetivo = pedidos or hd[:3]
    if not objetivo:
        print("  ✖ no hay ningún hard dollar identificado para comparar. Pasá los\n"
              "    tickers a mano: `--pedir AL30 GD30 GD46`.\n")
        return 2
    return _paso2(objetivo)


if __name__ == "__main__":
    raise SystemExit(main())
