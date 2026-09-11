"""core/byma_custodia.py — cliente único de las APIs de CUSTODIA de BYMA/CVSA.

Qué es: la Caja de Valores (CVSA) publica lo que ella tiene REGISTRADO a nombre
nuestro. No es lo mismo que Aunesa —que es el back-office tercerizado— y esa es
justamente la gracia: cuando las dos difieren, la que tiene razón legal es la
Caja. Ver `core/duplicados.py` para el patrón de "dos copias, un árbitro".

Centraliza `client_credentials` (token cacheado POR SCOPE) y los dos modos de
consulta que tiene el gateway. Mismo patrón que `core/interbanking.py` y
`core/aunesa.py`. Regla de capas: `core/` no importa nada del proyecto salvo
`config`.

Endpoints cubiertos (TODOS DE LECTURA):
    GET /holdings            tenencia de todo el agente a una fecha   ASÍNCRONO
    GET /holdings/accounts   tenencia de UNA cuenta                   inmediato
    GET /transactions        liquidaciones de una fecha               ASÍNCRONO
    GET /transactions/today  las de hoy                               inmediato

⛔ `POST /transactionsbyreference` NO ESTÁ ACÁ, A PROPÓSITO. El OpenAPI lo
   presenta como una consulta más; la ficha del portal dice: *"Este método
   ejecuta una tarea de ESCRITURA... El origen expuesto por medio del método se
   verá afectado con cada solicitud realizada"*. Un cliente de lectura no expone
   un método que escribe. Si algún día hace falta, va aparte y con la doble
   llave que lleva todo lo que opera (patrón `POSTRADE_ESCRITURA`).

⚠️ CINCO TRAMPAS, ninguna inferible del OpenAPI. Las cinco medidas contra
   producción el 2026-09-11; el spec publicado no menciona NINGUNA:

1. **`Accept: application/json` da 406.** Cada método elige su formato y el
   gateway no negocia: `/holdings` contesta CSV y `/holdings/accounts` JSON.
   Se manda `Accept: */*` y se parsea lo que venga (`_parsear`).

2. **Los métodos asincrónicos contestan 409, no 200.** La primera llamada
   dispara el trabajo y devuelve `{"code": 409, "uuid": "..."}`; hay que
   repetirla con la cabecera `X-UUID` hasta que conteste los datos. Acá vive
   adentro de `_get_async`, con tope de intentos y timeout: un poll sin techo
   es un job colgado para siempre esperando un uuid que no resuelve.

3. **`meta.count` MIENTE.** Medido: dijo `3` sobre un `result` de CUATRO filas.
   Nadie puede usarlo para paginar ni para validar. Se cuenta `len(result)`, y
   si difieren se avisa una vez — el día que BYMA lo arregle queremos saberlo,
   porque hoy estamos ignorando un campo a propósito.

4. **La respuesta JSON viene ENVUELTA** en `{"meta": {...}, "result": [...]}`.
   El OpenAPI declara un objeto plano.

5. **El CSV se separa con `;`**, no con coma, y trae un campo que no está
   documentado en ningún lado (`identAccountComposite`).

Identidad (REGLA #9 — la identidad no es el nombre):
  · `accountNumber` viene `"74/805"` = `participantCode/id_cuenta`. El `805` es
    nuestro `clientes.cuentas.id_cuenta` tal cual. Se parte por `/`, NO se
    interpreta el string entero.
  · `cvsaIdentifier` es un NÚMERO interno de CVSA (`5921`, `9422`), sin ninguna
    relación con nuestros tickers. El puente vive en `portafolio.assets.
    codigo_cnv` (que pese al nombre guarda el código de CAJA) y se carga desde
    el maestro de especies. **Nunca parear por nombre.**

Env vars (ver config.py): BYMA_CLIENT_ID, BYMA_CLIENT_SECRET,
BYMA_PARTICIPANT_CODE.
"""
from __future__ import annotations

import csv
import io
import logging
import threading
import time
from typing import Any

import requests

import config

logger = logging.getLogger(__name__)

TOKEN_URL = "https://api.byma.com.ar/oauth/token/"
BASE_SECURITIES = "https://api.byma.com.ar/custody-securities/v1"
BASE_FEES = "https://api.byma.com.ar/custody-fees/v2"

SCOPE_SECURITIES = "custodysecurities.read"
SCOPE_FEES = "custodyfees.read"

TIMEOUT_S = 60          # el SLO del proveedor dice 1500ms; el techo suyo es 60s
LOGIN_TIMEOUT_S = 20
# El token dura 86400s (24h, medido). Se renueva con 10 minutos de margen: no
# vale la pena afinar más, y un job largo no puede quedarse con uno por vencer.
MARGEN_RENOVACION_S = 600

# El baile del X-UUID. Medido: la primera respuesta llegó en ~100ms y los datos
# (92 KB, ~2.300 filas) estuvieron listos en el primer reintento. Estos números
# dan ~90s de espera total con backoff, y después se rinde CON ERROR — que es lo
# correcto: un job que espera para siempre no falla nunca y nadie se entera.
ASYNC_INTENTOS = 12
ASYNC_ESPERA_INICIAL_S = 2.0
ASYNC_ESPERA_MAX_S = 15.0

# Las columnas del CSV, en orden. El gateway NO manda cabecera de forma
# confiable, así que el orden es el contrato. Sale del diccionario de datos del
# portal; `identAccountComposite` se descubrió mirando la respuesta real.
COLUMNAS_HOLDINGS = ("participantCode", "accountNumber", "cvsaIdentifier",
                     "subBalanceType", "holding")
COLUMNAS_TRANSACTIONS = ("participantCode", "settlementDate", "accountNumber",
                         "cvsaIdentifier", "securitiesSubBalanceType", "volume",
                         "amount", "currency", "instructionReference")

_lock = threading.Lock()
_tokens: dict[str, tuple[str, float]] = {}      # scope → (token, vence_en)


class BymaCaido(RuntimeError):
    """BYMA no contesta, o contesta un 5xx. Es SU servidor, no el nuestro.

    Existe por lo mismo que `AunesaCaido`: sin un tipo propio, una caída ajena y
    un bug nuestro se ven igual en pantalla (un `HTTPError` crudo), y el back
    office termina reportando "la app tira 500" cuando el 500 lo devuelve el
    proveedor.
    """


class BymaError(RuntimeError):
    """Fallo hablando con BYMA que NO es una caída de ellos (4xx, payload raro).

    Separado de `BymaCaido` a propósito: un 401 o un 400 es problema nuestro
    —credenciales, parámetros— y reintentarlo no lo arregla.
    """


# --------------------------------------------------------------------------- #
# Auth
# --------------------------------------------------------------------------- #
def _pedir_token(scope: str) -> tuple[str, int]:
    """POST /oauth/token con `client_credentials`. Devuelve (token, segundos).

    NO reintenta ante 4xx: un 400/401 es credencial o scope nuestro, y
    machacarle el login al proveedor con lo mismo no lo arregla — puede
    bloquear la aplicación. Es la lección de `core/aunesa.py`.
    """
    faltan = [n for n, v in (("BYMA_CLIENT_ID", config.BYMA_CLIENT_ID),
                             ("BYMA_CLIENT_SECRET", config.BYMA_CLIENT_SECRET)) if not v]
    if faltan:
        raise BymaError(f"faltan credenciales de BYMA en el .env: {', '.join(faltan)}")

    try:
        r = requests.post(
            TOKEN_URL,
            data={"client_id": config.BYMA_CLIENT_ID,
                  "client_secret": config.BYMA_CLIENT_SECRET,
                  "grant_type": "client_credentials",
                  "scope": scope},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=LOGIN_TIMEOUT_S,
        )
    except requests.RequestException as e:
        raise BymaCaido(f"red pidiendo el token a BYMA: {e}") from e

    if r.status_code >= 500:
        raise BymaCaido(f"BYMA devolvió HTTP {r.status_code} pidiendo el token. "
                        "Es su servidor, no la app.")
    if r.status_code != 200:
        # El gateway envuelve el error de su IdP: contesta 400 con un cuerpo que
        # adentro dice 401. El texto crudo es lo único que sirve para saber qué
        # pasó, así que se propaga entero.
        raise BymaError(f"token HTTP {r.status_code}: {r.text[:300]} "
                        f"(scope={scope}). Revisar client_id/secret y que la "
                        "aplicación tenga OTORGADO ese scope en el portal.")

    try:
        j = r.json()
    except ValueError as e:
        raise BymaError(f"token: respuesta no-JSON: {r.text[:200]}") from e

    tok = j.get("access_token")
    if not tok:
        raise BymaError(f"token: la respuesta no trae access_token: {j}")

    # El scope que DEVUELVE puede no ser el que se pidió. Un 200 no garantiza
    # que tengamos el permiso que pedimos, así que si difiere se canta.
    devuelto = (j.get("scope") or "").strip()
    if devuelto and devuelto != scope:
        logger.warning("BYMA: pedimos scope %r y devolvió %r", scope, devuelto)

    return tok, int(j.get("expires_in") or 3600)


def token(scope: str = SCOPE_SECURITIES, *, force_refresh: bool = False) -> str:
    """Access token cacheado POR SCOPE.

    Por scope y no uno solo porque son permisos distintos: el de Securities no
    abre Fees. Si algún día el proveedor acepta pedir los dos juntos, se cachea
    esa combinación bajo su propia clave sin cambiar nada de esto.
    """
    with _lock:
        vigente = _tokens.get(scope)
        if not force_refresh and vigente and time.time() < vigente[1] - MARGEN_RENOVACION_S:
            return vigente[0]
        tok, dura = _pedir_token(scope)
        _tokens[scope] = (tok, time.time() + dura)
        logger.info("BYMA: token nuevo para %s, vence en %ss", scope, dura)
        return tok


def _headers(scope: str, *, force_refresh: bool = False, uuid: str | None = None) -> dict[str, str]:
    h = {
        "Authorization": f"Bearer {token(scope, force_refresh=force_refresh)}",
        # ⚠️ `application/json` da 406: cada método elige su formato y el gateway
        # no negocia. Ver trampa 1 del encabezado.
        "Accept": "*/*",
        "User-Agent": "AcaQuant/1.0",
    }
    if uuid:
        h["X-UUID"] = uuid
    return h


# --------------------------------------------------------------------------- #
# Parseo — el gateway contesta JSON o CSV según el método
# --------------------------------------------------------------------------- #
def _parsear(resp: requests.Response, columnas: tuple[str, ...]) -> list[dict[str, str]]:
    """Normaliza la respuesta a `list[dict]`, venga JSON o CSV.

    `columnas` es el orden del CSV, que es el contrato cuando no hay cabecera.
    """
    ctype = (resp.headers.get("Content-Type") or "").lower()

    if "json" in ctype:
        try:
            j = resp.json()
        except ValueError as e:
            raise BymaError(f"respuesta declarada JSON que no parsea: {resp.text[:200]}") from e
        # Viene envuelta: {"meta": {...}, "result": [...]}. Se tolera también el
        # array pelado por si algún método lo devuelve así.
        filas = j.get("result", j) if isinstance(j, dict) else j
        if not isinstance(filas, list):
            raise BymaError(f"se esperaba una lista y vino {type(filas).__name__}: {str(j)[:200]}")
        _avisar_count_mentiroso(j, len(filas))
        return filas

    # CSV. Separador `;` (medido). La cabecera no está garantizada, así que se
    # detecta: si la primera celda es un nombre de columna conocido, se descarta.
    texto = resp.text
    filas_csv = list(csv.reader(io.StringIO(texto), delimiter=";"))
    filas_csv = [f for f in filas_csv if f and any(c.strip() for c in f)]
    if filas_csv and filas_csv[0][0].strip() in columnas:
        filas_csv = filas_csv[1:]
    return [dict(zip(columnas, f, strict=False)) for f in filas_csv]


def _avisar_count_mentiroso(j: Any, reales: int) -> None:
    """`meta.count` no coincide con las filas. Se avisa, no se usa.

    Medido: `count: 3` sobre un `result` de 4. No se puede paginar ni validar
    con ese número. Queda el log para enterarnos si alguna vez lo arreglan.
    """
    if not isinstance(j, dict):
        return
    declarado = (j.get("meta") or {}).get("count")
    if declarado is not None and int(declarado) != reales:
        logger.warning("BYMA: meta.count dice %s y vinieron %s filas (se usa len)",
                       declarado, reales)


# --------------------------------------------------------------------------- #
# GET — los dos modos
# --------------------------------------------------------------------------- #
def _pedir(url: str, params: dict[str, Any], scope: str,
           *, uuid: str | None = None, force_refresh: bool = False) -> requests.Response:
    try:
        r = requests.get(url, params=params,
                         headers=_headers(scope, force_refresh=force_refresh, uuid=uuid),
                         timeout=TIMEOUT_S)
    except requests.RequestException as e:
        raise BymaCaido(f"red hablando con BYMA ({url}): {e}") from e
    if r.status_code >= 500:
        raise BymaCaido(f"BYMA devolvió HTTP {r.status_code} en {url}. "
                        "Es su servidor, no la app.")
    return r


def _get(path: str, params: dict[str, Any], *, columnas: tuple[str, ...],
         scope: str = SCOPE_SECURITIES, base: str = BASE_SECURITIES) -> list[dict[str, str]]:
    """GET inmediato, con re-auth ante 401 (el token pudo vencer antes de tiempo)."""
    url = f"{base}/{path.lstrip('/')}"
    p = {k: v for k, v in params.items() if v is not None and v != ""}

    for intento in (0, 1):
        r = _pedir(url, p, scope, force_refresh=bool(intento))
        if r.status_code == 401 and intento == 0:
            logger.warning("BYMA 401 en %s; reintento con token nuevo", path)
            continue
        if r.status_code != 200:
            raise BymaError(f"{path} HTTP {r.status_code}: {r.text[:400]}")
        return _parsear(r, columnas)

    raise BymaError(f"{path}: 401 incluso con token nuevo")


def _get_async(path: str, params: dict[str, Any], *, columnas: tuple[str, ...],
               scope: str = SCOPE_SECURITIES,
               base: str = BASE_SECURITIES) -> list[dict[str, str]]:
    """GET de los métodos ASÍNCRONOS: dispara, y repregunta con `X-UUID`.

    La primera llamada devuelve `409` con un `uuid`; la misma llamada repetida
    con la cabecera `X-UUID` devuelve `409` de nuevo mientras el trabajo corre, y
    `200` con los datos cuando termina.

    Tiene tope de intentos y backoff A PROPÓSITO: sin techo, un uuid que nunca
    resuelve deja el job colgado para siempre, y un job colgado no falla —
    simplemente no termina, y nadie se entera.
    """
    url = f"{base}/{path.lstrip('/')}"
    p = {k: v for k, v in params.items() if v is not None and v != ""}

    r = _pedir(url, p, scope)
    if r.status_code == 401:
        r = _pedir(url, p, scope, force_refresh=True)
    if r.status_code == 200:
        return _parsear(r, columnas)          # contestó de una: no hubo trabajo que esperar
    if r.status_code not in (200, 409):
        raise BymaError(f"{path} HTTP {r.status_code}: {r.text[:400]}")

    try:
        uuid = r.json().get("uuid")
    except ValueError:
        uuid = None
    if not uuid:
        raise BymaError(f"{path}: contestó 409 sin uuid, no hay cómo seguir: {r.text[:300]}")

    espera = ASYNC_ESPERA_INICIAL_S
    for intento in range(1, ASYNC_INTENTOS + 1):
        time.sleep(espera)
        espera = min(espera * 1.5, ASYNC_ESPERA_MAX_S)
        r = _pedir(url, p, scope, uuid=uuid)
        if r.status_code == 200:
            logger.info("BYMA %s: listo en el intento %d (uuid %s)", path, intento, uuid)
            return _parsear(r, columnas)
        if r.status_code != 409:
            raise BymaError(f"{path} HTTP {r.status_code} esperando {uuid}: {r.text[:400]}")

    raise BymaCaido(f"{path}: el trabajo {uuid} no terminó tras {ASYNC_INTENTOS} intentos. "
                    "No se puede seguir esperando sin techo.")


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #
def _participante(participant_code: str | None) -> str:
    pc = (participant_code or config.BYMA_PARTICIPANT_CODE or "").strip()
    if not pc:
        raise BymaError("falta BYMA_PARTICIPANT_CODE en el .env (nuestro código "
                        "de participante en CVSA). Sin eso ningún método contesta.")
    return pc


def holdings(balance_date: str, *, participant_code: str | None = None) -> list[dict[str, str]]:
    """Tenencia de TODAS las cuentas del agente a una fecha. ASÍNCRONO.

    `balance_date` en YYYY-MM-DD y **dentro de los últimos 7 días**: más atrás
    CVSA ya lo purgó de su base. Por eso no se puede reconstruir histórico a
    pedido — el que lo quiera tiene que guardarlo día a día.
    """
    return _get_async("holdings",
                      {"balanceDate": balance_date,
                       "participantCode": _participante(participant_code)},
                      columnas=COLUMNAS_HOLDINGS)


def holdings_cuenta(account_number: str, *, participant_code: str | None = None,
                    sub_balance_type: str | None = None) -> list[dict[str, str]]:
    """Tenencia de UNA cuenta. Inmediato.

    `account_number` va en el formato de CVSA: `"74/805"`. `sub_balance_type`
    filtra por estado (AVAILABLE, EMBARGO, BLOCKED_FOR_PLEDGE, …); vacío trae
    todos, que es lo que interesa: saber qué parte de la tenencia está trabada
    es información que Aunesa no nos da.
    """
    return _get("holdings/accounts",
                {"accountNumber": account_number,
                 "participantCode": _participante(participant_code),
                 "subBalanceType": sub_balance_type},
                columnas=COLUMNAS_HOLDINGS)


def transactions(settlement_date: str, *,
                 participant_code: str | None = None) -> list[dict[str, str]]:
    """Liquidaciones de una fecha, con su `instructionReference`. ASÍNCRONO."""
    return _get_async("transactions",
                      {"settlementDate": settlement_date,
                       "participantCode": _participante(participant_code)},
                      columnas=COLUMNAS_TRANSACTIONS)


def transactions_today(*, participant_code: str | None = None) -> list[dict[str, str]]:
    """Liquidaciones de hoy. Inmediato.

    ⚠️ El techo más bajo de toda la API: **2 requests por minuto y 100 por día**
    (lo declara el portal). No sirve para una pantalla live; como mucho un job
    cada varios minutos. Quien lo llame tiene que saberlo.
    """
    return _get("transactions/today",
                {"participantCode": _participante(participant_code)},
                columnas=COLUMNAS_TRANSACTIONS)


# --------------------------------------------------------------------------- #
# Ayudas de identidad (REGLA #9)
# --------------------------------------------------------------------------- #
def id_cuenta(account_number: str) -> str | None:
    """`"74/805"` → `"805"`, que es nuestro `clientes.cuentas.id_cuenta`.

    Existe para que nadie parsee el string a mano en cada caller: la forma del
    `accountNumber` es un detalle de CVSA y tiene que vivir en un solo lugar.
    Devuelve None si no tiene la forma esperada — un formato distinto es algo
    que hay que mirar, no algo que se adivina.
    """
    partes = (account_number or "").split("/")
    if len(partes) != 2 or not partes[1].strip():
        return None
    return partes[1].strip()
