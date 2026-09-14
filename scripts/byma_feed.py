"""byma_feed.py — la tenencia de la CAJA DE VALORES, desde la PC con Okta.

  *** ESTE SCRIPT CORRE EN LA PC DE OFICINA, NO EN EL DROPLET. ***

POR QUE EXISTE
==============
Las APIs de custodia de BYMA no estan publicadas en internet abierta: estan
detras de AppGate SDP. Medido el 2026-09-12: con AppGate prendido conectan, con
AppGate apagado dan TIMEOUT **con la misma IP publica de salida** — o sea no es
un filtro por IP, es que el trafico va por el tunel. Desde el Droplet tambien da
timeout, mientras Aunesa, BCRA y Finnhub conectan sin problema.

Asi que el unico que entra es esta PC. Hace lo minimo:

    1. Pide el token a BYMA (client_credentials).
    2. Baja GET /holdings (asincrono: 409 + uuid, despues X-UUID).
    3. Manda las filas CRUDAS a POST /api/ingest/custodia/holdings.

**No interpreta nada.** Traducir el codigo de la Caja a nuestro instrumento,
las guardas y el reemplazo de la foto pasan del lado del servidor, que ya los
tiene escritos y testeados. Este script es una pieza tonta y reemplazable: el
dia que el Droplet pueda llegar a BYMA, se apaga y el job de alla hace lo mismo
llamando a la MISMA funcion.

Es el mismo patron que `eikon_feed_simple.py` (Refinitiv) y que la ingesta de
MAE: la oficina tiene el acceso, el servidor tiene la logica, y el puente es un
POST con un token dedicado. Ver `docs/SECURITY.md`.

STANDALONE a proposito: solo stdlib, nada de instalar ni de copiar el repo.

COMO SE USA
===========
    1. Completa la configuracion de abajo (una sola vez).
    2. Con AppGate/Okta CONECTADO:   python byma_feed.py
    3. Para dejarlo andando solo, Programador de tareas de Windows cada 1 hora
       (el gateway de BYMA cachea su respuesta 60 min: mas seguido no trae nada).

    python byma_feed.py --fecha 2026-09-10    # otro dia (max. 7 atras)
    python byma_feed.py --solo-bajar          # baja y muestra, NO manda nada
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, timedelta

# ═══════════════════════════════════════════════════════════════════════════
#  CONFIGURACION — completar una sola vez
# ═══════════════════════════════════════════════════════════════════════════

# Credenciales de BYMA. Las genera el Portal de Desarrolladores al elegir la
# aplicacion en "Como obtener un token de acceso?". El client_id arranca con
# '0oa' (cero, no letra O).
BYMA_CLIENT_ID = ""
BYMA_CLIENT_SECRET = ""
BYMA_PARTICIPANT_CODE = "74"          # nuestro codigo de participante en CVSA

# A donde se mandan las filas. El token tiene que ser el MISMO que
# DOLAR_INGEST_TOKEN en el .env del Droplet.
API_URL = "https://api.acaquant.com"
INGEST_TOKEN = ""

# Cloudflare Access: el service token de la oficina (los mismos dos valores que
# ya usa el feed de Eikon). Sin esto, CF corta antes de llegar a la API.
CF_ACCESS_CLIENT_ID = ""
CF_ACCESS_CLIENT_SECRET = ""

# ═══════════════════════════════════════════════════════════════════════════

TOKEN_URL = "https://api.byma.com.ar/oauth/token/"
BASE = "https://api.byma.com.ar/custody-securities/v1"
SCOPE = "custodysecurities.read"
TIMEOUT_S = 60

# El baile del X-UUID: la primera llamada dispara el trabajo y devuelve 409 con
# un uuid; se repregunta hasta que conteste 200. CON TECHO: un poll sin limite
# no falla nunca, simplemente no termina, y nadie se entera.
ASYNC_INTENTOS = 12
ESPERA_INICIAL_S = 2.0
ESPERA_MAX_S = 15.0

# Orden de las columnas del CSV. El gateway no manda cabecera de forma
# confiable, asi que el orden ES el contrato.
COLUMNAS = ("participantCode", "accountNumber", "cvsaIdentifier",
            "subBalanceType", "holding")

# En consolas Windows con codepage viejo, un caracter raro en un print puede
# matar el script entero. Nunca crashear por imprimir.
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(errors="replace")
    except Exception:
        pass


def _post(url: str, data: bytes, headers: dict, timeout: int = TIMEOUT_S):
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    return urllib.request.urlopen(req, timeout=timeout)


def token() -> str:
    """client_credentials. El token dura 24h pero acá se pide uno por corrida."""
    faltan = [n for n, v in (("BYMA_CLIENT_ID", BYMA_CLIENT_ID),
                             ("BYMA_CLIENT_SECRET", BYMA_CLIENT_SECRET)) if not v]
    if faltan:
        raise SystemExit(f"Falta completar en este archivo: {', '.join(faltan)}")

    # Los errores tontos que dan un 400 y no se ven a simple vista. Se muestra
    # la LONGITUD, nunca el valor: alcanza para saber si llegan enteras.
    # Referencia medida en el portal: client_id 20 chars, client_secret 64.
    for nombre, valor, largo in (("BYMA_CLIENT_ID", BYMA_CLIENT_ID, 20),
                                 ("BYMA_CLIENT_SECRET", BYMA_CLIENT_SECRET, 64)):
        aviso = ""
        if valor != valor.strip():
            aviso = "  <-- TIENE ESPACIOS al principio o al final"
        elif '"' in valor or "'" in valor:
            aviso = "  <-- TIENE COMILLAS adentro del valor"
        elif len(valor) != largo:
            aviso = f"  <-- ojo: en el portal es de {largo} caracteres"
        print(f"  {nombre:20} {len(valor)} caracteres{aviso}")

    data = urllib.parse.urlencode({
        "client_id": BYMA_CLIENT_ID, "client_secret": BYMA_CLIENT_SECRET,
        "grant_type": "client_credentials", "scope": SCOPE}).encode()
    try:
        with _post(TOKEN_URL, data,
                   {"Content-Type": "application/x-www-form-urlencoded"}, 20) as r:
            j = json.loads(r.read())
    # ⚠️ HTTPError PRIMERO: es subclase de URLError, así que al revés el except
    # de red se lo traga y muestra el consejo del timeout ante un 400 — que es
    # justo el caso en que la red anda perfecto y el cuerpo del error tiene la
    # respuesta. Pasó en la primera corrida real.
    except urllib.error.HTTPError as e:
        cuerpo = e.read()[:400].decode("utf-8", "replace")
        raise SystemExit(
            f"BYMA rechazo el token: HTTP {e.code}\n"
            f"  cuerpo: {cuerpo}\n\n"
            ">> La red esta BIEN (llegaste hasta BYMA y te contesto).\n"
            ">> Es la credencial: revisa que client_id y client_secret sean los que\n"
            ">> muestra el portal AHORA. Si el secret se regenero alguna vez, el\n"
            ">> anterior deja de andar y este es el error exacto que da.") from e
    except urllib.error.URLError as e:
        raise SystemExit(
            f"No se pudo llegar a BYMA: {e}\n\n"
            ">> Si es un TIMEOUT: fijate que AppGate/Okta este CONECTADO.\n"
            ">> Las APIs de BYMA no se alcanzan desde internet abierta.") from e

    print(f"  token OK (dura {j.get('expires_in')}s, scope {j.get('scope')})")
    return j["access_token"]


def _parsear(cuerpo: bytes, ctype: str) -> list[dict]:
    """El gateway contesta JSON o CSV segun el metodo. `/holdings` manda CSV."""
    if "json" in ctype.lower():
        j = json.loads(cuerpo)
        return j.get("result", j) if isinstance(j, dict) else j
    # CSV separado con ';' (medido). La cabecera no esta garantizada: se detecta.
    filas = [f for f in csv.reader(io.StringIO(cuerpo.decode("utf-8", "replace")),
                                   delimiter=";") if f and any(c.strip() for c in f)]
    if filas and filas[0][0].strip() in COLUMNAS:
        filas = filas[1:]
    return [dict(zip(COLUMNAS, f)) for f in filas]


def holdings(tok: str, fecha: str) -> list[dict]:
    """GET /holdings — asincrono. Devuelve las filas crudas."""
    params = urllib.parse.urlencode(
        {"balanceDate": fecha, "participantCode": BYMA_PARTICIPANT_CODE})
    url = f"{BASE}/holdings?{params}"
    # `Accept: application/json` da 406: cada metodo elige su formato y el
    # gateway no negocia.
    headers = {"Authorization": f"Bearer {tok}", "Accept": "*/*",
               "User-Agent": "AcaQuant-feed/1.0"}

    def pedir(uuid=None):
        h = dict(headers)
        if uuid:
            h["X-UUID"] = uuid
        req = urllib.request.Request(url, headers=h)
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
                return r.status, r.read(), r.headers.get("Content-Type", "")
        except urllib.error.HTTPError as e:
            return e.code, e.read(), (e.headers.get("Content-Type", "") if e.headers else "")

    status, cuerpo, ctype = pedir()
    if status == 200:
        return _parsear(cuerpo, ctype)
    if status != 409:
        raise SystemExit(f"/holdings HTTP {status}: {cuerpo[:300].decode('utf-8','replace')}")

    uuid = json.loads(cuerpo).get("uuid")
    print(f"  trabajo disparado (uuid {uuid}), esperando...")
    espera = ESPERA_INICIAL_S
    for intento in range(1, ASYNC_INTENTOS + 1):
        time.sleep(espera)
        espera = min(espera * 1.5, ESPERA_MAX_S)
        status, cuerpo, ctype = pedir(uuid)
        if status == 200:
            print(f"  listo en el intento {intento}")
            return _parsear(cuerpo, ctype)
        if status != 409:
            raise SystemExit(f"/holdings HTTP {status}: {cuerpo[:300].decode('utf-8','replace')}")
    raise SystemExit(f"El trabajo {uuid} no termino tras {ASYNC_INTENTOS} intentos.")


def mandar(fecha: str, filas: list[dict]) -> dict:
    """POST a la API. El server hace todo lo demas."""
    if not INGEST_TOKEN:
        raise SystemExit("Falta completar INGEST_TOKEN en este archivo.")
    data = json.dumps({"fecha": fecha, "docs": filas}).encode()
    headers = {"Content-Type": "application/json", "X-Ingest-Token": INGEST_TOKEN}
    if CF_ACCESS_CLIENT_ID and CF_ACCESS_CLIENT_SECRET:
        headers["CF-Access-Client-Id"] = CF_ACCESS_CLIENT_ID
        headers["CF-Access-Client-Secret"] = CF_ACCESS_CLIENT_SECRET
    try:
        with _post(f"{API_URL}/api/ingest/custodia/holdings", data, headers) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise SystemExit(f"La API contesto {e.code}: "
                         f"{e.read()[:400].decode('utf-8','replace')}") from e


def main() -> int:
    ap = argparse.ArgumentParser(description="Tenencia de CVSA -> AcaQuant.")
    ap.add_argument("--fecha", help="YYYY-MM-DD (default: hoy). Max. 7 dias atras.")
    ap.add_argument("--solo-bajar", action="store_true",
                    help="baja y muestra, sin mandar nada a la API")
    args = ap.parse_args()

    f = date.fromisoformat(args.fecha) if args.fecha else date.today()
    if f < date.today() - timedelta(days=7):
        print(f"{f} esta a mas de 7 dias: CVSA ya la purgo y devuelve vacio.")
        return 2

    print(f"\nCUSTODIA CVSA - {f}")
    print("-" * 60)
    tok = token()
    filas = holdings(tok, f.isoformat())
    print(f"  {len(filas)} filas bajadas de BYMA")

    if not filas:
        # Vacio no es "no hay tenencia", es "todavia no la armaron". No se manda:
        # el server igual no borraria, pero mejor no gastar el viaje.
        print("\n  0 filas. Puede ser temprano: CVSA todavia no armo el dia.")
        print("  NO se manda nada (la foto anterior queda intacta).")
        return 0

    if args.solo_bajar:
        print("\n  --solo-bajar: no se manda nada. Primeras 3 filas:")
        for x in filas[:3]:
            print("   ", x)
        return 0

    print(f"  mandando a {API_URL} ...")
    r = mandar(f.isoformat(), filas)
    print(f"\n  OK: {r.get('escrito')} filas escritas, {r.get('cuentas')} cuentas, "
          f"{r.get('codigos_sin_asset')} codigos sin instrumento nuestro")
    if r.get("sin_cuenta_reconocible"):
        print(f"  OJO: {r['sin_cuenta_reconocible']} filas con accountNumber raro")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit as e:
        if e.code:
            print(f"\nERROR: {e}" if str(e) not in ("1", "2") else "")
            try:
                input("\n[ENTER] para cerrar...")
            except Exception:
                pass
        raise
