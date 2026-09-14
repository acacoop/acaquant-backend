"""Custodia CVSA -> AcaQuant: tenencias + movimientos del dia.

Corre en la PC con Okta (BYMA esta detras de AppGate y el Droplet no llega).
Detalle en docs/BYMA_CUSTODIA.md. Los dos metodos son ASINCRONOS (uuid).
El script NO interpreta nada: baja y reenvia; la logica vive en el backend."""
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
# CVSA usa TRES espacios de numeracion para el MISMO agente. Las tenencias hay
# que pedirlas para los tres: /holdings devuelve solo el que se le pide.
#   74     comitentes     las cuentas de clientes
#   70074  liquidadoras   70074/10000 gral, 70074/50000 Licis
#   80074  garantias      80074/555555555 clientes, /222222222 house,
#                         /888888888 default funds
PARTICIPANTES = ("74", "70074", "80074")
INGEST_TOKEN = ""
CF_ID = ""
CF_SECRET = ""
API = "https://api.acaquant.com"
# Cloudflare corta el UA por defecto de urllib con "error code: 1010".
UA = "AcaQuant-custodia/1.0"

BYMA = "https://api.byma.com.ar/custody-securities/v1"
COLS = ("participantCode", "accountNumber", "cvsaIdentifier", "subBalanceType", "holding")
# /transactions/today trae 11 columnas: las 9 de /transactions + la contraparte.
COLS_MOV = ("participantCode", "settlementDate", "accountNumber", "cvsaIdentifier",
            "securitiesSubBalanceType", "volume", "amount", "currency",
            "instructionReference", "counterparty", "counterpartySecuritiesAcc")
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
        {"Content-Type": "application/x-www-form-urlencoded",
         "User-Agent": UA}).read())["access_token"]
except urllib.error.HTTPError as e:
    raise SystemExit(f"Token HTTP {e.code}: {e.read().decode('utf-8', 'replace')}") from e

# 2. Las dos bajadas (asincronas: 202 + uuid en el cuerpo, despues X-UUID)
h = {"Authorization": f"Bearer {tok}", "Accept": "*/*",   # application/json da 406
     "User-Agent": UA}
def uuid_de(body):
    """El trabajo asincrono se reconoce por el uuid, NO por el status: BYMA
    contesta 202 y mete un "code": 409 adentro del cuerpo."""
    try:
        return json.loads(body).get("uuid")
    except Exception:
        return None


def bajar(url, cols, que):
    """El baile del uuid + parseo. Lo hacen IGUAL holdings y transactions/today."""
    status, body, ctype = get(url, h)
    uuid = uuid_de(body) if status != 200 else None
    if uuid:
        for _ in range(12):
            time.sleep(5)
            status, body, ctype = get(url, {**h, "X-UUID": uuid})
            if status == 200 or not uuid_de(body):
                break
    if status != 200:
        raise SystemExit(f"{que} HTTP {status}: {body[:300].decode('utf-8', 'replace')}")
    # JSON envuelto en "result", o CSV con ';'
    if "json" in ctype:
        j = json.loads(body)
        return j.get("result", j) if isinstance(j, dict) else j
    rows = [r for r in csv.reader(io.StringIO(body.decode("utf-8", "replace")), delimiter=";") if r]
    if rows and rows[0][0].strip().lower() in [c.lower() for c in cols]:
        rows = rows[1:]   # la cabecera del CSV viene en MAYUSCULAS
    return [dict(zip(cols, r, strict=False)) for r in rows]


def mandar(ruta, cuerpo, que):
    try:
        r = post(f"{API}{ruta}", json.dumps(cuerpo).encode(),
                 {"Content-Type": "application/json", "X-Ingest-Token": INGEST_TOKEN,
                  "CF-Access-Client-Id": CF_ID, "CF-Access-Client-Secret": CF_SECRET,
                  "User-Agent": UA})
        print(f"{que}: {r.read().decode()}")
    except urllib.error.HTTPError as e:
        # El cuerpo dice DE QUIEN es el error, y sin el no se puede distinguir:
        # JSON = llego a la API. Texto plano = lo corto Cloudflare Access.
        cuerpo_err = e.read().decode("utf-8", "replace")
        try:
            json.loads(cuerpo_err)
            quien = "LA API"
        except ValueError:
            quien = "CLOUDFLARE"   # "error code: 1010", HTML, etc.
        raise SystemExit(f"{quien} rechazo {que}: HTTP {e.code}\n{cuerpo_err[:500]}") from e


# ⚠️ LOS TRES ESPACIOS VAN EN UN SOLO ENVIO. El endpoint REEMPLAZA la foto del
# dia entera (DELETE + INSERT), asi que mandarlos por separado haria que el
# segundo BORRE lo que subio el primero — y no fallaria nada, simplemente
# faltarian cuentas. Se juntan aca y se manda una vez.
filas = []
for pc in PARTICIPANTES:
    try:
        parcial = bajar(f"{BYMA}/holdings?balanceDate={HOY}&participantCode={pc}",
                        COLS, f"holdings {pc}")
    except SystemExit as e:
        # Best-effort: si un espacio no existe o falla, los otros igual suben.
        # Perder las liquidadoras es peor que perder todo? No: peor es no subir
        # NADA por una cuenta que quizas ni tenga tenencia hoy.
        print(f"  holdings {pc}: {e}")
        continue
    print(f"  {pc}: {len(parcial)} filas")
    filas.extend(parcial)

print(f"{len(filas)} tenencias de BYMA (los {len(PARTICIPANTES)} espacios)")
if filas:
    mandar("/api/ingest/custodia/holdings", {"fecha": HOY, "docs": filas}, "tenencias")
else:
    # 0 filas NO se manda: una foto vacia no puede borrar la del dia anterior.
    print("0 tenencias: CVSA todavia no armo el dia. No se manda nada.")

# 3. Movimientos del dia. Es ASINCRONO igual que holdings (el portal dice que no,
#    pero hace el mismo baile) y el .csv va ADENTRO del path, con barra final.
#    Techo del metodo: 2/min y 100/dia. No correr este script en loop.
movs = bajar(f"{BYMA}/transactions/today.csv/?participantCode={PARTICIPANT}",
             COLS_MOV, "transactions/today")
print(f"{len(movs)} movimientos de BYMA")
if movs:
    # Aca SI se manda aunque el dia este a medias: son hechos, se acumulan por
    # UPSERT y no borran nada. Al reves que las tenencias.
    mandar("/api/ingest/custodia/movimientos", {"fuente": "today", "docs": movs},
           "movimientos")
else:
    print("0 movimientos: todavia no liquido nada hoy.")
