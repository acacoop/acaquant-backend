"""Tenencia de CVSA -> AcaQuant. Corre en la PC con Okta (BYMA esta detras de
AppGate y el Droplet no llega). Detalle en docs/BYMA_CUSTODIA.md."""
import csv
import hashlib
import io
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, timedelta

CLIENT_ID = ""
CLIENT_SECRET = ""
PARTICIPANT = "74"
INGEST_TOKEN = ""
CF_ID = ""
CF_SECRET = ""
API = "https://api.acaquant.com"
# Cloudflare corta el UA por defecto de urllib con "error code: 1010".
UA = "AcaQuant-custodia/1.0"

COLS = ("participantCode", "accountNumber", "cvsaIdentifier", "subBalanceType", "holding")


def habil_anterior(d: date) -> date:
    """El dia habil anterior. No mira feriados: si cae uno, BYMA devuelve lo que
    tenga y se ve en el conteo."""
    d -= timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


# SE PIDEN DOS FECHAS, y no es redundante.
#
# Medido: BYMA trae titulos que liquidaron el 10 y NO tiene el cierre del 11 —
# su foto va dos dias atras, no uno. Pero la guardabamos bajo la fecha que
# PEDIMOS (hoy), asi que la etiqueta mentia: el cartel de «dias distintos» de la
# vista comparaba nuestro 14 contra el 14 de Aunesa y nunca se encendia. El
# aviso que tenia que delatar el problema era el que lo tapaba.
#
# Pidiendo las dos y comparando el contenido se sabe que hace el gateway con
# `balanceDate`: si devuelve lo MISMO para las dos, lo ignora y da lo ultimo que
# tiene.
FECHAS = [date.today(), habil_anterior(date.today())]


def post(url, data, headers):
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    return urllib.request.urlopen(req, timeout=60)


def get(url, headers):
    try:
        r = urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=60)
        return r.status, r.read(), r.headers.get("Content-Type", "")
    except urllib.error.HTTPError as e:
        return e.code, e.read(), ""


def uuid_de(body):
    """El trabajo asincrono se reconoce por el uuid, NO por el status: BYMA
    contesta 202 y mete un "code": 409 adentro del cuerpo."""
    try:
        return json.loads(body).get("uuid")
    except Exception:
        return None


def huella(filas) -> str:
    """Para comparar dos respuestas sin mirarlas fila por fila."""
    if not filas:
        return "(vacio)"
    return hashlib.sha256(
        json.dumps(sorted(json.dumps(x, sort_keys=True) for x in filas)).encode()
    ).hexdigest()[:16]


# 1. Token
try:
    tok = json.loads(post(
        "https://api.byma.com.ar/oauth/token/",
        urllib.parse.urlencode({
            "client_id": CLIENT_ID, "client_secret": CLIENT_SECRET,
            "grant_type": "client_credentials", "scope": "custodysecurities.read"}).encode(),
        {"Content-Type": "application/x-www-form-urlencoded",
         "User-Agent": UA}).read())["access_token"]
except urllib.error.HTTPError as e:
    raise SystemExit(f"Token HTTP {e.code}: {e.read().decode('utf-8', 'replace')}") from e

H = {"Authorization": f"Bearer {tok}", "Accept": "*/*",   # application/json da 406
     "User-Agent": UA}


def bajar(f: date):
    """Las filas crudas de `balanceDate = f`. Asincrono: 202 + uuid, despues X-UUID."""
    url = ("https://api.byma.com.ar/custody-securities/v1/holdings"
           f"?balanceDate={f}&participantCode={PARTICIPANT}")
    status, body, ctype = get(url, H)
    uuid = uuid_de(body) if status != 200 else None
    if uuid:
        for _ in range(12):
            time.sleep(5)
            status, body, ctype = get(url, {**H, "X-UUID": uuid})
            if status == 200 or not uuid_de(body):
                break
    if status != 200:
        raise SystemExit(f"holdings {f} HTTP {status}: {body[:300].decode('utf-8', 'replace')}")

    # JSON envuelto en "result", o CSV con ';'
    if "json" in ctype:
        j = json.loads(body)
        return j.get("result", j) if isinstance(j, dict) else j
    rows = [r for r in csv.reader(io.StringIO(body.decode("utf-8", "replace")), delimiter=";") if r]
    if rows and rows[0][0].strip() in COLS:
        rows = rows[1:]
    return [dict(zip(COLS, r, strict=False)) for r in rows]


# 2. Las dos fechas
bajadas = {}
for f in FECHAS:
    filas = bajar(f)
    bajadas[f] = filas
    print(f"{f}  {len(filas):>6} filas  huella {huella(filas)}")

# LA PREGUNTA: ¿el gateway distingue las fechas, o devuelve siempre lo ultimo?
hs = {huella(v) for v in bajadas.values()}
if len(hs) == 1 and all(bajadas.values()):
    print("\n>> LAS DOS FECHAS DEVUELVEN EXACTAMENTE LO MISMO.")
    print("   BYMA ignora `balanceDate` y da lo ultimo que tiene: la fecha con la")
    print("   que guardamos es una etiqueta nuestra, no la del contenido.")
else:
    print("\n>> Las respuestas son DISTINTAS: el gateway si distingue por fecha.")

# 3. A la app, una por fecha
for f, filas in bajadas.items():
    if not filas:
        print(f"{f}: 0 filas, no se manda (la foto anterior queda intacta).")
        continue
    try:
        r = post(f"{API}/api/ingest/custodia/holdings",
                 json.dumps({"fecha": str(f), "docs": filas}).encode(),
                 {"Content-Type": "application/json", "X-Ingest-Token": INGEST_TOKEN,
                  "CF-Access-Client-Id": CF_ID, "CF-Access-Client-Secret": CF_SECRET,
                  "User-Agent": UA})
        print(f"{f}: {r.read().decode()}")
    except urllib.error.HTTPError as e:
        # El cuerpo dice DE QUIEN es el error: si parsea como JSON llego a la
        # API; si no, lo corto Cloudflare (contesta texto plano).
        cuerpo = e.read().decode("utf-8", "replace")
        try:
            json.loads(cuerpo)
            quien = "LA API"
        except ValueError:
            quien = "CLOUDFLARE"
        raise SystemExit(f"{quien} rechazo el POST: HTTP {e.code}\n{cuerpo[:500]}") from e
