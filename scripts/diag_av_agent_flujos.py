"""DEBUG del cuadro de flujos del AV AGENT — qué manda 1816 y qué hacemos con eso.

Nace del GD46 (2026-08-17): nuestra paridad daba 68,86% contra 72,78% de 1816, y
con la aritmética de pantalla el residual implícito de ellos es ~94,8 mientras
nosotros usamos 100. Nuestro conversor de la rama `soberanos` **no escribe**
`residual_previo_pct`, así que `engines/curvas.py` lo defaultea a 100 y la paridad
sale igual al precio en dólares.

Lo que NO se puede decidir sin ver la respuesta cruda (REGLA #2):

  ¿el cuadro que manda 1816 viene por 100 de VN ORIGINAL o ya renormalizado al
  residual VIVO? Y si es lo segundo, ¿en qué campo viaja el residual?

Este script no escribe nada. Imprime:
  1. el cashflow crudo (claves, primeras/últimas filas, Σ amortizaciones)
  2. qué NOMBRES DE CAMPO extra acepta la API (probados de a UNO — 1816 rechaza la
     llamada entera si uno no existe, la trampa ya pagada en jobs/tamar_1816)
  3. los indicadores en `ars` y en `mep`, con el valor técnico IMPLÍCITO
     (precio / paridad) de cada uno
  4. lo que produce nuestro `convertir_flujos` y qué paridad daría cada candidato

⚠️ CRÉDITOS: `cashflow` cuesta **1 por cupón**. GD46 tiene 51 → una corrida sale
~110 créditos. El sondeo de campos se hace SOLO contra el bono corto (1 cupón).

    python -m scripts.diag_av_agent_flujos                # GD46 + D30O6
    python -m scripts.diag_av_agent_flujos AL30 TX26      # otros
"""

from __future__ import annotations

import sys

from api.services.av_agent_alta import convertir_flujos
from core import mercado_1816 as m1816

# Sondeo de vocabulario: nombres PLAUSIBLES del residual / valor técnico. No son
# los nuestros — son los que usaría una API que ya publica `paridad`.
CANDIDATOS_CASHFLOW = (
    "residual", "residualPrevio", "valorResidual", "saldoResidual",
    "amortizacionResidual", "flujoResidual", "valorTecnico", "vidaRemanente",
)
CANDIDATOS_INDICADORES = (
    "valorTecnico", "residual", "valorResidual", "interesCorrido",
    "valorNominal", "amortizado",
)

# Ramas por ticker conocido — solo para que el conversor haga lo mismo que el
# agente. Si el ticker no está, se prueban las dos que importan.
RAMAS = ("soberanos", "dolar_linked")


def _probar_campos(fn, candidatos: tuple[str, ...]) -> dict[str, bool]:
    """De a UNO: la API rechaza la llamada ENTERA si un campo no existe."""
    out = {}
    for c in candidatos:
        try:
            fn(c)
            out[c] = True
        except Exception as e:
            out[c] = False
            print(f"      {c:24s} ✘ {type(e).__name__}: {str(e)[:90]}")
        else:
            print(f"      {c:24s} ✔ ACEPTADO")
    return out


def _cashflow(ticker: str) -> dict:
    print(f"\n{'=' * 78}\n1816 /cashflow — {ticker}\n{'=' * 78}")
    data = m1816.cashflow(ticker)
    filas = data.get("cashflow") or []
    print(f"  filas: {len(filas)}   fechaOperacion={data.get('fechaOperacion')} "
          f"plazo={data.get('plazo')}")
    if not filas:
        print("  (vacío)")
        return data
    print(f"  CLAVES de la primera fila: {sorted(filas[0].keys())}")
    print("  primeras 3:")
    for f in filas[:3]:
        print(f"    {f}")
    if len(filas) > 3:
        print("  última:")
        print(f"    {filas[-1]}")
    suma_a = sum(float(f.get("flujoAmortizacion") or 0) for f in filas)
    suma_i = sum(float(f.get("flujoInteres") or 0) for f in filas)
    print(f"  Σ amortizaciones = {suma_a:,.6f}")
    print(f"  Σ intereses      = {suma_i:,.6f}")
    return data


def _indicadores(ticker: str) -> dict[str, dict]:
    print(f"\n--- 1816 /indicadores — {ticker} (ars y mep) ---")
    res: dict[str, dict] = {}
    for moneda in ("ars", "mep"):
        try:
            d = m1816.indicadores_vigentes(
                [ticker], list(m1816.CAMPOS_INDICADORES), moneda=moneda,
                campos_dato=["precioClean", "precioDirty"])
        except Exception as e:
            print(f"  {moneda}: ✘ {type(e).__name__}: {str(e)[:120]}")
            continue
        inst = (d.get("instrumentos") or {}).get(ticker) or {}
        if not inst:
            inst = next(iter((d.get("instrumentos") or {}).values()), {}) or {}
        res[moneda] = inst
        par = inst.get("paridad")
        clean, dirty = inst.get("precioClean"), inst.get("precioDirty")
        print(f"  {moneda}: paridad={par} clean={clean} dirty={dirty} "
              f"tea={inst.get('tea')} duration={inst.get('duration')} "
              f"convencionTna={inst.get('convencionTna')} fecha={d.get('fechaOperacion')}")
        # El valor técnico IMPLÍCITO por cada precio. El que cierre parejo entre
        # las dos monedas (dividido por su TC) es el bueno.
        for etiqueta, px in (("clean", clean), ("dirty", dirty)):
            if par and px:
                print(f"      VT implícito por {etiqueta}: {float(px) / float(par):,.4f}")
    return res


def _nuestro(ticker: str, filas: list[dict]) -> None:
    print(f"\n--- NUESTRO convertir_flujos — {ticker} ---")
    for rama in RAMAS:
        conv = convertir_flujos(filas, rama)
        f0 = (conv["flujos"] or [{}])[0]
        print(f"  rama {rama:13s} escala={conv['escala']:10s} "
              f"Σ={conv['suma_amort']:,.6f} n={conv['n']}")
        print(f"      primer flujo: {f0}")
        print(f"      residual_previo_pct del primer flujo: "
              f"{f0.get('residual_previo_pct', 'NO LO ESCRIBIMOS → el motor usa 100')}")


def main() -> None:
    if not m1816.disponible():
        print("1816 no disponible (falta la API key). Nada que medir.")
        return
    tickers = [t.strip().upper() for t in sys.argv[1:]] or ["GD46", "D30O6"]

    for tk in tickers:
        try:
            data = _cashflow(tk)
        except Exception as e:
            print(f"  ✘ cashflow falló: {type(e).__name__}: {str(e)[:140]}")
            continue
        filas = data.get("cashflow") or []
        _indicadores(tk)
        _nuestro(tk, filas)

    # Sondeo de vocabulario contra el bono MÁS CORTO de la lista — el cashflow
    # cuesta 1 crédito por cupón y no hace falta pagarlo 51 veces para saber si un
    # nombre de campo existe.
    corto = tickers[-1]
    print(f"\n{'=' * 78}\nSONDEO DE CAMPOS (de a uno) — usando {corto}\n{'=' * 78}")
    print("  /cashflow:")
    _probar_campos(
        lambda c: m1816.cashflow(corto, [*m1816.CAMPOS_CASHFLOW, c]),
        CANDIDATOS_CASHFLOW)
    print("  /indicadores:")
    _probar_campos(
        lambda c: m1816.indicadores_vigentes(
            [corto], [*m1816.CAMPOS_INDICADORES, c], moneda="ars",
            campos_dato=["precioClean"]),
        CANDIDATOS_INDICADORES)

    print("\nQué mirar:")
    print("  · Si Σ amortizaciones ≈ 100 en un bono QUE YA AMORTIZÓ, 1816 manda el")
    print("    cuadro renormalizado al residual vivo → el residual NO sale de ahí.")
    print("  · El VT implícito de las dos monedas, dividido por su TC, tiene que dar")
    print("    lo mismo. Si no, la paridad de 1816 no es comparable entre monedas.")
    print("  · Si algún campo del sondeo sale ✔, ESE es el residual que nos falta.")


if __name__ == "__main__":
    main()
