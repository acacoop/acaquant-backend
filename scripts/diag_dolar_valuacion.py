"""`scripts/diag_dolar_valuacion.py` — **CON QUÉ DÓLAR VALUAMOS, Y CUÁNTO CAMBIA.**

Read-only. **No escribe una sola fila.** Doc: `docs/AGENT.md` §0.ec.

## El hallazgo que este diag mide

Un bono hard dólar que cotiza EN PESOS hay que pasarlo a dólares antes de
calcular la tasa. **Con qué dólar se divide no es un detalle: ES la tasa.**

    NOSOTROS  dividimos por MEP   (`engines/curvas.py::precio_soberano_a_usd`)
    1816      divide  por CCL     (su spec, textual: «para instrumentos pagaderos
                                   en moneda distinta a ARS, para calcular
                                   indicadores las cotizaciones se dividen por
                                   CCL»)

Está escrito en `core/mercado_1816.py` desde el 2026-08-17 —lo dejó el episodio
de GD46, 202 bps— y nunca se actuó sobre eso. Medido de nuevo el 2026-09-07
sobre cuatro ONs, el tipo de cambio implícito de cada uno:

    nuestro   1.525,4   (idéntico en los cuatro → es un dólar de la casa)
    de 1816   1.589,5   (+4,20%)

Y todo lo demás sale de ahí: 136 / 158 / 215 bps de diferencia en la TEA. El caso
que lo grita es **LMS8O**: nosotros −13,44% y 1816 −0,01%. Un bono que rinde cero
mostrado como si perdiera 13% al año, y no falla nada.

⚠️ **NINGUNO DE LOS DOS ESTÁ «MAL»: son dos preguntas distintas.** La TEA en MEP
es lo que rinde para alguien que liquida contra MEP; la TEA en CCL es la que
cotiza el mercado y la que publican los brokers. Lo que sí está mal es que la
pantalla diga «TEA» a secas: **una tasa sin decir en qué dólar está no es un
número.**

## Qué hace

Corre el MOTOR DE VERDAD (`engines.curvas.calcular_campos`, no una copia) DOS
veces sobre cada bono hard dólar con precio: una con MEP y otra con CCL. Tabula
la diferencia en bps y en paridad.

**Cero créditos de 1816 y cero escrituras**: todo sale de `mercado.curvas`,
`mercado.market_snapshot` y el snapshot de dólares que ya tenemos.

    python -m scripts.diag_dolar_valuacion
    python -m scripts.diag_dolar_valuacion --todos     # sin cortar la tabla
"""
from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime

# Ramas cuyo precio en pesos se divide por un dólar para valuar. Son las que
# `precio_soberano_a_usd` toca, y por eso son las únicas afectadas.
RAMAS_HARD_DOLAR = ("soberanos", "on")


def _titulo(s: str) -> None:
    print(f"\n{'═' * 78}\n {s}\n{'═' * 78}")


def _pct(v, d: int = 2) -> str:
    return f"{float(v):.{d}%}" if isinstance(v, int | float) else "—"


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--todos", action="store_true", help="no cortar la tabla")
    a = p.parse_args()

    from api.services.macro import get_ultimo_mep
    from core import curvas_sql, market_snapshot
    from engines.curvas import calcular_campos, cargar_dias_habiles, rama_calculo

    # ── 1 ──────────────────────────────────────────────────────────────────
    _titulo("1. LOS DÓLARES DE LA CASA — el insumo que decide toda la tasa")
    d = get_ultimo_mep() or {}
    mep, ccl = d.get("mep"), d.get("ccl")
    print(f"  MEP     : {mep}")
    print(f"  CCL     : {ccl}")
    print(f"  canje   : {d.get('canje')}   (CCL/MEP − 1)")
    print(f"  oficial : {d.get('oficial')}")
    print(f"  fuente  : {d.get('source')} · {d.get('timestamp')}")
    if not mep or not ccl:
        print("\n  ⚠️ falta uno de los dos: sin los dos no se puede comparar.")
        return 1
    print(f"\n  El motor usa **MEP**. 1816 usa **CCL**. Spread: "
          f"{ccl / mep - 1:+.2%}\n"
          "  Esa diferencia entra ENTERA en el precio en dólares, y de ahí a la\n"
          "  tasa dividida por los años que le quedan al bono.")

    # ── 2 ──────────────────────────────────────────────────────────────────
    _titulo("2. EL MISMO BONO, CON LOS DOS DÓLARES — corriendo el motor de verdad")
    print("  Se corre `engines.curvas.calcular_campos` DOS veces por bono, con el\n"
          "  mismo cuadro y el mismo precio: sólo cambia el tipo de cambio. Todo\n"
          "  lo que se mueva es exclusivamente eso.\n")

    docs = [c for c in (curvas_sql.cargar_todos() or [])
            if rama_calculo(c) in RAMAS_HARD_DOLAR
            and (c.get("moneda_flujo") or "USD").upper() == "USD"]
    if not docs:
        print("  no hay bonos hard dólar en el master.")
        return 0

    simbolos = [c.get("ticker") for c in docs if c.get("ticker")]
    try:
        snap = market_snapshot.cols_map(simbolos, ["last_price"])
    except Exception as e:
        print(f"  ⚠️ no pude leer el snapshot: {e}")
        return 1
    habiles = cargar_dias_habiles()
    ahora = datetime.now(UTC)

    filas = []
    sin_precio = 0
    for doc in docs:
        sim = doc.get("ticker") or ""
        px = (snap.get(sim) or {}).get("last_price")
        if not px or px <= 0:
            sin_precio += 1
            continue
        tick = {"price": float(px), "timestamp": ahora}
        con_mep = calcular_campos(tick, doc, {}, habiles, mep=mep) or {}
        con_ccl = calcular_campos(tick, doc, {}, habiles, mep=ccl) or {}
        t_mep, t_ccl = con_mep.get("TEA"), con_ccl.get("TEA")
        if t_mep is None or t_ccl is None:
            continue
        filas.append({
            "ticker": doc.get("ticker_corto") or sim,
            "curva": doc.get("curva"),
            "precio": float(px),
            "tea_mep": t_mep, "tea_ccl": t_ccl,
            "bps": (t_ccl - t_mep) * 10_000,
            "par_mep": con_mep.get("paridad"), "par_ccl": con_ccl.get("paridad"),
            # El que cambia de SIGNO es el que hace que alguien tome una decisión
            # al revés: «pierde» contra «rinde».
            "cambia_signo": (t_mep < 0) != (t_ccl < 0),
        })

    filas.sort(key=lambda f: abs(f["bps"]), reverse=True)
    print(f"  {len(filas)} bono(s) con precio · {sin_precio} sin precio en el snapshot\n")
    print(f"  {'TICKER':<9} {'CURVA':<14} {'TEA con MEP':>12} {'TEA con CCL':>12} "
          f"{'Δ bps':>9}  {'PARIDAD MEP':>11} {'CCL':>8}")
    print("  " + "─" * 84)
    for f in (filas if a.todos else filas[:30]):
        marca = "  ⚠ CAMBIA DE SIGNO" if f["cambia_signo"] else ""
        print(f"  {f['ticker']:<9} {str(f['curva'])[:14]:<14} "
              f"{_pct(f['tea_mep'], 4):>12} {_pct(f['tea_ccl'], 4):>12} "
              f"{f['bps']:>+9.0f}  {f['par_mep']:>11.2f} {f['par_ccl']:>8.2f}{marca}")
    if not a.todos and len(filas) > 30:
        print(f"     … y {len(filas) - 30} más (--todos)")

    # ── 3 ──────────────────────────────────────────────────────────────────
    _titulo("3. EL TAMAÑO DEL PROBLEMA")
    if filas:
        bps = [f["bps"] for f in filas]
        signo = [f for f in filas if f["cambia_signo"]]
        print(f"  bonos afectados         : {len(filas)}")
        print(f"  diferencia media        : {sum(bps) / len(bps):+.0f} bps")
        print(f"  diferencia máxima       : {max(bps, key=abs):+.0f} bps")
        print(f"  cambian de SIGNO        : {len(signo)}"
              + (f" → {', '.join(f['ticker'] for f in signo[:10])}" if signo else ""))
        print("\n  ⚠️ Los que cambian de signo son los graves: con un dólar «pierde»\n"
              "     y con el otro «rinde». Nadie mira dos veces un número plausible.")

    _titulo("LA DECISIÓN, QUE NO ES TÉCNICA")
    print("  Ninguno de los dos dólares está mal — son DOS PREGUNTAS:\n"
          "   · TEA en MEP → lo que rinde para quien liquida contra MEP.\n"
          "   · TEA en CCL → la que cotiza el mercado, la que publica 1816 y la\n"
          "     que usa cualquier broker. Es la comparable hacia afuera.\n\n"
          "  Lo que SÍ está mal hoy es que la pantalla diga «TEA» a secas: una\n"
          "  tasa sin decir en qué dólar está no es un número. Sea cual sea la\n"
          "  que elija la mesa, tiene que estar ESCRITA al lado.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
