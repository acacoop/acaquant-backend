"""scripts/diag_byma_clearing.py — probador de la API Clearing Workflow de BYMA.

Claude NO tiene salida a `*.byma.com.ar` (el proxy del entorno remoto responde
403 al CONNECT — verificado 2026-08-14), así que el testeo se entrega como
script (REGLA #0): esto corre en el Droplet o en la PC de oficina y devuelve la
verdad cruda de qué contesta BYMA.

Qué resuelve, además de "pegarle al endpoint":

  1. **La doc se contradice sola** y no se puede saber cuál gana sin medir:
     - Los `curl` de ejemplo usan sufijo `.json` (`/deposits.json`,
       `/settlement.json/?...`), el OpenAPI NO lo tiene. → `--sufijo auto`
       prueba las dos formas y reporta cuál responde.
     - El OpenAPI lista 5 servers con etiquetas cruzadas ("Production
       principal" apunta a `hs-` que es HOMOLOGACIÓN según la doc; "Production"
       apunta a `localhost`). → acá los hosts se eligen por `--entorno`, con
       los que aparecen en los `curl` reales como default.
     - `accountIds` es `[string]` en la doc y `string` en el OpenAPI. →
       `--cuenta` es repetible y `--formato-cuentas` prueba repetido vs CSV.
  2. **Fuera de horario (00:00–08:00 ART) BYMA devuelve HTTP 200** con
     `{"status":200,"description":"Service Out of Service","type":"OOS"}`.
     Un cliente ingenuo lo toma por éxito y guarda basura. Acá se detecta y se
     marca como OOS, no como OK.
  3. **Los POST mueven garantías de verdad.** Depositar/extraer collateral no
     es un test: es una orden. Por eso los POST exigen `--confirmo` Y quedan
     BLOQUEADOS contra producción salvo `--permitir-prod` explícito.

Uso (read-only, lo primero que hay que correr):

    python -m scripts.diag_byma_clearing sondeo
    python -m scripts.diag_byma_clearing token
    python -m scripts.diag_byma_clearing obligaciones --cuenta 14 --fecha 2026-08-14

Escritura (SOLO homologación, una orden real en el entorno de pruebas):

    python -m scripts.diag_byma_clearing deposito  --cuenta-id 14 --monto 100 \
        --activo 534 --client-id 00000001 --confirmo
    python -m scripts.diag_byma_clearing extraccion --cuenta-id 14 --monto 100 \
        --moneda 032 --client-id 00000005 --confirmo

Credenciales (ninguna se hardcodea; van en el `.env` del Droplet):

    BYMA_CLEARING_TOKEN   Atajo: bearer ya emitido, pegado a mano. Gana sobre todo.
    BYMA_CLIENT_ID / BYMA_CLIENT_SECRET   client_credentials.
    BYMA_TOKEN_URL        Endpoint de token del portal. La doc NO lo publica;
                          el JWT de ejemplo dice que el IdP es Okta
                          (iss=https://tecval-sandbox.oktapreview.com/oauth2/auslwvzzoiDTktvcO1d7,
                          o sea el issuer de SANDBOX). Sin esta var no se puede
                          pedir token: el script lo dice y no inventa una URL.
    BYMA_SCOPES           Default "clearing clearingworkflow.create" (los scopes
                          que trae el JWT de ejemplo de la doc).

Todo lo que sale por pantalla se puede volcar crudo con `--out archivo.json`
para comparar contra la doc sin re-correr nada.
"""
from __future__ import annotations

import argparse
import base64
import binascii
import json
import os
import sys
import time
from datetime import UTC, date, datetime
from typing import Any

import requests

# --------------------------------------------------------------------------
# Entornos. Los hosts NO salen del OpenAPI (sus etiquetas están cruzadas: el
# server "Production principal" es `hs-` = homologación, y "Production" es
# localhost). Salen de los `curl` de la doc, que son los que alguien ejecutó.
# --------------------------------------------------------------------------
ENTORNOS: dict[str, dict[str, str]] = {
    "hs": {
        "api": "https://hs-clearing-api.byma.com.ar/clearing-workflow/v1",
        "portal": "https://hs-desarrolladores.byma.com.ar",
        "etiqueta": "HOMOLOGACIÓN",
    },
    "prod": {
        "api": "https://clearing-api.byma.com.ar/clearing-workflow/v1",
        "portal": "https://desarrolladores.byma.com.ar",
        "etiqueta": "PRODUCCIÓN",
    },
}

# Los otros hosts que nombra el OpenAPI. No se usan para operar: se sondean
# para saber cuáles existen de verdad antes de elegir uno.
HOSTS_OPENAPI = [
    "https://hs-api.byma.com.ar/clearing-workflow/v1",
    "https://api-dev.hs.byma.com.ar/clearing-workflow/v1",
    "https://clearing-api-dev.hs.byma.com.ar/clearing-workflow/v1",
    "https://hs-clearing-api.byma.com.ar/clearing-workflow/v1",
    "https://clearing-api.byma.com.ar/clearing-workflow/v1",
]

TIMEOUT_S = 60  # el OpenAPI declara timeout_milliseconds=60000
SCOPES_DEFAULT = "clearing clearingworkflow.create"


# --------------------------------------------------------------------------
# Salida
# --------------------------------------------------------------------------
def p(msg: str = "") -> None:
    print(msg, flush=True)


def titulo(msg: str) -> None:
    p()
    p(f"── {msg} " + "─" * max(0, 68 - len(msg)))


def json_corto(obj: Any, limite: int = 4000) -> str:
    txt = json.dumps(obj, indent=2, ensure_ascii=False)
    return txt if len(txt) <= limite else txt[:limite] + f"\n… (+{len(txt) - limite} chars)"


# --------------------------------------------------------------------------
# Token
# --------------------------------------------------------------------------
def decodificar_jwt(token: str) -> dict[str, Any] | None:
    """Payload del JWT SIN validar firma (solo para ver scopes y vencimiento)."""
    partes = token.split(".")
    if len(partes) != 3:
        return None
    try:
        crudo = base64.urlsafe_b64decode(partes[1] + "=" * (-len(partes[1]) % 4))
        return json.loads(crudo)
    except (binascii.Error, ValueError, UnicodeDecodeError):
        return None


def describir_token(token: str) -> None:
    payload = decodificar_jwt(token)
    p(f"  token   : {token[:18]}…{token[-8:]}  ({len(token)} chars)")
    if not payload:
        p("  (no es un JWT legible — se manda igual como bearer)")
        return
    exp = payload.get("exp")
    if exp:
        falta = int(exp) - int(time.time())
        cuando = datetime.fromtimestamp(int(exp), tz=UTC).isoformat()
        estado = f"vence en {falta // 60} min" if falta > 0 else "⚠️ VENCIDO"
        p(f"  exp     : {cuando}  ({estado})")
    p(f"  iss     : {payload.get('iss')}")
    p(f"  cid     : {payload.get('cid')}")
    p(f"  scopes  : {payload.get('scp')}")


def obtener_token(verbose: bool = True) -> str | None:
    """Bearer pegado a mano, o client_credentials contra BYMA_TOKEN_URL."""
    pegado = os.getenv("BYMA_CLEARING_TOKEN", "").strip()
    if pegado:
        if verbose:
            p("  origen  : BYMA_CLEARING_TOKEN (pegado a mano)")
            describir_token(pegado)
        return pegado

    cid = os.getenv("BYMA_CLIENT_ID", "").strip()
    secret = os.getenv("BYMA_CLIENT_SECRET", "").strip()
    url = os.getenv("BYMA_TOKEN_URL", "").strip()
    scopes = os.getenv("BYMA_SCOPES", SCOPES_DEFAULT).strip()

    faltan = [n for n, v in (("BYMA_CLIENT_ID", cid), ("BYMA_CLIENT_SECRET", secret),
                             ("BYMA_TOKEN_URL", url)) if not v]
    if faltan:
        p(f"  ❌ faltan credenciales: {', '.join(faltan)}")
        p("     (o pegá un bearer ya emitido en BYMA_CLEARING_TOKEN)")
        p("     La doc de BYMA NO publica el endpoint de token: hay que pedírselo")
        p("     al portal de desarrolladores. No se inventa acá.")
        return None

    if verbose:
        p(f"  origen  : client_credentials contra {url}")
        p(f"  scopes  : {scopes}")
    try:
        r = requests.post(
            url,
            data={"grant_type": "client_credentials", "scope": scopes},
            auth=(cid, secret),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=TIMEOUT_S,
        )
    except requests.RequestException as e:
        p(f"  ❌ error de red pidiendo token: {type(e).__name__}: {e}")
        return None

    if r.status_code != 200:
        p(f"  ❌ token HTTP {r.status_code}: {r.text[:500]}")
        return None
    try:
        token = r.json().get("access_token")
    except ValueError:
        p(f"  ❌ el token endpoint no devolvió JSON: {r.text[:300]}")
        return None
    if not token:
        p(f"  ❌ respuesta sin access_token: {json_corto(r.json())}")
        return None
    if verbose:
        describir_token(token)
    return token


# --------------------------------------------------------------------------
# Llamada + lectura de la respuesta
# --------------------------------------------------------------------------
def es_oos(cuerpo: Any) -> bool:
    """Fuera de horario BYMA contesta 200 con type=OOS. Éxito NO es."""
    return isinstance(cuerpo, dict) and cuerpo.get("type") == "OOS"


def llamar(metodo: str, url: str, token: str, *, params=None, body=None) -> dict[str, Any]:
    cab = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    if body is not None:
        cab["Content-Type"] = "application/json"
    t0 = time.perf_counter()
    try:
        r = requests.request(metodo, url, headers=cab, params=params, json=body,
                             timeout=TIMEOUT_S)
    except requests.RequestException as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}", "url": url,
                "ms": round((time.perf_counter() - t0) * 1000)}
    ms = round((time.perf_counter() - t0) * 1000)
    try:
        cuerpo: Any = r.json()
    except ValueError:
        cuerpo = r.text[:2000]
    return {
        "ok": r.status_code == 200 and not es_oos(cuerpo),
        "http": r.status_code,
        "oos": es_oos(cuerpo),
        "ms": ms,
        "url": r.url,
        "cuerpo": cuerpo,
    }


def informar(res: dict[str, Any], *, crudo: bool = True) -> None:
    if res.get("error"):
        p(f"  ❌ RED  {res['url']}  →  {res['error']}")
        return
    marca = "✅" if res["ok"] else ("🕗 OOS" if res["oos"] else "❌")
    p(f"  {marca} HTTP {res['http']}  {res['ms']}ms  {res['url']}")
    if res["oos"]:
        p("     Fuera del horario de servicio (08:00–00:00). BYMA manda 200 igual:")
        p("     cualquier job que mire solo el status code va a guardar esto como dato.")
    if crudo:
        p("     " + json_corto(res["cuerpo"]).replace("\n", "\n     "))


def urls_candidatas(base: str, ruta: str, sufijo: str) -> list[str]:
    """La doc usa `.json`, el OpenAPI no. `auto` prueba las dos."""
    con, sin = f"{base}{ruta}.json", f"{base}{ruta}"
    return {"json": [con], "sin": [sin], "auto": [con, sin]}[sufijo]


# --------------------------------------------------------------------------
# Comandos
# --------------------------------------------------------------------------
def cmd_sondeo(args) -> int:
    """¿Qué hosts de los que nombra la doc existen? Sin token, sin escribir."""
    titulo("SONDEO DE HOSTS (sin credenciales)")
    p("  Responde qué host resuelve y contesta. Un 401 es BUENA señal:")
    p("  significa que el endpoint EXISTE y solo falta el token.")
    p()
    for base in HOSTS_OPENAPI:
        url = f"{base}/obligations/settlement"
        try:
            r = requests.get(url, timeout=15)
            cuerpo = r.text[:160].replace("\n", " ")
            p(f"  HTTP {r.status_code:<4} {base}")
            p(f"           {cuerpo}")
        except requests.RequestException as e:
            p(f"  ---      {base}  →  {type(e).__name__}: {e}")
    return 0


def cmd_token(args) -> int:
    titulo("TOKEN")
    return 0 if obtener_token() else 1


def cmd_obligaciones(args) -> int:
    entorno = ENTORNOS[args.entorno]
    base = args.base or entorno["api"]
    fecha = args.fecha or date.today().isoformat()

    titulo(f"GET /obligations/settlement — {entorno['etiqueta']}")
    token = obtener_token(verbose=False)
    if not token:
        return 1

    # accountIds es [string] en la doc y string en el OpenAPI: se prueban las
    # dos codificaciones hasta que una traiga datos.
    formatos = (["repetido", "csv"] if args.formato_cuentas == "auto"
                else [args.formato_cuentas])
    paginas: list[Any] = []

    for fmt in formatos:
        cuentas: Any = (args.cuenta if fmt == "repetido" else ",".join(args.cuenta))
        for url in urls_candidatas(base, "/obligations/settlement", args.sufijo):
            params: dict[str, Any] = {"accountIds": cuentas, "settlementDate": fecha}
            if args.activo:
                params["assetId"] = args.activo
            p(f"\n  · cuentas={fmt}  sufijo={'.json' if url.endswith('.json') else '(sin)'}")

            bookmark, pagina = None, 0
            while True:
                if bookmark:
                    params["bookmark"] = bookmark
                res = llamar("GET", url, token, params=params)
                informar(res, crudo=(pagina == 0))
                if not res["ok"]:
                    break
                paginas.append(res["cuerpo"])
                pagina += 1
                resumir_obligaciones(res["cuerpo"], pagina)
                bookmark = leer_bookmark(res["cuerpo"])
                if not bookmark or pagina >= args.max_paginas:
                    if bookmark:
                        p(f"     ⏸️  corto en {args.max_paginas} páginas (--max-paginas)")
                    break
            if paginas:
                break
        if paginas:
            break

    if not paginas:
        p("\n  Ninguna combinación devolvió datos. Ver el detalle de arriba.")
    if args.out and paginas:
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(paginas, fh, indent=2, ensure_ascii=False)
        p(f"\n  💾 respuesta cruda → {args.out}")
    return 0 if paginas else 1


def leer_bookmark(cuerpo: Any) -> str | None:
    """El bookmark vive en `meta` (ejemplo) o en la raíz (diccionario de datos)."""
    if not isinstance(cuerpo, dict):
        return None
    meta = cuerpo.get("meta") if isinstance(cuerpo.get("meta"), dict) else cuerpo
    if meta.get("atEnd") is True:
        return None
    return meta.get("bookmark") or None


def resumir_obligaciones(cuerpo: Any, pagina: int) -> None:
    """La doc describe `entries` y el ejemplo devuelve `result`. Se aceptan ambos."""
    if not isinstance(cuerpo, dict):
        return
    filas = cuerpo.get("result") or cuerpo.get("entries") or []
    if not isinstance(filas, list):
        return
    p(f"     página {pagina}: {len(filas)} obligaciones")
    if not filas:
        return
    # Aviso de tipado: en el ejemplo de la doc las cantidades vienen a veces
    # como string ("-10360557", "-48351.000000") y a veces como number (0).
    mixtos = {
        campo for campo in ("pendingQuantity", "openQuantity", "instructedQuantity")
        if len({type(f.get(campo)).__name__ for f in filas if isinstance(f, dict)}) > 1
    }
    if mixtos:
        p(f"     ⚠️ tipos MIXTOS (string y number) en: {', '.join(sorted(mixtos))}")
        p("        → parsear con Decimal(str(x)), nunca asumir número.")
    claves = sorted(filas[0].keys()) if isinstance(filas[0], dict) else []
    p(f"     campos: {', '.join(claves)}")


def _post_collateral(args, ruta: str, etiqueta: str) -> int:
    entorno = ENTORNOS[args.entorno]
    base = args.base or entorno["api"]

    titulo(f"POST {ruta} — {entorno['etiqueta']}")
    if args.entorno == "prod" and not args.permitir_prod:
        p("  ⛔ BLOQUEADO. Esto registra una orden de collateral REAL en producción.")
        p("     Si de verdad va, agregá --permitir-prod (y sabé por qué).")
        return 2
    if not args.confirmo:
        p("  ⛔ Falta --confirmo. Un POST acá NO es un test: es una orden.")
        return 2
    if bool(args.activo) == bool(args.moneda):
        p("  ⛔ assetExternalRefDataSystemId y currencyId son MUTUAMENTE EXCLUYENTES:")
        p("     hay que mandar exactamente uno (--activo o --moneda).")
        return 2

    body = {"clientId": args.client_id, "amount": str(args.monto), "accountId": args.cuenta_id}
    if args.activo:
        body["assetExternalRefDataSystemId"] = args.activo
    else:
        body["currencyId"] = args.moneda

    p(f"  body: {json.dumps(body, ensure_ascii=False)}")
    p("  Recordatorio: clientId debe ser ÚNICO por día y por agente — si se repite,")
    p("  la orden se rechaza (o peor, se duplica). Llevá la cuenta.")

    token = obtener_token(verbose=False)
    if not token:
        return 1

    res = llamar("POST", urls_candidatas(base, ruta, args.sufijo)[0], token, body=body)
    informar(res)

    cuerpo = res.get("cuerpo")
    if res["ok"] and isinstance(cuerpo, dict):
        det = cuerpo.get("countersignDetails") or {}
        if det.get("isPendingCountersignApproval"):
            p(f"  ⏳ PENDIENTE DE APROBACIÓN (countersign) — id: {det.get('countersignId')}")
            p("     La orden NO está registrada todavía: alguien la tiene que aprobar.")
        else:
            p(f"  ✅ {etiqueta} registrada (isPendingCountersignApproval=false).")
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(res, fh, indent=2, ensure_ascii=False, default=str)
        p(f"  💾 respuesta cruda → {args.out}")
    return 0 if res["ok"] else 1


def cmd_deposito(args) -> int:
    return _post_collateral(args, "/collateral-management/deposits", "orden de DEPÓSITO")


def cmd_extraccion(args) -> int:
    return _post_collateral(args, "/collateral-management/withdraws", "orden de EXTRACCIÓN")


# --------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--entorno", choices=list(ENTORNOS), default="hs",
                    help="hs = homologación (default), prod = producción")
    ap.add_argument("--base", help="URL base a mano (pisa --entorno)")
    ap.add_argument("--sufijo", choices=["auto", "json", "sin"], default="auto",
                    help="`.json` como los curl de la doc, o sin sufijo como el OpenAPI")
    ap.add_argument("--out", help="volcar la respuesta cruda a este archivo")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("sondeo", help="qué hosts existen (sin token)").set_defaults(fn=cmd_sondeo)
    sub.add_parser("token", help="pedir/mostrar el bearer").set_defaults(fn=cmd_token)

    g = sub.add_parser("obligaciones", help="GET /obligations/settlement (read-only)")
    g.add_argument("--cuenta", action="append", required=True,
                   help="accountIds (repetible)")
    g.add_argument("--fecha", help="settlementDate YYYY-MM-DD (default: hoy)")
    g.add_argument("--activo", help="assetId, filtro opcional")
    g.add_argument("--formato-cuentas", choices=["auto", "repetido", "csv"], default="auto")
    g.add_argument("--max-paginas", type=int, default=5)
    g.set_defaults(fn=cmd_obligaciones)

    for nombre, fn, verbo in (("deposito", cmd_deposito, "depositar"),
                              ("extraccion", cmd_extraccion, "extraer")):
        w = sub.add_parser(nombre, help=f"POST collateral — {verbo} (ESCRIBE)")
        w.add_argument("--client-id", required=True, help="único por día y por agente")
        w.add_argument("--monto", required=True)
        w.add_argument("--cuenta-id", required=True, help="cuenta de concentración")
        w.add_argument("--activo", help="assetExternalRefDataSystemId (excluyente con --moneda)")
        w.add_argument("--moneda", help="currencyId: 032 ARS / 840 USD / 999 EXT")
        w.add_argument("--confirmo", action="store_true")
        w.add_argument("--permitir-prod", action="store_true")
        w.set_defaults(fn=fn)

    args = ap.parse_args(argv)
    hora = datetime.now().astimezone()
    p(f"BYMA Clearing Workflow — {hora:%Y-%m-%d %H:%M %Z}")
    if not 8 <= hora.hour < 24:
        p("⚠️ Estás fuera del horario de servicio (08:00–00:00): esperá respuestas OOS.")
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
