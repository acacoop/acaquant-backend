"""Diag READ-ONLY: la posición de UNA cuenta, con lo LIQUIDADO y lo PENDIENTE al lado.

LA PREGUNTA QUE CONTESTA
========================
`jobs/control_saldos.py` persiste **solo `cantidadLiquidada`** y guarda el
pendiente al costado (`cantidad_pendiente`) sin mostrarlo. La sospecha a
verificar es si ese pendiente es plata que la pantalla se está comiendo: una
cuenta podría verse en cero (o a favor) mirando solo lo liquidado, cuando en
realidad tiene algo grande por liquidar en la otra columna.

Este script pone las dos columnas una al lado de la otra para la cuenta que le
pidas, y las contrasta con `posicionValuada` — que es la fuente VIEJA y
proyectada. Ese contraste es el que decide, porque hay una hipótesis concreta:

    liquidada           ≈ posicionValuada t0  (liquidada a HOY)
    liquidada+pendiente ≈ posicionValuada t1  (liquidada a MAÑANA)

Si eso se cumple, el pendiente NO es plata perdida: es exactamente el futuro que
`control_saldos` deja afuera A PROPÓSITO, y el tablero está bien como está.
Si NO se cumple, el pendiente es otra cosa y hay que entender qué antes de
seguir mostrando un saldo que lo ignora.

SEGURIDAD — NO ESCRIBE NADA
===========================
Cero INSERT/UPDATE/DELETE. Solo GETs de lectura a Aunesa (los MISMOS que ya hace
el daemon) y SELECTs. 3 llamadas por cuenta.

Uso:
    python -m scripts.diag_posicion_cuenta                    # cuenta 1243
    python -m scripts.diag_posicion_cuenta --cuentas 1243,805
    python -m scripts.diag_posicion_cuenta --moneda ARS       # solo esa
    python -m scripts.diag_posicion_cuenta --con-titulos      # también los títulos
"""
from __future__ import annotations

import json
import sys
from datetime import UTC, date, datetime, timedelta

from api.services._sql import _q
from core.calendario import es_habil, proximo_habil
from jobs.aum import _SESSION, POSICION_URL, autenticar, obtener_cuentas
from jobs.control_saldos import MONEDAS, SIGNO, UMBRALES, _traer, parsear
from jobs.portafolio_backfill import (
    _PARAMS_BASE,
    _load_assets_map,
    _parse,
    _timeout_for,
    cargar_contrapartes,
)

CUENTAS_DEFAULT = ("1243",)


def _arg(flag: str, default=None):
    if flag in sys.argv:
        i = sys.argv.index(flag)
        if i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return default


def _num(v) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _hoy_art() -> date:
    return (datetime.now(UTC) - timedelta(hours=3)).date()


def _ultimo_habil(d: date) -> date:
    while not es_habil(d):
        d -= timedelta(days=1)
    return d


# ── 1) las filas crudas ───────────────────────────────────────────────────────
def crudas(raw: list, moneda: str | None, con_titulos: bool) -> list[dict]:
    out = []
    for r in raw:
        if not isinstance(r, dict):
            continue
        es_moneda = str(r.get("tipoTitulo") or "").strip().lower() == "moneda"
        if not es_moneda and not con_titulos:
            continue
        if moneda and str(r.get("especie") or "").strip().upper() != moneda:
            continue
        out.append(r)
    return out


def mostrar_crudas(filas: list[dict]) -> None:
    print("\n── 1) FILAS CRUDAS (tal cual las manda Aunesa, SIN corregir el signo) ─────")
    if not filas:
        print("   (ninguna)")
        return
    print(f"   {'especie':<14} {'estado':<6} {'subCta':<8} {'lugar':<7} "
          f"{'LIQUIDADA':>18} {'PENDIENTE':>18}")
    for r in sorted(filas, key=lambda x: str(x.get("especie") or "")):
        print(f"   {str(r.get('especie') or '')[:14]:<14} "
              f"{str(r.get('estado') or '')[:6]:<6} "
              f"{str(r.get('subCuenta') or '')[:8]:<8} "
              f"{str(r.get('lugar') or '')[:7]:<7} "
              f"{_num(r.get('cantidadLiquidada')):>18,.2f} "
              f"{_num(r.get('cantidadPendienteLiquidar')):>18,.2f}")
    print("\n   Recordatorio: Aunesa manda el signo AL REVÉS. Un -56.095,90 acá es una")
    print(f"   cuenta A FAVOR. El job lo corrige con SIGNO = {SIGNO}.")


# ── 2) por moneda, ya con el signo derecho ────────────────────────────────────
def por_moneda(filas: list[dict]) -> dict[str, dict]:
    g: dict[str, dict] = {}
    for r in filas:
        esp = str(r.get("especie") or "").strip().upper()
        d = g.setdefault(esp, {"n": 0, "liq": 0.0, "pen": 0.0})
        d["n"] += 1
        d["liq"] += _num(r.get("cantidadLiquidada"))
        d["pen"] += _num(r.get("cantidadPendienteLiquidar"))
    return g


def mostrar_por_moneda(g: dict[str, dict]) -> None:
    print("\n── 2) POR ESPECIE, con el signo YA CORREGIDO (lo que vale de verdad) ──────")
    print(f"   {'especie':<14} {'filas':>5} {'LIQUIDADO':>20} {'PENDIENTE':>20} "
          f"{'LIQ + PEND':>20}")
    for esp, d in sorted(g.items()):
        liq, pen = SIGNO * d["liq"], SIGNO * d["pen"]
        print(f"   {esp[:14]:<14} {d['n']:>5} {liq:>20,.2f} {pen:>20,.2f} "
              f"{liq + pen:>20,.2f}")
    print("\n   LIQUIDADO  = lo que ESTÁ en la cuenta hoy   → es lo que persiste el job")
    print("   PENDIENTE  = lo que todavía no liquidó      → hoy NO se muestra")
    print("   LIQ + PEND = cómo quedaría cuando liquide todo")


# ── 3) qué persiste el job (el MISMO código del daemon) ───────────────────────
def mostrar_persistido(raw: list, idc: str, denom: str, g: dict[str, dict]) -> None:
    print("\n── 3) QUÉ GUARDA EL JOB HOY (`parsear`, el mismo código del daemon) ───────")
    registros, descartadas = parsear(raw, idc, denom)
    if registros:
        for r in registros:
            print(f"   {r['ticker']:<8} cantidad={r['cantidad']:>20,.2f}   "
                  f"(cantidad_pendiente={r['cantidad_pendiente']:>18,.2f}, "
                  f"{r['filas_origen']} fila/s)")
    else:
        print("   (no guardaría NADA para esta cuenta)")

    # Lo que el UMBRAL dejó afuera se dice explícito: es la explicación más
    # probable de "la cuenta no aparece en la pantalla" y no tiene nada que ver
    # con el pendiente.
    guardadas = {r["ticker"] for r in registros}
    for esp, d in sorted(g.items()):
        if esp in guardadas or esp not in MONEDAS:
            continue
        liq = SIGNO * d["liq"]
        umbral = UMBRALES.get(esp, 0.0)
        motivo = ("es exactamente 0" if liq == 0 else
                  f"|{liq:,.2f}| < umbral {umbral:,.2f}" if abs(liq) < umbral else "?")
        print(f"   ⚠ {esp}: NO se guarda — {motivo}")
    if descartadas:
        print(f"   monedas fuera de la whitelist {MONEDAS}: {descartadas}")


# ── 4) qué hay hoy en la tabla ────────────────────────────────────────────────
def mostrar_tabla(idc: str) -> None:
    print("\n── 4) QUÉ HAY AHORA EN portafolio.control_saldos ──────────────────────────")
    try:
        filas = _q("SELECT fecha, ticker, cantidad, cantidad_pendiente, filas_origen, "
                   "       origen, actualizado_at "
                   "FROM portafolio.control_saldos WHERE id_cuenta = %(c)s "
                   "ORDER BY fecha DESC, ticker", {"c": idc})
    except Exception as e:
        print(f"   no pude leer ({type(e).__name__}: {e})")
        return
    if not filas:
        print("   (sin filas — o el barrido todavía no corrió, o el umbral las dejó")
        print("    afuera, o la cuenta está en la lista de OCULTAS)")
        return
    for f in filas:
        print(f"   {f['fecha']}  {f['ticker']:<8} cantidad={float(f['cantidad'] or 0):>20,.2f} "
              f"pend={float(f['cantidad_pendiente'] or 0):>18,.2f}  "
              f"{f['origen'] or ''} · {f['actualizado_at']}")


# ── 5) el contraste que decide: contra posicionValuada ────────────────────────
def _valuada(idc: str, denom: str, desde: date, headers: dict, amap: dict) -> dict:
    params = {**_PARAMS_BASE, "desde": desde.strftime("%d/%m/%Y")}
    try:
        resp = _SESSION.get(POSICION_URL.format(idc), params=params, headers=headers,
                            timeout=_timeout_for(idc, denom))
    except Exception as e:
        print(f"   posicionValuada desde={desde}: {type(e).__name__}: {e}")
        return {}
    if resp.status_code != 200:
        print(f"   posicionValuada desde={desde}: HTTP {resp.status_code}")
        return {}
    try:
        data = resp.json()
    except ValueError:
        return {}
    regs = _parse(data if isinstance(data, list) else [], idc, denom, "1970-01-01", amap)
    return {r["unidad"]: r["cantidad"] for r in regs}


def contraste(idc: str, denom: str, headers: dict, amap: dict, hoy: date,
              g: dict[str, dict]) -> None:
    print("\n── 5) CONTRA `posicionValuada` (la fuente VIEJA, proyectada) ──────────────")
    h0 = _ultimo_habil(hoy)
    d_t0, d_t1 = proximo_habil(h0), proximo_habil(proximo_habil(h0))
    t0 = _valuada(idc, denom, d_t0, headers, amap)
    t1 = _valuada(idc, denom, d_t1, headers, amap)
    print(f"   t0 (desde={d_t0}) = posición liquidada a HOY    · {len(t0)} unidades")
    print(f"   t1 (desde={d_t1}) = liquidada a MAÑANA          · {len(t1)} unidades")

    print(f"\n   {'especie':<14} {'LIQUIDADO':>18} {'LIQ+PEND':>18} "
          f"{'valuada t0':>18} {'valuada t1':>18}")
    for esp, d in sorted(g.items()):
        liq, pen = SIGNO * d["liq"], SIGNO * d["pen"]
        v0, v1 = t0.get(esp), t1.get(esp)
        print(f"   {esp[:14]:<14} {liq:>18,.2f} {liq + pen:>18,.2f} "
              f"{(f'{v0:,.2f}' if v0 is not None else '—'):>18} "
              f"{(f'{v1:,.2f}' if v1 is not None else '—'):>18}")

    # El veredicto se escribe solo: qué columna coincide con qué.
    print("\n   ▸ VEREDICTO")
    tol = 0.01
    algo = False
    for esp, d in sorted(g.items()):
        liq, pen = SIGNO * d["liq"], SIGNO * d["pen"]
        v0, v1 = t0.get(esp), t1.get(esp)
        if v0 is None and v1 is None:
            continue
        algo = True
        casa_t0 = v0 is not None and abs(liq - v0) <= max(tol, abs(v0) * 1e-6)
        casa_t1 = v1 is not None and abs((liq + pen) - v1) <= max(tol, abs(v1) * 1e-6)
        if casa_t0 and casa_t1:
            print(f"     {esp}: ✓ liquidado = t0 y liquidado+pendiente = t1 → el pendiente")
            print("        es EXACTAMENTE el futuro que el tablero deja afuera a propósito.")
        elif casa_t0:
            print(f"     {esp}: ~ liquidado = t0, pero liq+pend NO da t1 "
                  f"(dif {abs((liq + pen) - (v1 or 0)):,.2f}) → el pendiente es otra cosa.")
        elif casa_t1:
            print(f"     {esp}: ~ liq+pend = t1, pero el liquidado NO da t0 "
                  f"(dif {abs(liq - (v0 or 0)):,.2f}).")
        else:
            print(f"     {esp}: ✗ no coincide con ninguna — mirar los números de arriba.")
    if not algo:
        print("     (posicionValuada no devolvió ninguna de estas especies)")


def main() -> int:
    cuentas = tuple(c.strip() for c in
                    (_arg("--cuentas") or ",".join(CUENTAS_DEFAULT)).split(",") if c.strip())
    moneda = (_arg("--moneda") or "").strip().upper() or None
    con_titulos = "--con-titulos" in sys.argv
    dump = _arg("--dump")
    hoy = _hoy_art()

    print(f"\n{'=' * 78}")
    print(f"DIAG — posición por cuenta: LIQUIDADO vs PENDIENTE   ·   {hoy} (ART)")
    print(f"{'=' * 78}")
    print("READ-ONLY: no escribe una sola fila.")
    print(f"   cuentas: {', '.join(cuentas)}"
          + (f"   ·   moneda: {moneda}" if moneda else "   ·   todas las monedas")
          + ("   ·   + títulos" if con_titulos else ""))

    cargar_contrapartes()
    amap = _load_assets_map()
    headers = autenticar()
    df = obtener_cuentas(headers)
    denoms = {str(r["id"]): str(r["denominacion"]) for _, r in df.iterrows()}

    payload: dict = {}
    for idc in cuentas:
        denom = denoms.get(idc, "")
        print(f"\n\n{'█' * 78}\n█  CUENTA {idc}  {denom[:55]}\n{'█' * 78}")
        if not denom:
            print("   ⚠ no figura como activa en el listado de Aunesa — la sondeo igual")

        ok, raw, why = _traer(idc, hoy)
        if not ok:
            print(f"\n   ✗ no se pudo consultar: {why}")
            continue
        if not raw:
            print("\n   Aunesa respondió SIN posiciones (204) para esta cuenta.")
            continue

        filas = crudas(raw, moneda, con_titulos)
        g = por_moneda(filas)
        mostrar_crudas(filas)
        mostrar_por_moneda(g)
        mostrar_persistido(raw, idc, denom, g)
        mostrar_tabla(idc)
        contraste(idc, denom, headers, amap, hoy, g)
        payload[idc] = raw

    if dump:
        with open(dump, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=1, default=str)
        print(f"\n   crudo completo guardado en {dump}")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
