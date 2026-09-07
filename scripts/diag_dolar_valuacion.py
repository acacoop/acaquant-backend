"""`scripts/diag_dolar_valuacion.py` — **CON QUÉ DÓLAR VALUAMOS, Y CUÁNTO CAMBIA.**

Read-only. **No escribe una sola fila.** Doc: `docs/AGENT.md` §0.ec y §0.ed.

## La pregunta, bien planteada

Un bono hard dólar que cotiza EN PESOS hay que pasarlo a dólares antes de
calcular la tasa. **Con qué dólar se divide no es un detalle: ES la tasa.**

    NOSOTROS  dividimos por MEP   (`engines/curvas.py::precio_soberano_a_usd`)
    1816      divide  por CCL     (su spec, textual)

⚠️⚠️ **Y EL MEP ES EL CORRECTO** (user, 2026-09-07: *«los bonos pagan en USD, o
sea vos recibís MEP no CCL»*). Tiene razón, y el argumento es de flujos:

    pagás PESOS por la pata O  →  el bono te paga DÓLARES en tu cuenta local
    tu alternativa con esos pesos era comprar dólar MEP
    ⇒ el rendimiento en dólares se calcula contra el MEP

El CCL son dólares AFUERA: de este bono no salen, salvo que además hagas el
canje. Que 1816 use CCL es una CONVENCIÓN —homogeneiza todo el mercado bajo un
solo tipo de cambio— y no una afirmación sobre los flujos de este bono. Así que
la diferencia de 136-215 bps contra ellos **no es un error nuestro que haya que
corregir: es una diferencia de convención que hay que ESCRIBIR.**

## Lo que sí puede estar mal, y es más fino

**Si la pata D del bono cotiza, no hace falta ningún tipo de cambio.** El precio
de la D ya está en dólares y los flujos también: la tasa sale sin convención
ninguna, y es la que un trader puede ejecutar de verdad.

Hoy el motor divide la pata O por un MEP GENÉRICO (el del AL30) aunque la D del
propio bono esté operando. Eso mete el canje de OTRO papel adentro de la tasa de
éste. `precio_soberano_a_usd` ya sabe no convertir cuando el símbolo termina en
D o C — el problema es que en `mercado.curvas` el `ticker` guardado es la pata en
pesos, así que nunca toma ese camino.

**Este diag mide las dos cosas**: cuánto se movería la tasa con CCL (para saber
de dónde salen los bps contra 1816) y cuánto se movería usando la pata D del
propio bono (que es el camino sin convención).

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
    from core import curvas_sql, especies, instrumentos_validos, market_snapshot
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
    if not mep:
        print("\n  ⚠️ sin MEP no se puede valuar NADA en dólares. Ese es el problema.")
        return 1
    if not ccl:
        # ⚠️ HALLAZGO, no un fin de programa. El CCL sale de UNA sola cuenta
        # (`engines/dolares.py`: AL30 offer ÷ AL30C bid). Si esa punta se vacía
        # —el AL30C es mucho menos líquido que el AL30D— el CCL desaparece, y con
        # él el canje y cualquier comparación contra el mercado. **Y nada avisa.**
        print("\n  ⚠️⚠️ **NO HAY CCL**, y eso es un hallazgo en sí mismo.\n"
              "  Sale de UNA cuenta: AL30 (offer) ÷ AL30C (bid), en\n"
              "  `engines/dolares.py`. Si la punta compradora del AL30C se vacía,\n"
              "  el CCL queda en None — y con él el canje. El MEP sobrevive porque\n"
              "  el AL30D es mucho más líquido.\n"
              "  Nada avisa de esto hoy: la casa se queda sin CCL y sin canje en\n"
              "  silencio. El bloque 2 igual corre, sin la columna de CCL.")
    else:
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
    fichas_primary = instrumentos_validos.fichas() or []
    # Todas las patas USD de la foto, de una sola consulta al snapshot.
    usd = [f.get("simbolo") for f in fichas_primary
           if (f.get("moneda") or "").upper() == "USD" and f.get("simbolo")]
    try:
        snap_usd = market_snapshot.cols_map(usd, ["last_price"]) if usd else {}
    except Exception:
        snap_usd = {}

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
        t_mep = con_mep.get("TEA")
        if t_mep is None:
            continue
        t_ccl = None
        if ccl:
            t_ccl = (calcular_campos(tick, doc, {}, habiles, mep=ccl) or {}).get("TEA")

        # ── LA PATA D: el camino SIN tipo de cambio ─────────────────────────
        # Se busca por FICHA (`especies.hermanas_por_ficha` → `core/pareo`), no
        # por string: los tickers están topeados en 5 caracteres y `AL30→AL30D`
        # anda por casualidad (REGLA #9). Si la D cotiza, su precio YA está en
        # dólares y la tasa sale sin convención ninguna.
        t_d = px_d = fx_bono = None
        corto = (doc.get("ticker_corto") or "").strip().upper()
        for h in (especies.hermanas_por_ficha(corto, fichas_primary, moneda="USD")
                  if fichas_primary else []):
            cand = (snap_usd.get(h["simbolo"]) or {}).get("last_price")
            if cand and cand > 0:
                px_d = float(cand)
                fx_bono = float(px) / px_d
                t_d = (calcular_campos({"price": px_d, "timestamp": ahora},
                                       {**doc, "ticker": h["simbolo"]},
                                       {}, habiles, mep=mep) or {}).get("TEA")
                break

        filas.append({
            "ticker": corto or sim,
            "curva": doc.get("curva"),
            "precio": float(px),
            "tea_mep": t_mep, "tea_ccl": t_ccl, "tea_d": t_d,
            "px_d": px_d, "fx_bono": fx_bono,
            "bps": ((t_ccl - t_mep) * 10_000) if t_ccl is not None else None,
            "bps_d": ((t_d - t_mep) * 10_000) if t_d is not None else None,
            "par_mep": con_mep.get("paridad"),
            # El que cambia de SIGNO es el que hace que alguien tome una decisión
            # al revés: «pierde» contra «rinde».
            "cambia_signo": (t_d is not None and (t_mep < 0) != (t_d < 0)),
        })

    # Se ordena por el gap contra la PATA D, que es el que importa: el de CCL es
    # convención, el de la D es la tasa que se puede ejecutar de verdad.
    filas.sort(key=lambda f: abs(f["bps_d"] or f["bps"] or 0), reverse=True)
    con_d = [f for f in filas if f["tea_d"] is not None]
    print(f"  {len(filas)} bono(s) con precio · {sin_precio} sin precio · "
          f"{len(con_d)} con pata D cotizando\n")
    print(f"  {'TICKER':<9} {'TEA con MEP':>12} {'TEA con CCL':>12} "
          f"{'TEA pata D':>12} {'Δ vs D':>8}  {'FX del bono':>11} {'vs MEP':>8}")
    print("  " + "─" * 84)
    for f in (filas if a.todos else filas[:30]):
        marca = "  ⚠ CAMBIA DE SIGNO" if f["cambia_signo"] else ""
        fx = f["fx_bono"]
        d_bps = f"{f['bps_d']:+.0f}" if f["bps_d"] is not None else "—"
        fx_txt = f"{fx:,.1f}" if fx else "—"
        fx_gap = f"{fx / mep - 1:+.2%}" if fx else "—"
        print(f"  {f['ticker']:<9} {_pct(f['tea_mep'], 4):>12} "
              f"{_pct(f['tea_ccl'], 4):>12} {_pct(f['tea_d'], 4):>12} "
              f"{d_bps:>8}  {fx_txt:>11} {fx_gap:>8}{marca}")
    if not a.todos and len(filas) > 30:
        print(f"     … y {len(filas) - 30} más (--todos)")

    # ── 3 ──────────────────────────────────────────────────────────────────
    _titulo("3. EL TAMAÑO DEL PROBLEMA")
    if con_d:
        bps = [f["bps_d"] for f in con_d]
        signo = [f for f in con_d if f["cambia_signo"]]
        fxs = [f["fx_bono"] for f in con_d if f["fx_bono"]]
        print(f"  bonos con pata D cotizando : {len(con_d)} de {len(filas)}")
        print(f"  diferencia media vs pata D : {sum(bps) / len(bps):+.0f} bps")
        print(f"  diferencia máxima          : {max(bps, key=abs):+.0f} bps")
        if fxs:
            print(f"  FX implícito de los bonos  : {min(fxs):,.1f} a {max(fxs):,.1f} "
                  f"(MEP genérico: {mep:,.1f})")
        print(f"  cambian de SIGNO           : {len(signo)}"
              + (f" → {', '.join(f['ticker'] for f in signo[:10])}" if signo else ""))
        print("\n  ⚠️ El FX implícito de cada bono ES su propio canje. Si el rango es\n"
              "     ancho, dividir a todos por el MEP del AL30 le mete a cada bono el\n"
              "     canje de OTRO papel. Si es angosto y pegado al MEP, el atajo es\n"
              "     inofensivo — y eso hay que MEDIRLO, no suponerlo.")
    else:
        print("  Ningún bono tiene su pata D con precio en el snapshot ahora.\n"
              "  Sin eso no se puede comparar contra el camino sin convención:\n"
              "  correlo en rueda.")

    _titulo("QUÉ SE DECIDE Y QUÉ NO")
    print("  · **El MEP se queda.** Un hard dólar te paga dólares LOCALES, y tu\n"
          "    alternativa con esos pesos era comprar MEP. Que 1816 use CCL es una\n"
          "    convención suya para homogeneizar el mercado, no una afirmación\n"
          "    sobre los flujos de este bono. Los 136-215 bps contra ellos son\n"
          "    ESO, y hay que escribirlo, no perseguirlo.\n")
    print("  · **Lo que sí hay que decidir es la pata.** Si la D del propio bono\n"
          "    cotiza, su precio YA está en dólares: la tasa sale sin ningún tipo\n"
          "    de cambio y es la que se puede ejecutar. Hoy dividimos la pata en\n"
          "    pesos por un MEP genérico aunque la D esté operando.\n")
    print("  · **Y sin CCL no hay canje.** Sale de una sola cuenta y hoy vino en\n"
          "    None. Eso es un agujero propio, independiente de todo lo demás.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
