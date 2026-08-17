"""scripts/diag_1816_indicadores.py — QUÉ acepta de verdad `/indicadores` de 1816.

Doc madre: `docs/AV_AGENT.md` · `docs/RENTA_FIJA.md` paso 18.

**Por qué existe.** El AV Agent pide 4 campos y se quedó corto en dos frentes:

1. **La MONEDA.** Se le pasó `moneda="usd"` para un bono en dólares y GD46 volvió
   con `Error1816` — o sea que ese valor NO se acepta, o se escribe distinto. Y
   antes de eso, con el default `ars`, GD46 devolvió `precioClean = 114.247`
   (un global cotiza ~60-90 por 100 VN): **el precio venía en PESOS**, nuestro
   motor lo dividió por NUESTRO MEP y la TEA salió a 202 bps de la de ellos.
   Sin saber qué monedas acepta la API no se puede cerrar ese caso.

2. **Los CAMPOS.** Hoy se piden 4 de una lista que nadie relevó. Como el
   simulador **no persiste nada**, traer más campos es gratis en riesgo y podría
   cerrar el diagnóstico solo (¿su precio es clean o dirty? ¿publican TIR? ¿valor
   técnico? ¿intereses corridos?).

**⚠️ Por eso se prueba DE A UNO.** La API **rechaza la llamada entera** si un solo
campo no existe (verificado el 2026-08-16 con `margen`/`margin`/`spreadTamar`).
En lote no se sabe cuál falló: se sabe que falló todo.

**Solo lee.** No escribe una fila en ninguna tabla. Costo ≈ 1 crédito por prueba
(ticker × campo), sobre 100.000 diarios — la corrida completa ronda los 80.

    python -m scripts.diag_1816_indicadores                    # default GD46 + TZXM8
    python -m scripts.diag_1816_indicadores --tickers AL30,TX28
    python -m scripts.diag_1816_indicadores --solo-monedas     # más barato
"""
from __future__ import annotations

import argparse

from core import mercado_1816

# Candidatos a MONEDA. Los dos primeros son los que el código usa hoy; el resto
# son variantes de grafía —no se adivina cuál anda, se prueban todas.
MONEDAS = ["ars", "usd", "ARS", "USD", "Ars", "Usd", "dolar", "pesos"]

# Candidatos a CAMPO. Los 6 primeros están VERIFICADOS en producción
# (`jobs/tamar_1816` y `jobs/mercado_1816_series` los piden todos los días); el
# resto son hipótesis a probar. Se listan agrupados por qué pregunta contestan.
CAMPOS_CONOCIDOS = ["tea", "tna", "spread", "precioClean", "duration", "paridad"]
CAMPOS_CANDIDATOS = [
    # precio: ¿publican el SUCIO? Es la pregunta que decide si su `precioClean`
    # es comparable con el `last_price` de Primary (que en ARG viene con
    # intereses corridos incluidos).
    "precioDirty", "precioSucio", "precio", "precioTecnico", "valorTecnico",
    "interesesCorridos", "valorResidual",
    # tasa: otras formas de la misma pregunta
    "tir", "tirReal", "ytm", "tem", "tna360", "tirUsd",
    # riesgo
    "durationModificada", "modifiedDuration", "dm", "convexidad", "convexity",
    # actividad — sirve para saber si el precio es de un trade real o teórico
    "volumen", "montoOperado", "cantidadOperaciones", "ultimoPrecio", "cierre",
    "apertura", "maximo", "minimo", "variacion",
    # ajuste
    "cer", "coeficiente", "tasaCupon", "cupon",
]


def _probe(fn, etiqueta: str) -> tuple[bool, str]:
    """Corre una prueba y NUNCA levanta. Devuelve `(anduvo, detalle)`."""
    try:
        r = fn()
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"[:160]
    inst = (r or {}).get("instrumentos") or {}
    valores = {t: v for t, v in inst.items() if v and any(x is not None for x in v.values())}
    if not valores:
        return True, f"acepta {etiqueta} pero devolvió TODO NULL"
    return True, "; ".join(f"{t}={v}" for t, v in list(valores.items())[:3])[:220]


def probar_monedas(tickers: list[str]) -> list[str]:
    """Qué valores de `moneda` acepta la API — y con qué precio contesta cada uno.

    Las dos cosas importan: que no explote NO alcanza, porque el bug de GD46 fue
    que `ars` aceptó feliz y devolvió el precio en la moneda equivocada."""
    print("\n" + "=" * 78)
    print("MONEDAS — ¿cuáles acepta, y qué precio devuelve cada una?")
    print("=" * 78)
    aceptadas = []
    for m in MONEDAS:
        ok, det = _probe(
            lambda m=m: mercado_1816.indicadores_vigentes(
                tickers, ["precioClean", "tea"], moneda=m),
            f"moneda={m}")
        print(f"  {'✔' if ok else '✘'} moneda={m:<8} {det}")
        if ok:
            aceptadas.append(m)
    print(f"\n  → aceptadas: {aceptadas or 'NINGUNA'}")
    print("  → si dos monedas dan PRECIOS distintos para el mismo ticker, ESA es "
          "la explicación de una TEA divergente: no es la fórmula, es el insumo.")
    return aceptadas


def probar_campos(tickers: list[str], moneda: str) -> list[str]:
    """Campo por campo, porque la API rechaza la llamada entera si uno no existe."""
    print("\n" + "=" * 78)
    print(f"CAMPOS — de a uno (moneda={moneda}). ✔ = existe · ✘ = HTTP 400")
    print("=" * 78)
    existen = []
    for campo in CAMPOS_CONOCIDOS + CAMPOS_CANDIDATOS:
        conocido = campo in CAMPOS_CONOCIDOS
        ok, det = _probe(
            lambda c=campo: mercado_1816.indicadores_vigentes(
                tickers, [c], moneda=moneda),
            campo)
        marca = "✔" if ok else "✘"
        etiqueta = " (ya usado)" if conocido and ok else ""
        print(f"  {marca} {campo:<22}{etiqueta} {det}")
        if ok:
            existen.append(campo)
    nuevos = [c for c in existen if c not in CAMPOS_CONOCIDOS]
    print(f"\n  → existen {len(existen)} campos; **{len(nuevos)} NUEVOS**: {nuevos}")
    print("  → nada de esto se persiste: son insumos del simulador, así que sumar "
          "los útiles a `_CAMPOS_REF` de av_agent_alta es gratis en riesgo.")
    return existen


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tickers", default="GD46,TZXM8",
                    help="separados por coma. Default: un global USD y un CER ARS, "
                         "que son los dos casos que fallaron distinto")
    ap.add_argument("--solo-monedas", action="store_true",
                    help="salteá la prueba de campos (mucho más barata)")
    ap.add_argument("--moneda", default="ars",
                    help="con qué moneda probar los campos (default ars, el que "
                         "sabemos que anda)")
    args = ap.parse_args()

    if not mercado_1816.disponible():
        print("✗ falta MERCADO_1816_API_KEY en el .env")
        return

    tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
    n_pruebas = len(MONEDAS) * 2 + (0 if args.solo_monedas
                                    else len(CAMPOS_CONOCIDOS) + len(CAMPOS_CANDIDATOS))
    print(f"Tickers: {tickers}")
    print(f"Costo estimado: ~{n_pruebas * len(tickers)} créditos (de 100.000/día). "
          "SOLO LECTURA — no escribe nada.")

    probar_monedas(tickers)
    if not args.solo_monedas:
        probar_campos(tickers, args.moneda)

    print("\n" + "=" * 78)
    print("QUÉ HACER CON ESTO")
    print("=" * 78)
    print("  1. Si alguna moneda devuelve el precio en la moneda DEL BONO, esa es "
          "la que tiene que pedir `_referencia_1816`.")
    print("  2. Si NINGUNA lo hace, el precio siempre viene en pesos y hay que "
          "dividir por el MEP a propósito — dejando dicho cuál se usó.")
    print("  3. Los campos nuevos útiles se suman a `_CAMPOS_REF`; el simulador "
          "no persiste nada, así que solo mejora el diagnóstico.")


if __name__ == "__main__":
    main()
