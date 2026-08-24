"""scripts/diag_postrade_posicion.py — probar PositionReport contra PRODUCCIÓN.

READ-ONLY. Solo hace GET a `PosTrade/PositionReport`. No escribe en la base ni
llama a ningún otro método.

`PositionReport` es el reporte de posición de la cámara: qué tenemos abierto,
a qué precio promedio, con qué precio de ajuste y cuánto dio el resultado
diario. Del manual, cada fila trae `Account`, `Instrument` (símbolo, CFICode,
tipo, unidad de medida), `AvgPX`, `SettlPrice`, `DailySettlement` y
`PositionQty` con `LongQty`/`ShortQty`.

Tres parámetros, y dos de ellos cambian **qué se devuelve**, no cómo se muestra:

    ClearingBusinessDate   AAAAMMDD — OBLIGATORIO
    viewDetails            true = portfolio detallado / false = resumido
    viewPafg               true = contratos PAF G / false = consolidado del ALyC

Por eso el script recorre las combinaciones en vez de elegir una a ciegas: sin
compararlas no se puede saber cuál responde la pregunta que uno tiene.

Y prueba las **dos capitalizaciones** del parámetro de fecha, porque el manual
se contradice: la tabla dice `ClearingBusinessDate` y el ejemplo de al lado usa
`clearingBusinessDate`. Con `PartyDetails` ya vimos que esta API contesta
`404 NotFound` cuando le falta un parámetro — o sea que la capitalización
equivocada puede **parecer "no hay posición"** cuando en realidad es el nombre
del campo.

Uso (desde la raíz del repo, en el Droplet):

    python -m scripts.diag_postrade_posicion                  # último día hábil
    python -m scripts.diag_postrade_posicion --fecha 20260821
    python -m scripts.diag_postrade_posicion --fecha 20260821 --crudo
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
PATH = "PosTrade/PositionReport"


def _ultimo_habil() -> str:
    """AAAAMMDD del último día hábil. Sin feriados: solo evita sábado y domingo.

    Si cae feriado la API contesta vacío y el script lo dice — no se hace pasar
    un feriado por "no está habilitado".
    """
    d = date.today() - timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d.strftime("%Y%m%d")


def _llamar(tok: str, params: dict) -> tuple[int, dict | str]:
    """GET crudo. Devuelve (status_http, json_o_texto).

    A propósito NO usa `postrade.leer()`: acá se está midiendo qué forma de los
    parámetros funciona, así que hace falta ver la respuesta tal cual viene,
    incluido el sobre de error.
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
    if str(j.get("Status", "")).strip().upper() == "OK":
        v = j.get("Value")
        if v is None:
            return "OK pero Value=null"
        if isinstance(v, list):
            return f"OK — {len(v)} posiciones"
        return f"OK — Value es {type(v).__name__}"
    detalle = " ".join(
        str(j[k]) for k in ("ErrorMessage", "ErrorDescription") if j.get(k)
    )
    return (
        f"Status={str(j.get('Status')).strip()!r} "
        f"Code={str(j.get('Code')).strip()!r} {detalle[:160]}"
    )


def _valor(j) -> list | None:
    """El `Value` si el sobre vino OK y es una lista; si no, None."""
    if not isinstance(j, dict) or str(j.get("Status", "")).strip().upper() != "OK":
        return None
    v = j.get("Value")
    return v if isinstance(v, list) else None


def _resumen(pos: list) -> None:
    """Qué hay adentro de la posición."""
    print("\n  ── QUÉ TRAJO ──")

    cuentas = Counter(str(p.get("Account")) for p in pos if isinstance(p, dict))
    print(f"  Cuentas ({len(cuentas)}): "
          f"{', '.join(f'{k}={v}' for k, v in cuentas.most_common(10))}")

    for campo in ("Currency", "SecurityExchange", "SettlSessID", "ClearingBusinessDate"):
        c = Counter(str(p.get(campo)) for p in pos if isinstance(p, dict))
        if c:
            print(f"  {campo:<22} {', '.join(f'{k}={v}' for k, v in c.most_common(6))}")

    # El instrumento viene anidado: sin desanidarlo no se ve QUÉ tenemos.
    tipos = Counter()
    simbolos = Counter()
    for p in pos:
        inst = p.get("Instrument") if isinstance(p, dict) else None
        if isinstance(inst, dict):
            tipos[str(inst.get("SecurityType"))] += 1
            simbolos[str(inst.get("Symbol"))] += 1
    if tipos:
        print(f"  SecurityType           {', '.join(f'{k}={v}' for k, v in tipos.most_common(8))}")
    if simbolos:
        print(f"  Símbolos ({len(simbolos)}): {', '.join(list(simbolos)[:12])}")

    # Lo que importa del reporte: cuánto estamos largos, cuánto cortos y el
    # resultado del día. Se suma acá porque un conteo de filas no dice nada.
    largo = corto = 0.0
    for p in pos:
        for q in (p.get("PositionQty") or []) if isinstance(p, dict) else []:
            if isinstance(q, dict):
                largo += float(q.get("LongQty") or 0)
                corto += float(q.get("ShortQty") or 0)
    diario = sum(
        float(p.get("DailySettlement") or 0) for p in pos if isinstance(p, dict)
    )
    print(f"\n  LongQty total   {largo:,.0f}")
    print(f"  ShortQty total  {corto:,.0f}")
    print(f"  DailySettlement {diario:,.2f}  (suma de todas las filas)")

    primera = next((p for p in pos if isinstance(p, dict)), None)
    if primera:
        print(f"\n  campos de una fila ({len(primera)}): {', '.join(sorted(primera))}")
        print("\n  ── UNA POSICIÓN COMPLETA ──")
        print(json.dumps(primera, ensure_ascii=False, indent=2)[:1800])


def main() -> None:
    ap = argparse.ArgumentParser(description="Prueba PositionReport contra producción.")
    ap.add_argument("--fecha", help="AAAAMMDD (default: último día hábil)")
    ap.add_argument("--crudo", action="store_true", help="imprime el JSON completo")
    args = ap.parse_args()

    fecha = postrade.fecha_api(args.fecha or _ultimo_habil())

    print(SEP)
    print("PositionReport — reporte de posición de la cámara")
    print(SEP)
    print(f"  Base URL : {config.POSTRADE_BASE_URL}")
    print(f"  Fecha    : {fecha}")

    try:
        tok = postrade.token()
    except postrade.PostradeError as e:
        print(f"\n  ✗ No se pudo obtener token: {e}")
        raise SystemExit(2) from None
    print(f"  Header   : Authorization: {(config.POSTRADE_TOKEN_PREFIJO or '')!r} + token")
    print("  Token    : OK\n")

    # PASO 1 — la capitalización de la fecha. El manual dice las dos cosas.
    print("  1) NOMBRE DEL PARÁMETRO DE FECHA (el manual se contradice)")
    clave = None
    for k in ("ClearingBusinessDate", "clearingBusinessDate"):
        try:
            status, j = _llamar(tok, {k: fecha, "viewDetails": "false"})
        except requests.RequestException as e:
            print(f"     ✗ {k:<24} → red: {type(e).__name__}: {e}")
            continue
        v = _valor(j)
        marca = "✓" if v else ("○" if v is not None else "✗")
        print(f"     {marca} {k:<24} → HTTP {status} | {_describir(j)}")
        if v and clave is None:
            clave = k
    if clave is None:
        # Ninguna trajo datos. Igual seguimos con la de la tabla para que el
        # paso 2 muestre los errores concretos de cada combinación, en vez de
        # cortar acá con un "no anduvo" sin detalle.
        clave = "ClearingBusinessDate"
        print(f"     → ninguna trajo posiciones; sigo con {clave} para ver el detalle")
    else:
        print(f"     → sirve: {clave}")

    # PASO 2 — las combinaciones. viewDetails/viewPafg cambian QUÉ devuelve,
    # no cómo se ve, así que hay que compararlas para saber cuál sirve.
    print("\n  2) QUÉ VISTA DE LA POSICIÓN DEVUELVE CADA COMBINACIÓN")
    combos = (
        ("consolidado ALyC, resumido", {"viewDetails": "false"}),
        ("consolidado ALyC, detallado", {"viewDetails": "true"}),
        ("PAF G, contratos", {"viewDetails": "false", "viewPafg": "true"}),
        ("PAF G, relaciones", {"viewDetails": "true", "viewPafg": "true"}),
    )

    mejor = None
    for etiqueta, extra in combos:
        params = {clave: fecha, **extra}
        try:
            status, j = _llamar(tok, params)
        except requests.RequestException as e:
            print(f"     ✗ {etiqueta:<28} → red: {type(e).__name__}: {e}")
            continue
        v = _valor(j)
        marca = "✓" if v else ("○" if v is not None else "✗")
        print(f"     {marca} {etiqueta:<28} → HTTP {status} | {_describir(j)}")
        if v and (mejor is None or len(v) > len(mejor[1])):
            mejor = (etiqueta, v, j)

    if mejor is None:
        print()
        print(SEP)
        print("SIN POSICIONES — cuatro motivos posibles, no uno")
        print(SEP)
        print("  1. El HEADER está mal armado → si viste 'Invalid Authorization")
        print("     header.' (con la palabra 'header'). El prefijo correcto es")
        print("     'Token '. NO es un problema de permisos.")
        print("  2. Las CREDENCIALES no entran → 'Invalid Authorization' SIN la")
        print("     palabra 'header'. Es otro problema y otro arreglo.")
        print("  3. Ese día no había posición → 'OK — 0 posiciones'. Probá otra")
        print("     fecha con --fecha AAAAMMDD.")
        print("  4. El método no está habilitado → Code='403', o Code='404' si")
        print("     falta un parámetro (le pasó a PartyDetails, que pedía")
        print("     Classification). Eso sí es reclamo al proveedor.")
        raise SystemExit(1)

    etiqueta, pos, j = mejor
    print()
    print(SEP)
    print(f"FUNCIONA — la vista con más filas es: {etiqueta}")
    print(SEP)
    print(f"  {len(pos)} posiciones al {fecha}")
    _resumen(pos)

    if args.crudo:
        print("\n  ── JSON COMPLETO ──")
        print(json.dumps(j, ensure_ascii=False, indent=2))

    print()
    print(SEP)
    print("PARA EL CÓDIGO")
    print(SEP)
    print(f"  Parámetro de fecha : {clave}")
    print(f"  Vista con más datos: {etiqueta}")
    print("  (el manual escribe el parámetro de dos formas distintas en páginas")
    print("   contiguas — esta es la medida, no la lectura)")


if __name__ == "__main__":
    main()
