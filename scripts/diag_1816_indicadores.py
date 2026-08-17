"""scripts/diag_1816_indicadores.py — QUÉ acepta de verdad `/indicadores` de 1816.

Doc madre: `docs/AV_AGENT.md` · `docs/RENTA_FIJA.md` paso 18.

**Nació para adivinar menos y quedó como banco de pruebas.** La primera corrida
cerró las dos preguntas que lo motivaron —qué monedas y qué campos acepta la
API— y el OpenAPI (`/v1/doc/openapi.json`) confirmó las respuestas. Sobrevive
porque el contrato de un proveedor **cambia sin avisar**, y correr esto cuesta
~100 créditos de 100.000: es más barato preguntarle a la API que descubrir en
producción que un campo dejó de existir.

**Lo que ya contestó (2026-08-17):**

- **`moneda` es `ars | ccl | mep`.** No existe `usd`. Y con el default `ars`,
  para un bono pagadero en dólares 1816 **divide las cotizaciones por CCL**
  mientras NUESTRO motor divide por MEP — esa, y no la fórmula, fue la
  explicación de los 202 bps de GD46.
- **`precioDirty` es el precio de MERCADO.** Para GD46, `precioDirty / paridad`
  da un valor técnico con un TC implícito de ~1.436 (plausible) y `precioClean`
  da ~1.570: el que cierra con la paridad que ellos mismos publican es el dirty,
  que además es lo que cotiza en el mercado argentino y lo que da Primary.

**⚠️ Se prueba DE A UNO.** La API **rechaza la llamada entera** si un solo campo
no existe (verificado el 2026-08-16 con `margen`/`margin`/`spreadTamar`). En lote
no se sabe cuál falló: se sabe que falló todo.

**Solo lee.** No escribe una fila en ninguna tabla.

    python -m scripts.diag_1816_indicadores                     # monedas + campos
    python -m scripts.diag_1816_indicadores --solo-monedas      # más barato
    python -m scripts.diag_1816_indicadores --precio 104500     # + input manual
"""
from __future__ import annotations

import argparse

from core import mercado_1816

# **Ya relevadas contra el OpenAPI (2026-08-17): el enum es `ars | ccl | mep`.**
# No hay `usd`. Y la diferencia NO es de formato: el spec dice que para un
# instrumento pagadero en moneda distinta a ARS las cotizaciones **se dividen por
# CCL** con el default `ars`, mientras NUESTRO motor divide por MEP. Se siguen
# probando las tres para poder VER esa diferencia en números, que es lo que
# convierte una nota del spec en una explicación de los 202 bps.
MONEDAS = list(mercado_1816.MONEDAS)

# Los 6 que ya se usan en producción (`jobs/tamar_1816`, `jobs/mercado_1816_series`).
CAMPOS_CONOCIDOS = ["tea", "tna", "spread", "precioClean", "duration", "paridad"]
# El enum COMPLETO del spec — ya no hay que adivinar cuáles existen.
CAMPOS_CANDIDATOS = [c for c in mercado_1816.CAMPOS_INDICADORES
                     if c not in CAMPOS_CONOCIDOS]


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


def probar_input_manual(ticker: str, precio: float, moneda: str) -> None:
    """`/indicadores/{ticker}` — SU tasa a NUESTRO precio.

    Es el control cruzado que elimina el precio como variable. Si con el MISMO
    número las dos tasas coinciden, la conversión del cuadro está bien y lo único
    que separaba a las dos cuentas era el insumo."""
    print("\n" + "=" * 78)
    print(f"INPUT MANUAL — la TEA de 1816 a NUESTRO precio ({ticker} @ {precio}, "
          f"moneda={moneda})")
    print("=" * 78)
    try:
        r = mercado_1816.indicadores_de(
            ticker, ["tea", "paridad", "convencionTna", "duration"],
            moneda=moneda, precioDirty=precio)
        print(f"  ✔ {r.get('indicadores')}")
        print("  → esta es la tasa comparable: misma entrada, distinta cuenta.")
    except Exception as e:
        print(f"  ✘ {type(e).__name__}: {e}"[:300])


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
    ap.add_argument("--precio", type=float, default=None,
                    help="probar el endpoint de INPUT MANUAL con este precio "
                         "(dirty). Sin esto ese bloque se saltea.")
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
    if args.precio:
        probar_input_manual(tickers[0], args.precio, args.moneda)

    print("\n" + "=" * 78)
    print("QUÉ HACER CON ESTO")
    print("=" * 78)
    print("  1. `mep` vs `ars` para un bono USD: si dan TEAs distintas, esa es la "
          "diferencia de tipo de cambio (ellos CCL por default, nosotros MEP).")
    print("  2. `precioDirty` es el precio de MERCADO (el comparable con Primary); "
          "`precioClean` no cierra con la paridad que ellos mismos publican.")
    print("  3. El INPUT MANUAL (--precio) es el cotejo definitivo: su fórmula "
          "sobre nuestro número. Lo que quede ahí es convención, nada más.")


if __name__ == "__main__":
    main()
