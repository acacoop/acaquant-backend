"""READ-ONLY. ¿A qué dólar están las series de RESEARCH → RENTA FIJA ARGENTINA?

Herramienta: diag · Primero la base (gratis), después la API (cuesta créditos).

EL PUNTO
========

Los cuadrantes SPREAD A−B y COMPARAR leen `research.mkt_1816_series`. Hasta el
2026-09-09 ese feed se pedía con el default de la API (`ars`), y el spec de 1816
dice que con `ars` *«para instrumentos pagaderos en moneda distinta a ARS, para
calcular indicadores las cotizaciones se dividen por CCL»*. Esta plataforma
divide por **MEP**: la TEA y la paridad de todo bono en dólares estaban al dólar
de ellos. **Medido**: BPOB7 daba 7,26% en `ars` contra 2,44% en `mep` — 481 bps.

Ya está corregido (`core.mercado_1816.moneda_series`: cada bono se pide en la
moneda en la que PAGA), así que este diag contesta las dos preguntas que quedan:

PASO 1 — GRATIS (solo Postgres). **¿Ya está cada bono en la moneda que le toca?**
   Por ticker: en qué paga, qué moneda le corresponde, qué series hay guardadas
   y el veredicto — `✅ MEP`, `⚠ FALTA REBAJAR` (todavía se lee al CCL), u
   `ok — paga en pesos`. Es la verificación de que el backfill entró.

PASO 2 — CUESTA CRÉDITOS, por eso es opt-in (`--pedir`). Le pregunta a 1816 los
MISMOS campos, del MISMO día, en `ars` y en `mep`, y muestra la diferencia. Es lo
que prueba que las dos monedas NO son la misma cosa. Costo: tickers × campos × 2
(default 3 × 3 × 2 = 18 créditos).

Uso:
    python -m scripts.diag_1816_moneda_series                    # ¿está en MEP?
    python -m scripts.diag_1816_moneda_series --pedir            # + ars vs mep
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
    """Imprime el estado en la base. Devuelve los tickers a comparar en el paso 2
    (los que falten rebajar; si no falta ninguno, todos los hard dollar)."""
    from core import mercado_1816

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
    print("\n  `moneda` es lo que la API DEVOLVIÓ, y es parte de la PK: un mismo\n"
          "  bono puede tener las dos series. Que aparezca una fila `mep` es la\n"
          "  señal de que el rebajado corrió; que TODO siga en `ars` significa que\n"
          "  los bonos en dólares se están leyendo al CCL de 1816. El desglose por\n"
          "  bono está abajo.\n")

    print(f"\n{'=' * 78}\n 2) EL WATCH: quién está afectado y quién no\n{'=' * 78}\n")
    uni = _q("""
        SELECT w.ticker,
               coalesce(i.curva, w.curva, '?')   AS curva,
               i.moneda_denom, i.moneda_pago,
               s.moneda, count(s.*) AS filas
        FROM research.mkt_1816_watch w
        LEFT JOIN research.mkt_1816_instrumentos i ON i.ticker = w.ticker
        LEFT JOIN research.mkt_1816_series s       ON s.ticker = w.ticker
        WHERE w.activo
        GROUP BY w.ticker, i.curva, w.curva, i.moneda_denom, i.moneda_pago, s.moneda
        ORDER BY i.moneda_pago NULLS FIRST, curva, w.ticker
    """)
    if not uni:
        print("  ✖ `research.mkt_1816_watch` está vacío (el job estaría usando el "
              "seed).\n     Corré `python -m jobs.mercado_1816_discovery --apply`.\n")
        return []

    # Una fila por (ticker, moneda) → se pliega a una por ticker con el desglose.
    bonos: dict[str, dict] = {}
    for r in uni:
        b = bonos.setdefault(r["ticker"], {**r, "por_moneda": {}})
        if r["moneda"]:
            b["por_moneda"][r["moneda"]] = r["filas"]

    print(f"  {'TICKER':<10} {'CURVA':<26} {'PAGO':>5} {'OBJETIVO':>9} "
          f"{'SERIES GUARDADAS':<24} ESTADO")
    faltan: list[str] = []
    sin_ficha: list[str] = []
    listos = 0
    en_pesos = 0
    for tk, b in bonos.items():
        pago = (b["moneda_pago"] or "").strip().upper()
        objetivo = mercado_1816.moneda_series(b["moneda_pago"])
        guardadas = b["por_moneda"]
        detalle = " · ".join(f"{m}:{n}" for m, n in sorted(guardadas.items())) or "—"

        if not pago:
            estado = "?? sin ficha en el catálogo"
            sin_ficha.append(tk)
        elif objetivo == "ars":
            estado = "ok — paga en pesos"
            en_pesos += 1
        elif guardadas.get("mep"):
            estado = f"✅ MEP ({guardadas['mep']} filas)"
            listos += 1
        else:
            estado = "⚠ FALTA REBAJAR — hoy se lee al CCL"
            faltan.append(tk)

        print(f"  {tk:<10} {str(b['curva'])[:26]:<26} {pago or '—':>5} "
              f"{objetivo:>9} {detalle[:24]:<24} {estado}")

    hd = [tk for tk, b in bonos.items()
          if mercado_1816.moneda_series(b["moneda_pago"]) == "mep"]
    print(f"\n  → {len(hd)} de {len(bonos)} bonos del watch PAGAN EN DÓLARES "
          f"(los únicos que pueden estar al CCL).")
    print(f"  → {listos} {'ya tiene' if listos == 1 else 'ya tienen'} su serie en "
          f"MEP · {len(faltan)} todavía no.")
    if faltan:
        print(f"\n  ⚠ FALTA REBAJAR: {', '.join(faltan)}\n"
              f"    El comando (fuera de rueda, scopeado a los que pagan en USD):\n"
              f"      python -m jobs.mercado_1816_series --backfill --moneda mep "
              f"--desde <hace 1 año> --dry-run\n"
              f"    y sin --dry-run cuando el presupuesto que imprime cierre.\n")
    else:
        print(f"\n  ✅ LOS {len(hd)} ESTÁN EN MEP. La vista ya los lee al dólar de la\n"
              f"     casa: en el selector de SPREAD y COMPARAR dicen MEP, no CCL.\n"
              f"     Las filas viejas en `ars` siguen ahí a propósito (son el tramo\n"
              f"     que la API ya no deja rebajar) y la lectura las ignora.\n")
    if sin_ficha:
        print(f"  → ⚠ {len(sin_ficha)} sin ficha en `mkt_1816_instrumentos` "
              f"({', '.join(sin_ficha[:8])}{'…' if len(sin_ficha) > 8 else ''}):\n"
              f"     de esos no se puede DERIVAR la moneda, y caen en `ars` por\n"
              f"     default. Falta correr\n"
              f"     `python -m jobs.mercado_1816_discovery --apply --catalogo`.")
    print(f"  → {en_pesos} {'paga' if en_pesos == 1 else 'pagan'} en pesos: `ars` es "
          f"lo correcto y no se tocan. Los\n"
          "     dólar-linked entran acá — están denominados en USD pero pagan en\n"
          "     pesos, y 1816 no publica `mep` para ellos (D30O6: todo None).\n")
    return faltan or hd


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
