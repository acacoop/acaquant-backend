"""`scripts/diag_1816_dolar_on.py` — **¿CON QUÉ DÓLAR CALCULA 1816, Y ES LA MISMA
CUENTA QUE LA NUESTRA?**

Read-only. **No escribe una sola fila.** Doc: `docs/AGENT.md` §0.ee.

## Qué contesta

Tres preguntas, cada una con su prueba:

1. **¿La fórmula es la misma?** Se le pasa a la calculadora de 1816
   (`/indicadores/{ticker}`, input MANUAL) el precio en pesos con `moneda=ars`
   y, por bisección, se despeja el FX que hace que NUESTRO motor reproduzca su
   TEA. Cualquier TEA suelta se reproduce con algún dólar; lo que no se puede
   fabricar es que el MISMO dólar reproduzca bonos con durations distintas. Si
   el rango entre tickers es angosto, la fórmula es la misma y ese FX ES su
   dólar, en número. Y la prueba directa: le mandamos el precio dividido por
   NUESTRO MEP con `moneda=mep` — su TEA tiene que dar igual a la nuestra.

2. **¿Qué dólar es ese?** La calculadora no lo dice. Lo dice el endpoint de
   PRECIOS pedido en `mep` y en `ccl`: el precio en pesos dividido por el precio
   en cada moneda es SU MEP y SU CCL en número. Se compara con el FX de la
   pregunta 1 y con los dólares de la casa.

3. **¿Qué valor técnico usa?** `precioDirty / paridad`, en dólares. Contra el
   residual del cuadro, la diferencia es el interés corrido que ellos devengan
   y nosotros no (`engines/curvas.py` divide por el residual pelado).

## Lo que ya se midió (2026-09-07, primera corrida)

La fila `ars` reprodujo 5 bonos (duration 1,8 a 3,2) con UN dólar de **1.590**
y 0,04% de rango, contra nuestro MEP de 1.528 (+4,0%). Fórmula, cronograma y
liquidación son los mismos. Su valor técnico de EAC3O dio 101,86 = residual 100
+ 1,86 de devengado por días reales.

⚠️ **La calculadora espera el precio YA en la moneda pedida.** La primera
versión mandaba pesos con `moneda=mep` y 1816 los tomó como dólares (TEA −95%,
paridad 152.000%). Por eso ahora `mep` recibe `precio / MEP casa`.

## Costo

**Sale a la red y consume créditos de 1816.** Por ticker: 7 (referencia) + 4
(calculadora en ars) + 4 (calculadora en mep) + 2 (precio en mep y ccl) + 1 por
cupón del cuadro.

    python -m scripts.diag_1816_dolar_on
    python -m scripts.diag_1816_dolar_on --tickers EAC3O,YM43O
"""
from __future__ import annotations

import argparse
import sys

DEFAULT_TICKERS = "EAC3O,YM43O,LMS8O,YFCPO,AEC3O,CP37O,DNC3O"

CAMPOS_REF = ["precioDirty", "precioClean", "tea", "paridad", "duration",
              "convencionTna", "fechaLiquidacion"]
CAMPOS_MANUAL = ["tea", "paridad", "duration", "precioClean"]

FX_LO, FX_HI = 800.0, 4000.0
FX_ITER = 60
# Rango máximo del FX implícito entre tickers para decir «un solo dólar».
RANGO_UN_DOLAR = 0.003
# Con el MISMO dólar, hasta cuántos bps es «la misma cuenta».
BPS_MISMA_CUENTA = 5.0


def _titulo(s: str) -> None:
    print(f"\n{'═' * 78}\n {s}\n{'═' * 78}")


def _num(v) -> float | None:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _pct_frac(v, d: int = 2) -> str:
    """Fracción (0.0925) → «9.25%»."""
    return f"{v:.{d}%}" if isinstance(v, int | float) else "—"


def _pct_ya(v, d: int = 2) -> str:
    """Ya viene en % (102.16) → «102.16%»."""
    return f"{v:.{d}f}%" if isinstance(v, int | float) else "—"


def _dur(v) -> str:
    return f"{v:.4f}" if isinstance(v, int | float) else "—"


def _fx_txt(v) -> str:
    return f"{v:,.1f}" if isinstance(v, int | float) else "—"


def _vs(fx, base) -> str:
    if not isinstance(fx, int | float) or not base:
        return "—"
    return f"{fx / base - 1:+.2%}"


def _manual(mercado_1816, tk: str, moneda: str, precio: float, fecha_op) -> dict:
    """Calculadora de 1816 a UN precio, en la moneda en que está ese precio."""
    try:
        r = mercado_1816.indicadores_de(tk, CAMPOS_MANUAL, moneda=moneda,
                                        precioDirty=float(precio))
    except Exception:
        try:
            r = mercado_1816.indicadores_de(tk, CAMPOS_MANUAL, moneda=moneda,
                                            precioDirty=float(precio),
                                            fecha_operacion=fecha_op)
        except Exception as e2:
            return {"error": str(e2)[:120]}
    ind = (r or {}).get("indicadores") or {}
    return {"tea": _num(ind.get("tea")), "paridad": _num(ind.get("paridad")),
            "duration": _num(ind.get("duration")), "precio": float(precio),
            "error": None}


def _precio_en(mercado_1816, tk: str, moneda: str, fecha_op) -> float | None:
    """`precioDirty` de la rueda `fecha_op` expresado en `moneda`. 1 crédito."""
    try:
        r = mercado_1816.indicadores([tk], ["precioDirty"], moneda=moneda,
                                     fecha_operacion=fecha_op)
    except Exception:
        return None
    v = (r.get("instrumentos") or {}).get(tk) or {}
    return _num(v.get("precioDirty"))


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--tickers", default=DEFAULT_TICKERS,
                   help="tickers separados por coma")
    a = p.parse_args()

    from datetime import UTC, datetime

    from agente.alta import convertir_flujos
    from api.services.macro import get_ultimo_mep
    from core import mercado_1816
    from engines.curvas import calcular_campos, cargar_dias_habiles

    tickers_pedidos = [t.strip().upper() for t in a.tickers.split(",") if t.strip()]
    if not tickers_pedidos:
        print("no se pidió ningún ticker.")
        return 1

    costo_fijo = len(tickers_pedidos) * (len(CAMPOS_REF) + 2 * len(CAMPOS_MANUAL) + 2)
    print(f"Costo estimado: {len(tickers_pedidos)} ticker(s) × ({len(CAMPOS_REF)} de "
          f"referencia + {len(CAMPOS_MANUAL)}×2 calculadora + 2 precios) = {costo_fijo} "
          "créditos + 1 por cupón del cuadro (recién se sabe al pedirlo).")

    if not mercado_1816.disponible():
        print("sin MERCADO_1816_API_KEY")
        return 1

    # ── 1 ──────────────────────────────────────────────────────────────────
    _titulo("1. LOS DÓLARES DE LA CASA")
    d = get_ultimo_mep() or {}
    mep, ccl = d.get("mep"), d.get("ccl")
    print(f"  MEP     : {mep}")
    print(f"  CCL     : {ccl}")
    print(f"  canje   : {d.get('canje')}")
    print(f"  fuente  : {d.get('source')} · {d.get('timestamp')}")
    if not mep:
        print("\n  sin MEP no se puede correr el motor. Se aborta.")
        return 1
    if not ccl:
        print("\n  ⚠️ sin CCL de la casa (AL30C sin punta): se compara su CCL solo contra "
              "el MEP.")

    habiles = cargar_dias_habiles()
    ahora = datetime.now(UTC)

    resultados: list[dict] = []

    for t in tickers_pedidos:
        tk = mercado_1816.normalizar_ticker(t)
        try:
            resp = mercado_1816.indicadores_vigentes(
                [tk], CAMPOS_REF, moneda="ars",
                campos_dato=["precioDirty", "precioClean"]) or {}
        except Exception as e:
            print(f"\n  {tk}: no se pudo pedir la referencia ({str(e)[:120]})")
            continue
        v = (resp.get("instrumentos") or {}).get(tk) or {}
        px = _num(v.get("precioDirty")) or _num(v.get("precioClean"))
        if not px:
            print(f"\n  {tk}: sin precio en 1816.")
            continue
        fecha_op = resp.get("fechaOperacion")

        try:
            data = mercado_1816.cashflow(tk)
        except Exception as e:
            print(f"\n  {tk}: no se pudo pedir el cashflow ({str(e)[:120]})")
            continue
        cupones = data.get("cashflow") or []
        conv = convertir_flujos(cupones, "on")
        if not conv["flujos"]:
            print(f"\n  {tk}: el cuadro no tiene flujos convertibles.")
            continue

        doc = {"ticker_corto": tk, "ticker": f"MERV - XMEV - {tk} - 24hs",
               "emisor_tipo": "corporativo", "moneda_eje": "USD", "ajuste": "fija",
               "ley": "", "fecha_vencimiento": conv["flujos"][-1]["fecha"],
               "valor_nominal": 100.0, "moneda_flujo": "USD",
               "flujos": conv["flujos"]}

        def nuestro(fx, _px=px, _doc=doc):
            return calcular_campos({"price": float(_px), "timestamp": ahora},
                                   _doc, {}, habiles, mep=fx) or {}

        def fx_implicito(tea_obj, _nuestro=nuestro):
            """FX que mete en NUESTRO motor y devuelve `tea_obj`. La TEA crece con
            el FX. El motor devuelve None fuera de (−50%, 5000%): a FX bajo la
            TEA cae debajo de −50% (bonos cortos), a FX alto se dispara — los
            dos extremos se corren hasta entrar en la zona válida."""
            if not isinstance(tea_obj, int | float):
                return None
            lo, hi = FX_LO, FX_HI
            while lo < hi and _nuestro(lo).get("TEA") is None:
                lo *= 1.05
            while hi > lo and _nuestro(hi).get("TEA") is None:
                hi /= 1.05
            if lo >= hi:
                return None
            for _ in range(FX_ITER):
                mid = (lo + hi) / 2
                t_mid = _nuestro(mid).get("TEA")
                if t_mid is None:
                    return None
                if t_mid < tea_obj:
                    lo = mid
                else:
                    hi = mid
            return (lo + hi) / 2

        # Pregunta 1: la calculadora en ars (precio en pesos) y en mep (precio
        # dividido por NUESTRO MEP — el mismo número que consume el motor).
        f_ars = _manual(mercado_1816, tk, "ars", px, fecha_op)
        f_mep = _manual(mercado_1816, tk, "mep", px / mep, fecha_op)
        f_ars["fx"] = fx_implicito(f_ars.get("tea")) if not f_ars.get("error") else None

        # Pregunta 2: sus dólares, en número.
        px_mep = _precio_en(mercado_1816, tk, "mep", fecha_op)
        px_ccl = _precio_en(mercado_1816, tk, "ccl", fecha_op)
        su_mep = px / px_mep if px_mep else None
        su_ccl = px / px_ccl if px_ccl else None

        # Pregunta 3: su valor técnico (en dólares, con SU dólar de `ars`).
        vt = None
        if f_ars.get("paridad") and f_ars.get("fx"):
            vt = px / f_ars["fx"] / f_ars["paridad"]
        residual = sum(float(f.get("amortizacion") or 0) for f in conv["flujos"]
                       if f["fecha"] > ahora.date().isoformat())

        resultados.append({
            "tk": tk, "px": px, "fecha_op": fecha_op,
            "convencion_tna": v.get("convencionTna"),
            "ref": {"tea": _num(v.get("tea")), "paridad": _num(v.get("paridad")),
                    "duration": _num(v.get("duration"))},
            "ars": f_ars, "mep": f_mep,
            "su_mep": su_mep, "su_ccl": su_ccl,
            "vt": vt, "residual": residual,
            "nuestro": nuestro(mep),
        })

    # ── 2 ──────────────────────────────────────────────────────────────────
    _titulo("2. UN BONO, UN PRECIO — la cuenta de ellos y la nuestra")
    for r in resultados:
        print(f"\n  {r['tk']}  ·  precio {r['px']:,.2f} ARS  ·  rueda {r['fecha_op']}  ·  "
              f"convención TNA {r['convencion_tna']}")
        print(f"  {'fila':<26} {'precio dado':>12} {'TEA':>9} {'paridad':>9} "
              f"{'duration':>9} {'FX implícito':>13} {'vs MEP casa':>12}")
        print("  " + "─" * 95)
        fa, fm, n = r["ars"], r["mep"], r["nuestro"]
        if fa.get("error"):
            print(f"  {'1816 ars (pesos)':<26} ⚠ {fa['error']}")
        else:
            print(f"  {'1816 ars (pesos)':<26} {fa['precio']:>12,.2f} {_pct_frac(fa['tea']):>9} "
                  f"{_pct_frac(fa['paridad']):>9} {_dur(fa['duration']):>9} "
                  f"{_fx_txt(fa['fx']):>13} {_vs(fa['fx'], mep):>12}")
        if fm.get("error"):
            print(f"  {'1816 mep (px / MEP casa)':<26} ⚠ {fm['error']}")
        else:
            print(f"  {'1816 mep (px / MEP casa)':<26} {fm['precio']:>12,.4f} "
                  f"{_pct_frac(fm['tea']):>9} {_pct_frac(fm['paridad']):>9} "
                  f"{_dur(fm['duration']):>9} {_fx_txt(mep):>13} {'+0.00%':>12}")
        print(f"  {'nuestro @MEP casa':<26} {r['px'] / mep:>12,.4f} {_pct_frac(n.get('TEA')):>9} "
              f"{_pct_ya(n.get('paridad')):>9} {_dur(n.get('duration')):>9} "
              f"{_fx_txt(mep):>13} {'+0.00%':>12}")
        ref = r["ref"]
        print(f"  {'1816 publica (su precio)':<26} {'':>12} {_pct_frac(ref['tea']):>9} "
              f"{_pct_frac(ref['paridad']):>9} {_dur(ref['duration']):>9}")
        if isinstance(fm.get("tea"), int | float) and isinstance(n.get("TEA"), int | float):
            bps = (n["TEA"] - fm["tea"]) * 10_000
            print(f"  → con el MISMO dólar: {bps:+.1f} bps entre su TEA y la nuestra")
        print(f"  → sus dólares: MEP {_fx_txt(r['su_mep'])} ({_vs(r['su_mep'], mep)} vs "
              f"casa) · CCL {_fx_txt(r['su_ccl'])} "
              f"({_vs(r['su_ccl'], ccl) + ' vs casa' if ccl else _vs(r['su_ccl'], mep) + ' vs MEP casa'})")
        if r["vt"]:
            print(f"  → su valor técnico: {r['vt']:.4f} USD = residual {r['residual']:.2f} + "
                  f"devengado {r['vt'] - r['residual']:+.4f}")

    # ── 3 ──────────────────────────────────────────────────────────────────
    _titulo("3. LECTURA")
    if not resultados:
        print("  no hay ningún ticker con resultado: nada que leer.")
        return 0

    # (1) La fórmula. Dispersión del FX implícito + prueba directa al mismo dólar.
    fxs = [r["ars"].get("fx") for r in resultados if isinstance(r["ars"].get("fx"), int | float)]
    if len(fxs) >= 2:
        rango = max(fxs) / min(fxs) - 1
        print(f"  FX implícito de su `ars`: min {min(fxs):,.1f}  max {max(fxs):,.1f}  "
              f"prom {sum(fxs) / len(fxs):,.1f}  rango {rango:.2%}"
              + ("  → UN solo dólar: misma fórmula" if rango <= RANGO_UN_DOLAR
                 else "  → NO es un solo dólar: hay algo más que el FX"))
    bps_all = [(r["tk"], (r["nuestro"]["TEA"] - r["mep"]["tea"]) * 10_000) for r in resultados
               if isinstance(r["mep"].get("tea"), int | float)
               and isinstance(r["nuestro"].get("TEA"), int | float)]
    if bps_all:
        peor = max(bps_all, key=lambda x: abs(x[1]))
        print(f"  Al MISMO dólar (px / MEP casa en `mep`): peor diferencia {peor[1]:+.1f} bps "
              f"({peor[0]})"
              + ("  → LA MISMA CUENTA: lo único que difiere es el dólar"
                 if abs(peor[1]) <= BPS_MISMA_CUENTA else
                 "  → no es solo el dólar: mirar fechas de liquidación o cupón en curso"))

    # (2) Sus dólares.
    su_meps = [r["su_mep"] for r in resultados if r["su_mep"]]
    su_ccls = [r["su_ccl"] for r in resultados if r["su_ccl"]]
    fx_ars = sum(fxs) / len(fxs) if fxs else None
    if su_meps:
        m_ = sum(su_meps) / len(su_meps)
        print(f"\n  su MEP : {m_:,.1f}  ({_vs(m_, mep)} vs MEP casa {mep:,.1f})")
    if su_ccls:
        c_ = sum(su_ccls) / len(su_ccls)
        print(f"  su CCL : {c_:,.1f}  ({_vs(c_, mep)} vs MEP casa"
              + (f", {_vs(c_, ccl)} vs CCL casa {ccl:,.1f})" if ccl else ")"))
    if fx_ars and (su_meps or su_ccls):
        cerca = []
        if su_ccls and abs(fx_ars / (sum(su_ccls) / len(su_ccls)) - 1) <= RANGO_UN_DOLAR:
            cerca.append("CCL")
        if su_meps and abs(fx_ars / (sum(su_meps) / len(su_meps)) - 1) <= RANGO_UN_DOLAR:
            cerca.append("MEP")
        print(f"  el dólar de su `ars` ({fx_ars:,.1f}) "
              + (f"ES su {' y su '.join(cerca)}" if cerca else
                 "NO es ni su MEP ni su CCL: es otro dólar (¿de la curva?)"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
