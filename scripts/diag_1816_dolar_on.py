"""`scripts/diag_1816_dolar_on.py` — **¿CON QUÉ DÓLAR CALCULA 1816 EN CADA
MONEDA, Y ES EL MISMO DÓLAR QUE EL NUESTRO?**

Read-only. **No escribe una sola fila.** Doc: `docs/AGENT.md` §0.ee.

## Qué contesta

Le pedimos a la calculadora de 1816 (`/indicadores/{ticker}`, input MANUAL) los
indicadores de N ONs **al mismo precio** en tres monedas (`ars`, `mep`, `ccl`) y
comparamos contra NUESTRO motor (`engines.curvas.calcular_campos`) corrido con
el MEP y el CCL de la casa. Para cada TEA que publica 1816 despejamos, por
bisección, el **tipo de cambio implícito**: el FX que, metido en nuestro motor,
reproduce esa TEA. Así se ve con qué dólar calcula 1816 en cada moneda, y si
nuestra cuenta coincide con la de ellos cuando el dólar es el mismo.

Contexto medido (2026-09-07, 7 ONs: EAC3O YM43O LMS8O YFCPO AEC3O CP37O DNC3O):
la TEA de 1816 en `ars` se reproduce **EXACTA** con nuestro motor (mismo
cronograma, T+1, act/365) usando un FX de **1.588** en las 7, contra nuestro
MEP de **1.523** (+4,25%). El CCL de la casa ese día ~1.576. Este diag existe
para saber si el `ars` de 1816 es su CCL, cuánto vale su MEP, y si con el mismo
dólar las dos cuentas coinciden.

## Qué hace

Por cada ticker: trae el precio de referencia de 1816 (input manual sobre ese
mismo precio), el cuadro de cupones (`cashflow`, vía `agente.alta.convertir_flujos`
rama `on`), y corre el motor DOS veces (MEP y CCL de la casa). Para cada moneda
pedida, le pide a 1816 sus indicadores AL MISMO precio y despeja por bisección
[800, 4000] el FX que hace que nuestro motor reproduzca esa TEA.

## Costo

**Sale a la red y consume créditos de 1816.** Por ticker: 7 créditos de
referencia (`indicadores_vigentes`, 7 campos) + 4 × cantidad de monedas
(`indicadores_de`, 4 campos por moneda) + 1 crédito por cupón del cuadro
(`cashflow`) — este último no se sabe hasta pedirlo.

    python -m scripts.diag_1816_dolar_on
    python -m scripts.diag_1816_dolar_on --tickers EAC3O,YM43O
    python -m scripts.diag_1816_dolar_on --monedas ars,mep
"""
from __future__ import annotations

import argparse
import sys

DEFAULT_TICKERS = "EAC3O,YM43O,LMS8O,YFCPO,AEC3O,CP37O,DNC3O"
DEFAULT_MONEDAS = "ars,mep,ccl"

FILAS_1816 = ("ars", "mep", "ccl")

CAMPOS_REF = ["precioDirty", "precioClean", "tea", "paridad", "duration",
              "convencionTna", "fechaLiquidacion"]
CAMPOS_MANUAL = ["tea", "paridad", "duration", "precioClean"]

FX_LO, FX_HI = 800.0, 4000.0
FX_ITER = 60
# Rango máximo del FX implícito entre tickers para decir «un solo dólar».
RANGO_UN_DOLAR = 0.003


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


def _vs_mep(fx, mep) -> str:
    if not isinstance(fx, int | float) or not mep:
        return "—"
    return f"{fx / mep - 1:+.2%}"


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--tickers", default=DEFAULT_TICKERS,
                   help="tickers separados por coma")
    p.add_argument("--monedas", default=DEFAULT_MONEDAS,
                   help="monedas separadas por coma (ars,mep,ccl)")
    a = p.parse_args()

    from datetime import UTC, datetime

    from agente.alta import convertir_flujos
    from api.services.macro import get_ultimo_mep
    from core import mercado_1816
    from engines.curvas import calcular_campos, cargar_dias_habiles

    tickers_pedidos = [t.strip().upper() for t in a.tickers.split(",") if t.strip()]
    monedas_pedidas = [m.strip().lower() for m in a.monedas.split(",") if m.strip()]
    if not tickers_pedidos:
        print("no se pidió ningún ticker.")
        return 1
    for m in monedas_pedidas:
        if m not in mercado_1816.MONEDAS:
            print(f"moneda inválida: {m!r}. Válidas: {mercado_1816.MONEDAS}")
            return 1

    costo_fijo = len(tickers_pedidos) * (len(CAMPOS_REF) + len(CAMPOS_MANUAL) * len(monedas_pedidas))
    print(f"Costo estimado: {len(tickers_pedidos)} ticker(s) × "
          f"({len(CAMPOS_REF)} de referencia + {len(CAMPOS_MANUAL)}×{len(monedas_pedidas)} "
          f"monedas) = {costo_fijo} créditos + 1 por cupón del cuadro (recién se sabe al pedirlo).")

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
        print("\n  ⚠️ sin CCL: la columna «nuestro @CCL» va a faltar en todas las tablas.")

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

        def fx_implicito(tea_obj):
            if not isinstance(tea_obj, int | float):
                return None
            lo, hi = FX_LO, FX_HI
            t_lo, t_hi = nuestro(lo).get("TEA"), nuestro(hi).get("TEA")
            if t_lo is None or t_hi is None:
                return None
            for _ in range(FX_ITER):
                mid = (lo + hi) / 2
                t_mid = nuestro(mid).get("TEA")
                if t_mid is None:
                    return None
                if t_mid < tea_obj:
                    lo = mid
                else:
                    hi = mid
            return (lo + hi) / 2

        filas_1816: dict[str, dict] = {}
        for m in monedas_pedidas:
            try:
                r = mercado_1816.indicadores_de(tk, CAMPOS_MANUAL, moneda=m,
                                                precioDirty=float(px))
            except Exception:
                try:
                    r = mercado_1816.indicadores_de(tk, CAMPOS_MANUAL, moneda=m,
                                                    precioDirty=float(px),
                                                    fecha_operacion=fecha_op)
                except Exception as e2:
                    filas_1816[m] = {"error": str(e2)[:120]}
                    continue
            ind = (r or {}).get("indicadores") or {}
            filas_1816[m] = {"tea": _num(ind.get("tea")), "paridad": _num(ind.get("paridad")),
                             "duration": _num(ind.get("duration")), "error": None}

        for fila in filas_1816.values():
            fila["fx"] = fx_implicito(fila.get("tea")) if not fila.get("error") else None

        tea_ref = _num(v.get("tea"))
        fx_ref = fx_implicito(tea_ref)

        n_mep = nuestro(mep)
        n_ccl = nuestro(ccl) if ccl else {}

        resultados.append({
            "tk": tk, "px": px, "fecha_op": fecha_op,
            "convencion_tna": v.get("convencionTna"),
            "duration_1816_ars": _num(v.get("duration")),
            "filas_1816": filas_1816,
            "ref": {"tea": tea_ref, "paridad": _num(v.get("paridad")),
                    "duration": _num(v.get("duration")), "fx": fx_ref},
            "nuestro_mep": n_mep, "nuestro_ccl": n_ccl if ccl else None,
        })

    # ── 2 ──────────────────────────────────────────────────────────────────
    _titulo("2. UN BONO, UN PRECIO, CUATRO DÓLARES")
    for r in resultados:
        print(f"\n  {r['tk']}  ·  precio {r['px']:.4f}  ·  fecha op {r['fecha_op']}  ·  "
              f"convención TNA {r['convencion_tna']}  ·  duration 1816 (ars) "
              f"{_dur(r['duration_1816_ars'])}")
        print(f"  {'fila':<20} {'TEA':>10} {'paridad':>10} {'duration':>10} "
              f"{'FX implícito':>13} {'vs MEP casa':>12}")
        print("  " + "─" * 79)
        for m in FILAS_1816:
            if m not in monedas_pedidas:
                continue
            fila = r["filas_1816"].get(m) or {}
            if fila.get("error"):
                print(f"  {'1816 ' + m:<20} ⚠ {fila['error']}")
                continue
            print(f"  {'1816 ' + m:<20} {_pct_frac(fila.get('tea')):>10} "
                  f"{_pct_frac(fila.get('paridad')):>10} "
                  f"{_dur(fila.get('duration')):>10} {_fx_txt(fila.get('fx')):>13} "
                  f"{_vs_mep(fila.get('fx'), mep):>12}")
        ref = r["ref"]
        print(f"  {'1816 ref (su precio)':<20} {_pct_frac(ref.get('tea')):>10} "
              f"{_pct_frac(ref.get('paridad')):>10} "
              f"{_dur(ref.get('duration')):>10} {_fx_txt(ref.get('fx')):>13} "
              f"{_vs_mep(ref.get('fx'), mep):>12}")
        nm = r["nuestro_mep"]
        print(f"  {'nuestro @MEP':<20} {_pct_frac(nm.get('TEA')):>10} "
              f"{_pct_ya(nm.get('paridad')):>10} {_dur(nm.get('duration')):>10} "
              f"{_fx_txt(mep):>13} {_vs_mep(mep, mep):>12}")
        if r["nuestro_ccl"] is not None:
            nc = r["nuestro_ccl"]
            print(f"  {'nuestro @CCL':<20} {_pct_frac(nc.get('TEA')):>10} "
                  f"{_pct_ya(nc.get('paridad')):>10} {_dur(nc.get('duration')):>10} "
                  f"{_fx_txt(ccl):>13} {_vs_mep(ccl, mep):>12}")

    # ── 3 ──────────────────────────────────────────────────────────────────
    _titulo("3. LECTURA")
    if not resultados:
        print("  no hay ningún ticker con resultado: nada que leer.")
        return 0

    # LA PRUEBA DE LA FÓRMULA ES LA DISPERSIÓN, no el valor. Cualquier TEA sola
    # se reproduce con ALGÚN FX (un grado de libertad); lo que no se puede
    # fabricar es que el MISMO FX reproduzca las TEAs de bonos con durations
    # distintas. Si el rango entre tickers es angosto, 1816 hace nuestra misma
    # cuenta y lo único que difiere es el dólar — y ese FX ES su dólar, en número.
    for m in monedas_pedidas:
        fxs = [r["filas_1816"].get(m, {}).get("fx") for r in resultados]
        fxs = [f for f in fxs if isinstance(f, int | float)]
        if not fxs:
            print(f"  FX implícito de 1816 en {m}: sin datos.")
            continue
        rango = max(fxs) / min(fxs) - 1
        print(f"  FX implícito de 1816 en {m}: min {min(fxs):,.1f}  "
              f"max {max(fxs):,.1f}  prom {sum(fxs) / len(fxs):,.1f}  "
              f"rango {rango:.2%}"
              + ("" if len(fxs) < 2 else
                 "  → UN solo dólar: misma fórmula, difiere solo el FX"
                 if rango <= RANGO_UN_DOLAR else
                 "  → NO es un solo dólar: hay algo más que el FX (fechas o cuadro)"))

    if "ars" in monedas_pedidas and "ccl" in monedas_pedidas:
        deltas = []
        todos_cerca = True
        for r in resultados:
            fx_ars = r["filas_1816"].get("ars", {}).get("fx")
            fx_ccl = r["filas_1816"].get("ccl", {}).get("fx")
            if isinstance(fx_ars, int | float) and isinstance(fx_ccl, int | float) and fx_ccl:
                delta = abs(fx_ars / fx_ccl - 1)
                deltas.append((r["tk"], delta))
                if delta > 0.002:
                    todos_cerca = False
            else:
                todos_cerca = False
        if deltas and todos_cerca:
            print("\n  ✔ su «ars» ES su «ccl» (FX implícito a ≤0,2% en todos los tickers).")
        elif deltas:
            lejos = [tk for tk, d in deltas if d > 0.002]
            print(f"\n  ✖ su «ars» NO coincide con su «ccl» en: {', '.join(lejos) or '—'}")

    if "mep" in monedas_pedidas:
        fxs_mep = [r["filas_1816"].get("mep", {}).get("fx") for r in resultados]
        fxs_mep = [f for f in fxs_mep if isinstance(f, int | float)]
        if fxs_mep:
            prom = sum(f / mep - 1 for f in fxs_mep) / len(fxs_mep)
            print(f"\n  su MEP vs el nuestro: {prom:+.2%} en promedio.")

        for r in resultados:
            tea_1816_mep = (r["filas_1816"].get("mep") or {}).get("tea")
            tea_nuestro_mep = r["nuestro_mep"].get("TEA")
            if isinstance(tea_1816_mep, int | float) and isinstance(tea_nuestro_mep, int | float):
                bps = (tea_nuestro_mep - tea_1816_mep) * 10_000
                print(f"    {r['tk']}: {bps:+.1f} bps (nuestra TEA @MEP vs 1816 en mep)")
    else:
        print("\n  «mep» no se pidió: no se puede evaluar «misma cuenta».")

    if ccl and "ccl" in monedas_pedidas:
        fxs_ccl = [r["filas_1816"].get("ccl", {}).get("fx") for r in resultados]
        fxs_ccl = [f for f in fxs_ccl if isinstance(f, int | float)]
        if fxs_ccl:
            prom = sum(f / ccl - 1 for f in fxs_ccl) / len(fxs_ccl)
            print(f"\n  su CCL vs el nuestro: {prom:+.2%} en promedio.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
