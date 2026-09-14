"""Tenencia de CVSA -> AcaQuant. Corre en la PC con Okta (BYMA esta detras de
AppGate y el Droplet no llega). Detalle en docs/BYMA_CUSTODIA.md."""
import csv
import io
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date

CLIENT_ID = ""
CLIENT_SECRET = ""
PARTICIPANT = "74"
INGEST_TOKEN = ""
CF_ID = ""
CF_SECRET = ""
API = "https://api.acaquant.com"

COLS = ("participantCode", "accountNumber", "cvsaIdentifier", "subBalanceType", "holding")
HOY = date.today().isoformat()


def post(url, data, headers):
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    return urllib.request.urlopen(req, timeout=60)


def get(url, headers):
    try:
        r = urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=60)
        return r.status, r.read(), r.headers.get("Content-Type", "")
    except urllib.error.HTTPError as e:
        return e.code, e.read(), ""


# 1. Token
try:
    tok = json.loads(post(
        "https://api.byma.com.ar/oauth/token/",
        urllib.parse.urlencode({
            "client_id": CLIENT_ID, "client_secret": CLIENT_SECRET,
            "grant_type": "client_credentials", "scope": "custodysecurities.read"}).encode(),
        {"Content-Type": "application/x-www-form-urlencoded"}).read())["access_token"]
except urllib.error.HTTPError as e:
    raise SystemExit(f"Token HTTP {e.code}: {e.read().decode('utf-8', 'replace')}") from e

# 2. Holdings (asincrono: 409 + uuid, despues X-UUID)
url = ("https://api.byma.com.ar/custody-securities/v1/holdings"
       f"?balanceDate={HOY}&participantCode={PARTICIPANT}")
h = {"Authorization": f"Bearer {tok}", "Accept": "*/*"}   # application/json da 406
def uuid_de(body):
    """El trabajo asincrono se reconoce por el uuid, NO por el status: BYMA
    contesta 202 y mete un "code": 409 adentro del cuerpo."""
    try:
        return json.loads(body).get("uuid")
    except Exception:
        return None


status, body, ctype = get(url, h)
uuid = uuid_de(body) if status != 200 else None
if uuid:
    for _ in range(12):
        time.sleep(5)
        status, body, ctype = get(url, {**h, "X-UUID": uuid})
        if status == 200 or not uuid_de(body):
            break
if status != 200:
    raise SystemExit(f"holdings HTTP {status}: {body[:300].decode('utf-8', 'replace')}")

# 3. Parseo: JSON envuelto en "result", o CSV con ';'
if "json" in ctype:
    j = json.loads(body)
    filas = j.get("result", j) if isinstance(j, dict) else j
else:
    rows = [r for r in csv.reader(io.StringIO(body.decode("utf-8", "replace")), delimiter=";") if r]
    if rows and rows[0][0].strip() in COLS:
        rows = rows[1:]
    filas = [dict(zip(COLS, r, strict=False)) for r in rows]
print(f"{len(filas)} filas de BYMA")
if not filas:
    raise SystemExit("0 filas: CVSA todavia no armo el dia. No se manda nada.")

# 4. A la app
try:
    r = post(f"{API}/api/ingest/custodia/holdings",
             json.dumps({"fecha": HOY, "docs": filas}).encode(),
             {"Content-Type": "application/json", "X-Ingest-Token": INGEST_TOKEN,
              "CF-Access-Client-Id": CF_ID, "CF-Access-Client-Secret": CF_SECRET})
    print(r.read().decode())
except urllib.error.HTTPError as e:
    # El cuerpo dice DE QUIEN es el error, y sin el no se puede distinguir:
    # HTML = lo corto Cloudflare Access (service token). JSON = llego a la API.
    cuerpo = e.read().decode("utf-8", "replace")
    quien = "CLOUDFLARE ACCESS" if "<html" in cuerpo[:200].lower() else "LA API"
    raise SystemExit(f"{quien} rechazo el POST: HTTP {e.code}\n{cuerpo[:500]}") from e
