"""¿En qué BASE está escrito un cronograma CER? — el caso DICP (2026-08-17).

**El síntoma.** PARP cerró perfecto contra 1816 (TEA 0 bps, duration idéntica) y
DICP no: TEA 3,92% contra 9,25% al MISMO precio, paridad 86,57% contra 90,19%,
duration 3,4756 contra 3,2512. La única diferencia estructural entre los dos es
que **DICP ya empezó a amortizar y PARP no** (sus amortizaciones arrancan en
2029). Eso apunta a la base sobre la que está escrito el cuadro, no al cuadro.

**La pregunta que hay que contestar antes de tocar una línea.** Un cronograma de
un bono amortizante se puede escribir en DOS bases, y las dos son correctas
mientras no se mezclen:

    BASE ORIGINAL   los % son sobre el VN de EMISIÓN. Σ de TODAS las
                    amortizaciones = 100. El residual vivo es < 100 y la
                    PARIDAD tiene que dividir por él.
    BASE RESIDUAL   los % son sobre lo que queda VIVO hoy. Σ de las
                    amortizaciones FUTURAS = 100. La paridad divide por 100.

`engines/curvas.py` hoy hace una mezcla: los flujos los toma como vengan y la
paridad la calcula SIEMPRE contra `valor_nominal × ratio` (o sea, asume BASE
RESIDUAL), mientras que `convertir_flujos` normaliza por la Σ del cuadro COMPLETO
que manda 1816 (o sea, escribe BASE ORIGINAL). Para un bono que no amortizó nada
las dos bases coinciden y no se nota — **por eso PARP dio perfecto**.

Pero cuál de las dos es la correcta NO se decide leyendo código: se decide
mirando (a) en qué base están escritos los CER que la mesa ya cargó a mano y que
hoy valúan bien, y (b) qué residual usa 1816. Eso es lo que mide este script.

**No escribe nada.** Imprime:

  1. CONVENCIÓN DEL MASTER (gratis, sin API): para cada CER de `mercado.curvas`
     con cronograma, Σ de amortizacion_pct total vs futura y el
     `residual_previo_pct` del primer flujo futuro. Un bono que ya amortizó
     delata la base: si Σ TOTAL ≈ 100 es base original, si Σ FUTURA ≈ 100 es
     base residual.
  2. EL BONO CONTRA 1816 (cuesta créditos): Σ de amortizaciones pasadas vs
     futuras del cuadro de 1816, el valor técnico IMPLÍCITO de ellos
     (precio / paridad) y el residual que se deduce por TRES caminos
     independientes. Si los tres coinciden, la respuesta es esa.

⚠️ CRÉDITOS: `cashflow` cuesta **1 por cupón**. DICP tiene 60 y PARP 70 → el paso
2 sale ~130 + ~8 de indicadores. El paso 1 es GRATIS.

    python -m scripts.diag_cer_amortizado              # solo el paso 1 (gratis)
    python -m scripts.diag_cer_amortizado DICP         # 1 + 2 para DICP
    python -m scripts.diag_cer_amortizado DICP PARP    # el caso y su control
"""

from __future__ import annotations

import sys
from datetime import date


def _f(v, d=0.0) -> float:
    try:
        return float(v)
    except Exception:
        return d


# ── 1. LA CONVENCIÓN DEL MASTER (gratis) ──────────────────────────────────
def convencion_del_master() -> None:
    """En qué base están escritos los CER que la mesa YA cargó.

    Es la medición decisiva y no cuesta un crédito: los bonos que hoy valúan bien
    son la especificación de hecho. Si todos los amortizados tienen Σ FUTURA ≈
    100, el master está en base residual y el conversor tiene que renormalizar.
    """
    from core import curvas_sql

    hoy = date.today().isoformat()
    print("=" * 78)
    print("1. CONVENCIÓN DEL MASTER — CER ya cargados (sin tocar la API de 1816)")
    print("=" * 78)
    filas = []
    for d in curvas_sql.cargar_todos():
        if (d.get("ajuste") or d.get("curva")) != "cer":
            continue
        flujos = d.get("flujos") or []
        if not flujos:
            continue
        tot = sum(_f(f.get("amortizacion_pct")) for f in flujos)
        fut_fl = [f for f in flujos if str(f.get("fecha") or "") > hoy]
        fut = sum(_f(f.get("amortizacion_pct")) for f in fut_fl)
        if tot <= 0:
            continue
        # `residual_previo_pct` del primer flujo FUTURO: es el otro testigo de la
        # base — en base residual el primero vivo arranca cerca de 100.
        res1 = _f(fut_fl[0].get("residual_previo_pct")) if fut_fl else 0.0
        filas.append((d.get("ticker_corto") or "?", tot, fut, fut / tot * 100, res1,
                      len(flujos), len(fut_fl)))
    if not filas:
        print("  (no hay ningún CER con cronograma cargado)")
        return

    amortizados = [r for r in filas if r[3] < 99.5]   # ya pagó algo de capital
    intactos = [r for r in filas if r[3] >= 99.5]
    print(f"  {len(filas)} bonos CER con cronograma · {len(amortizados)} ya "
          f"amortizaron algo · {len(intactos)} intactos\n")
    print(f"  {'TICKER':<10}{'Σ TOTAL':>12}{'Σ FUTURA':>12}{'% VIVO':>10}"
          f"{'res_prev[0]':>13}{'cupones':>10}{'futuros':>9}")
    for tk, tot, fut, pct, res1, n, nf in sorted(amortizados, key=lambda r: r[3]):
        print(f"  {tk:<10}{tot:>12,.4f}{fut:>12,.4f}{pct:>9.2f}%{res1:>13,.4f}"
              f"{n:>10}{nf:>9}")
    if not amortizados:
        print("  ⚠ NINGUNO amortizó todavía: con estos datos las dos bases son")
        print("    indistinguibles y el master NO contesta la pregunta.")
        return

    # El veredicto: en base ORIGINAL la Σ TOTAL es ~100; en base RESIDUAL lo es
    # la Σ FUTURA. Se cuenta sobre los amortizados, que son los únicos que
    # distinguen las dos.
    orig = sum(1 for r in amortizados if 99.0 <= r[1] <= 101.0)
    resi = sum(1 for r in amortizados if 99.0 <= r[2] <= 101.0)
    print(f"\n  → Σ TOTAL ≈ 100 (base ORIGINAL):  {orig}/{len(amortizados)}")
    print(f"  → Σ FUTURA ≈ 100 (base RESIDUAL): {resi}/{len(amortizados)}")
    if orig and not resi:
        print("  VEREDICTO: el master está en BASE ORIGINAL → el bug es la "
              "PARIDAD del motor,\n             que divide por 100 en vez de "
              "por el residual vivo.")
    elif resi and not orig:
        print("  VEREDICTO: el master está en BASE RESIDUAL → el bug es el "
              "CONVERSOR,\n             que normaliza por la Σ del cuadro "
              "COMPLETO en vez de la futura.")
    else:
        print("  VEREDICTO: ⚠ CONVIVEN LAS DOS BASES en el master. Eso es peor "
              "que cualquiera\n             de las dos: la misma columna "
              "significa cosas distintas según el bono.")


# ── 2. EL BONO CONTRA 1816 (cuesta créditos) ──────────────────────────────
def contra_1816(ticker: str) -> None:
    from api.services.av_agent_alta import convertir_flujos
    from core import mercado_1816 as m1816

    tk = m1816.normalizar_ticker(ticker)
    print("\n" + "=" * 78)
    print(f"2. {tk} CONTRA 1816")
    print("=" * 78)

    try:
        data = m1816.cashflow(tk)
    except Exception as e:
        print(f"  ✘ cashflow: {e}")
        return
    cupones = data.get("cashflow") or []
    if not cupones:
        print("  ✘ el cuadro vino VACÍO")
        return
    print(f"  claves de una fila: {sorted(cupones[0])}")

    hoy = date.today().isoformat()
    conv = convertir_flujos(cupones, "cer")
    flujos = conv["flujos"]
    fut = [f for f in flujos if f["fecha"] > hoy]
    sum_tot = sum(_f(f.get("amortizacion_pct")) for f in flujos)
    sum_fut = sum(_f(f.get("amortizacion_pct")) for f in fut)
    residual_cuadro = sum_fut / sum_tot * 100 if sum_tot else 0.0
    print(f"\n  cupones {conv['n']} ({conv['n'] - len(fut)} pagados · "
          f"{len(fut)} futuros) · Σ amortizaciones 1816 {conv['suma_amort']:,.4f}")
    print(f"  Σ amortizacion_pct   TOTAL {sum_tot:>10,.4f}   "
          f"FUTURA {sum_fut:>10,.4f}")
    print(f"  → RESIDUAL según el CUADRO: {residual_cuadro:.4f}%")
    if fut:
        print(f"  residual_previo_pct del primer futuro: "
              f"{_f(fut[0].get('residual_previo_pct')):,.4f}  "
              f"(fecha {fut[0]['fecha']})")
        print("\n  primeros 3 flujos futuros (fecha · amort_pct · cupón/residual):")
        for f in fut[:3]:
            print(f"    {f['fecha']}  {_f(f.get('amortizacion_pct')):>10,.6f}"
                  f"  {_f(f.get('cupon_sobre_residual')):>12,.8f}")

    # Los indicadores de ELLOS. ⚠️ SOLO campos del enum del spec
    # (`CAMPOS_INDICADORES`): la API rechaza la llamada ENTERA si uno no existe,
    # y `valorTecnico`/`valorResidual`/`interesCorrido` NO están en el enum. El
    # interés corrido se DEDUCE: precioDirty − precioClean.
    # `indicadores_vigentes` retrocede al último hábil con datos — sin eso un
    # domingo devuelve todo null (trampa ya pagada dos veces).
    campos = ["paridad", "tea", "duration", "precioClean", "precioDirty"]
    ref: dict = {}
    try:
        r = m1816.indicadores_vigentes([tk], campos, moneda="ars")
        ref = ((r.get("instrumentos") or {}).get(tk)) or {}
        print(f"\n  indicadores 1816 (rueda {r.get('fechaOperacion')}):")
        for k, v in ref.items():
            print(f"    {k:<20} {v}")
    except Exception as e:
        print(f"  ✘ indicadores: {e}")

    par = _f(ref.get("paridad"))
    clean, dirty = _f(ref.get("precioClean")), _f(ref.get("precioDirty"))
    if par and clean:
        # El valor técnico IMPLÍCITO de ellos. El cociente contra 100 × ratio
        # (el denominador que usa el motor hoy) ES el residual que aplican.
        tecnico = clean / (par / 100)
        print(f"\n  precio clean {clean:,.4f} · paridad {par:,.4f}%"
              f" → valor técnico IMPLÍCITO {tecnico:,.4f}")
        if dirty:
            print(f"  precio dirty {dirty:,.4f} → interés corrido "
                  f"{dirty - clean:,.4f} ({(dirty / clean - 1) * 100:.4f}% del clean)")
        print("  Comparalo con 100 × CER_liq/cer_emision, que es el denominador "
              "que el motor\n  usa HOY: el cociente entre los dos ES el residual "
              "que aplican ellos, y tiene\n  que dar parecido al RESIDUAL SEGÚN "
              "EL CUADRO de arriba. Si dan distinto,\n  el cuadro que bajamos no "
              "es el mismo bono que ellos publican.")

    print(f"\n  RESUMEN {tk}: residual por el cuadro = {residual_cuadro:.4f}%")
    print("  Si ese número explica a la vez la diferencia de PARIDAD y la de "
          "TEA, el\n  problema es la BASE. Si explica solo una de las dos, "
          "son dos problemas.")


def main() -> None:
    convencion_del_master()
    for tk in sys.argv[1:]:
        contra_1816(tk)
    if len(sys.argv) == 1:
        print("\n(para el paso 2, que cuesta créditos: "
              "python -m scripts.diag_cer_amortizado DICP PARP)")


if __name__ == "__main__":
    main()
