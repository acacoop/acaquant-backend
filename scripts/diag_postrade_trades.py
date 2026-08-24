"""scripts/diag_postrade_trades.py — probar TradeCaptureReport contra PRODUCCIÓN.

READ-ONLY. Solo hace GET a `PosTrade/TradeCaptureReport`. No escribe en la base
ni llama a ningún otro método.

Responde tres preguntas, en este orden:

  1. ¿El método está habilitado para nuestro usuario? (`CurrencyList` y
     `ClosingProcesses` dieron 401 — puede pasar lo mismo acá).
  2. ¿Cómo se llaman los parámetros de fecha? El manual **se contradice**: la
     tabla dice `DateFrom`/`DateTo` y el ejemplo de abajo usa
     `dateFrom`/`dateTo`. Con `PartyDetails` ya vimos que esta API contesta
     `404 NotFound` cuando le falta un parámetro, así que la capitalización
     equivocada puede devolver vacío o 404 y **parecer "no hay operaciones"**
     cuando en realidad es el nombre del campo. Se prueban las dos.
  3. ¿Qué trae? Cuántas operaciones, de qué mercados, y una fila completa para
     ver los campos reales (que es lo que se necesita para decidir qué hacer
     con esto).

Uso (desde la raíz del repo, en el Droplet):

    python -m scripts.diag_postrade_trades                  # último día hábil
    python -m scripts.diag_postrade_trades --fecha 20260821
    python -m scripts.diag_postrade_trades --desde 20260818 --hasta 20260822
    python -m scripts.diag_postrade_trades --fecha 20260821 --crudo

`--crudo` imprime el JSON completo de la respuesta (puede ser largo).
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import date, timedelta

import requests

import config
from core import postrade

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SEP = "=" * 78
PATH = "PosTrade/TradeCaptureReport"


def _ultimo_habil() -> str:
    """AAAAMMDD del último día hábil. Sin feriados: solo evita sábado y domingo.

    Si cae feriado la API contesta con la lista vacía, y el script lo dice — no
    se hace pasar un feriado por "no está habilitado".
    """
    d = date.today() - timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d.strftime("%Y%m%d")


def _llamar(tok: str, params: dict) -> tuple[int, dict | str]:
    """GET crudo. Devuelve (status_http, json_o_texto).

    A propósito NO usa `postrade.leer()`: acá se está midiendo justamente qué
    forma de los parámetros funciona, así que hace falta ver la respuesta tal
    cual viene, incluido el sobre de error.
    """
    r = requests.get(
        f"{config.POSTRADE_BASE_URL.rstrip('/')}/{PATH}",
        params=params,
        headers={
            "Authorization": f"{config.POSTRADE_TOKEN_PREFIJO or ''}{tok}",
            "Accept": "application/json",
        },
        timeout=120,
    )
    try:
        return r.status_code, r.json()
    except ValueError:
        return r.status_code, (r.text or "")[:400]


def _describir(j: dict | str) -> str:
    """Qué dice el sobre, en una línea."""
    if not isinstance(j, dict):
        return f"respuesta no-JSON: {str(j)[:200]}"
    status = str(j.get("Status", "")).strip()
    code = str(j.get("Code", "")).strip()
    if status.upper() == "OK":
        v = j.get("Value")
        if v is None:
            return "OK pero Value=null"
        if isinstance(v, list):
            return f"OK — {len(v)} operaciones"
        return f"OK — Value es {type(v).__name__}"
    detalle = " ".join(
        str(j[k]) for k in ("ErrorMessage", "ErrorDescription") if j.get(k)
    )
    return f"Status={status!r} Code={code!r} {detalle[:160]}"


def _resumen(ops: list) -> None:
    """Qué hay adentro: mercados, segmentos, monedas, estados."""
    if not ops:
        return
    print("\n  ── QUÉ TRAJO ──")
    for campo in ("MarketID", "MarketSegmentID", "Currency", "TrdRptStatus", "TradeDate"):
        c = Counter(str(o.get(campo)) for o in ops if isinstance(o, dict))
        if c:
            top = ", ".join(f"{k}={v}" for k, v in c.most_common(8))
            print(f"  {campo:<18} {top}")

    # Los campos que trae una fila: es lo que hace falta para decidir qué
    # construir arriba. Del manual no se puede confiar (documenta el máximo,
    # no lo que llega con NUESTRO usuario).
    primera = next((o for o in ops if isinstance(o, dict)), None)
    if primera:
        print(f"\n  campos de una fila ({len(primera)}): {', '.join(sorted(primera))}")
        print("\n  ── UNA OPERACIÓN COMPLETA ──")
        print(json.dumps(primera, ensure_ascii=False, indent=2)[:2000])


def main() -> None:
    ap = argparse.ArgumentParser(description="Prueba TradeCaptureReport contra producción.")
    ap.add_argument("--fecha", help="AAAAMMDD — un solo día (atajo de --desde/--hasta)")
    ap.add_argument("--desde", help="AAAAMMDD")
    ap.add_argument("--hasta", help="AAAAMMDD")
    ap.add_argument("--crudo", action="store_true", help="imprime el JSON completo")
    args = ap.parse_args()

    if args.fecha:
        desde = hasta = args.fecha
    elif args.desde or args.hasta:
        desde = args.desde or args.hasta
        hasta = args.hasta or args.desde
    else:
        desde = hasta = _ultimo_habil()

    desde, hasta = postrade.fecha_api(desde), postrade.fecha_api(hasta)

    print(SEP)
    print("TradeCaptureReport — operaciones de A3 Mercados")
    print(SEP)
    print(f"  Base URL : {config.POSTRADE_BASE_URL}")
    print(f"  Rango    : {desde} → {hasta}")

    try:
        tok = postrade.token()
    except postrade.PostradeError as e:
        print(f"\n  ✗ No se pudo obtener token: {e}")
        raise SystemExit(2) from None
    print("  Token    : OK\n")

    # El manual se contradice con la capitalización. Se prueban las dos y gana
    # la que traiga operaciones — no la que "no falle": las dos pueden devolver
    # HTTP 200 y una traer vacío por no reconocer el parámetro.
    variantes = (
        ("mayúscula (tabla del manual)", {"DateFrom": desde, "DateTo": hasta}),
        ("minúscula (ejemplo del manual)", {"dateFrom": desde, "dateTo": hasta}),
    )

    ganadora = None
    for etiqueta, params in variantes:
        try:
            status, j = _llamar(tok, params)
        except requests.RequestException as e:
            print(f"  ✗ {etiqueta:<32} → red: {type(e).__name__}: {e}")
            continue

        desc = _describir(j)
        ops = j.get("Value") if isinstance(j, dict) and str(j.get("Status", "")).upper() == "OK" else None
        marca = "✓" if isinstance(ops, list) and ops else ("○" if isinstance(ops, list) else "✗")
        print(f"  {marca} {etiqueta:<32} → HTTP {status} | {desc}")

        if isinstance(ops, list) and ops and ganadora is None:
            ganadora = (etiqueta, params, ops, j)

    if ganadora is None:
        print()
        print(SEP)
        print("SIN OPERACIONES — y hay cuatro motivos posibles, no uno")
        print(SEP)
        print("  1. El HEADER está mal armado → si viste 'Invalid Authorization")
        print("     header.' (con la palabra 'header'). El prefijo correcto es")
        print("     'Token ' — está en POSTRADE_TOKEN_PREFIJO. NO es un permiso.")
        print("  2. Las CREDENCIALES no entran → si viste 'Invalid Authorization'")
        print("     SIN la palabra 'header'. Es otro problema, y otro arreglo.")
        print("  3. Ese día no hubo operaciones nuestras → si viste 'OK — 0")
        print("     operaciones'. Probá otra fecha con --fecha AAAAMMDD.")
        print("  4. Falta un parámetro obligatorio → si viste Code='404'. Es lo")
        print("     que le pasó a PartyDetails, que pedía Classification.")
        raise SystemExit(1)

    etiqueta, params, ops, j = ganadora
    print()
    print(SEP)
    print(f"FUNCIONA — parámetros en {etiqueta}")
    print(SEP)
    print(f"  {len(ops)} operaciones entre {desde} y {hasta}")
    _resumen(ops)

    if args.crudo:
        print("\n  ── JSON COMPLETO ──")
        print(json.dumps(j, ensure_ascii=False, indent=2))

    print()
    print(SEP)
    print("PARA EL CÓDIGO")
    print(SEP)
    clave = "DateFrom" if "DateFrom" in params else "dateFrom"
    print(f"  Los parámetros de fecha van como: {clave} / {clave.replace('From', 'To')}")
    print("  (el manual dice las dos cosas en páginas distintas — esta es la medida)")


if __name__ == "__main__":
    main()
