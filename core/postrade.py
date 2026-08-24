"""core/postrade.py — cliente ÚNICO de la API Postrade (A3 Mercados / ACyRSA).

Es la API de **post-trade** de Argentina Clearing y Registro S.A.: participantes,
cuentas, posiciones, garantías, márgenes, contabilidad y los parámetros de los
contratos negociados en A3 Mercados. De acá van a derivar varias funcionalidades,
así que todo lo que sea "cómo se habla con Postrade" vive en este módulo y en
`core/postrade_catalogo.py` — un caller nuevo no debería tener que saber nada de
tokens, sobres, reintentos ni paths.

Regla de capas: `core/` no importa nada del proyecto salvo `config` (y `postgres`
en diferido, para el token compartido).

═══════════════════════════════════════════════════════════════════════════════
 LO QUE HACE DISTINTO A ESTE CLIENTE — cuatro decisiones, todas con motivo
═══════════════════════════════════════════════════════════════════════════════

**1. ESCRITURA DEFAULT-DENY.** Interbanking era 100% GET: no podía mover plata ni
por error. Postrade **sí puede** — `NewOrderSingle` suscribe y rescata FCI,
`CancelOrder` cancela, `AccountStatus` inactiva una cuenta, `ChangePassword` nos
deja afuera de nuestra propia integración. Por eso escribir requiere DOS cosas a
la vez: `POSTRADE_ESCRITURA=1` en el `.env` **y** que el caller lo pida explícito
con `confirmo_escritura=True`. Un bug, un copy-paste o un parámetro mal armado no
alcanzan para mandar una orden. La marca de lectura/escritura no es disciplina:
es un dato del catálogo (`core/postrade_catalogo.py`).

**2. EL SOBRE MIENTE SOBRE EL STATUS HTTP.** Verificado contra producción: un
rechazo de credenciales llega con **HTTP 200** y el error adentro del cuerpo
(`{"Status":"Unauthorized","Code":"401"}`). Mirar `r.status_code` no alcanza y
nunca alcanzó — `desempaquetar()` valida el sobre, y es la única puerta por la
que salen los datos.

**3. EL TOKEN SE COMPARTE ENTRE PROCESOS.** Dura 24 hs y el Droplet corre la API
más N jobs de cron: si cada proceso pidiera el suyo, un token de 24 horas se
usaría dos segundos y nos loguearíamos decenas de veces por día contra un
proveedor que no publica límite de logins. Se cachea en memoria y, detrás, en
Postgres. Si Postgres no está, funciona igual con memoria (degradación elegante,
mismo criterio que `core/instrumentos_validos`): un problema de base no puede
dejar sin funcionar a la integración.

**4. LOS TIMEOUTS SE REINTENTAN.** No es teoría: en el primer contacto con
producción el endpoint de token cortó por `ReadTimeout`. Un fallo de red no puede
parecer un fallo de permisos.

═══════════════════════════════════════════════════════════════════════════════

Autenticación — en qué se diferencia de los otros clientes del repo: **no es
OAuth**. No hay `client_id`, `client_secret`, `grant_type` ni scopes. Se manda
usuario y contraseña a `/AuthToken/AuthToken` y devuelve un token opaco en el
campo `Value` del sobre. Ese token después viaja en el header `Authorization`.

Env vars (ver config.py): POSTRADE_USUARIO, POSTRADE_PASSWORD y los overrides
POSTRADE_BASE_URL / POSTRADE_AUTH_STYLE / POSTRADE_TOKEN_PREFIJO /
POSTRADE_ESCRITURA.

Uso:
    from core import postrade
    postrade.leer("CurrencyList")
    postrade.leer("ClosingProcesses", {"EntryDate": postrade.fecha_api(hoy)})
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import date, datetime
from typing import Any

import requests

import config
from core.http_base import Throttle
from core.postrade_catalogo import ESCRITURA, LECTURA, metodo

logger = logging.getLogger(__name__)

TOKEN_PATH = "/AuthToken/AuthToken"

# El proveedor no publica límite de llamadas. Sin dato medido (REGLA #2) el
# cliente se auto-limita a 5 req/s: alcanza de sobra para consultas batch y no
# puede ser tomado por abuso.
_throttle = Throttle(0.2)

# Vida del token declarada en el manual. La respuesta NO la informa, así que la
# sabe el cliente y no el servidor — si el proveedor la acortara, nos
# enteraríamos por un 401, que `_request` ya sabe recuperar.
TOKEN_VIDA_S = 24 * 3600
# Margen para que un job largo no se quede sin token a mitad de camino.
_MARGEN_S = 30 * 60

_REINTENTOS = 3

_lock = threading.RLock()
_token: str | None = None
_token_vence: float = 0.0


# --------------------------------------------------------------------------- #
# Errores — tipados, porque arriba se decide distinto según cuál sea
# --------------------------------------------------------------------------- #
class PostradeError(RuntimeError):
    """Cualquier fallo hablando con Postrade."""


class PostradeAuthError(PostradeError):
    """Credenciales rechazadas o token inválido. Es un problema NUESTRO o del
    alta del usuario — se reclama a atencionalcliente@matbarofex.com.ar."""


class PostradeNoHabilitado(PostradeError):
    """El usuario existe y el token es válido, pero no tiene permiso sobre ese
    método. Es OTRO reclamo, distinto del anterior: no se arregla cambiando la
    contraseña ni el código."""


class PostradeEscrituraBloqueada(PostradeError):
    """Se intentó un método que tiene efecto real sin habilitarlo. NO es un
    error del proveedor: es esta librería frenando algo que mueve plata."""


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _base() -> str:
    return (config.POSTRADE_BASE_URL or "").rstrip("/")


def fecha_api(d: date | datetime | str) -> str:
    """La fecha como la quiere Postrade: AAAAMMDD.

    Existe para que ningún caller vuelva a escribir el `strftime`: el formato es
    del proveedor, no de quien lo llama, y una fecha mal puesta **no da error
    ruidoso** — el manual dice que si no se especifica fecha, el método devuelve
    la última información disponible. O sea que el modo de fallar es contestar
    algo plausible y equivocado.
    """
    if isinstance(d, str):
        limpio = d.replace("-", "").replace("/", "").strip()
        if len(limpio) != 8 or not limpio.isdigit():
            raise ValueError(f"fecha inválida para Postrade: {d!r} (se espera AAAAMMDD)")
        return limpio
    return d.strftime("%Y%m%d")


# --------------------------------------------------------------------------- #
# El sobre {Status, Code, Value}
# --------------------------------------------------------------------------- #
_STATUS_AUTH = {"UNAUTHORIZED", "INVALID AUTHORIZATION"}
_STATUS_PERMISO = {"FORBIDDEN"}


def desempaquetar(j: Any, *, contexto: str) -> Any:
    """Valida el sobre `{Status, Code, Value}` y devuelve `Value`.

    ⚠️ VERIFICADO contra producción: un rechazo de credenciales llega con
    **HTTP 200** y el error adentro del sobre. Un cliente que mirara solo el
    status HTTP lo daría por bueno y seguiría con un token vacío.

    El motivo real viaja en `ErrorMessage`/`ErrorDescription` — no en `Value`,
    que en los errores llega `null`. Y `Code` es un **string**, no un número.

    Está separado del transporte para que los diags usen exactamente esta
    validación: si un diag afloja el criterio, deja de estar midiendo lo que
    después corre en producción.
    """
    if not isinstance(j, dict):
        raise PostradeError(f"{contexto}: la respuesta no es un objeto JSON: {str(j)[:200]}")

    status = str(j.get("Status", "")).strip().upper()
    if status == "OK":
        return j.get("Value")

    codigo = str(j.get("Code", "")).strip()
    detalle = " ".join(
        str(j[k]) for k in ("ErrorMessage", "ErrorDescription") if j.get(k)
    ) or str(j.get("Value"))
    msg = f"{contexto}: Status={j.get('Status')!r} Code={codigo!r} → {detalle[:300]}"

    if status in _STATUS_AUTH or codigo == "401":
        raise PostradeAuthError(msg)
    if status in _STATUS_PERMISO or codigo == "403":
        raise PostradeNoHabilitado(msg)
    raise PostradeError(msg)


# --------------------------------------------------------------------------- #
# Token — memoria → Postgres → API
# --------------------------------------------------------------------------- #
def _pedir_token_a_la_api() -> str:
    """POST a `/AuthToken/AuthToken`. Devuelve el token opaco.

    Las credenciales admiten body JSON o querystring: el manual dice que las dos
    valen y aceptan los MISMOS dos campos. El default es **body** — la
    querystring deja la contraseña escrita en la URL, y una URL termina en los
    logs de cualquier proxy que haya en el camino. (Medido además: el camino por
    querystring se colgó con ReadTimeout mientras el body contestó al instante.)
    """
    usuario = (config.POSTRADE_USUARIO or "").strip()
    password = config.POSTRADE_PASSWORD or ""
    if not usuario or not password:
        raise PostradeAuthError("Faltan POSTRADE_USUARIO / POSTRADE_PASSWORD en el .env")

    url = f"{_base()}{TOKEN_PATH}"
    credenciales = {"nombreUsuario": usuario, "password": password}
    por_query = (config.POSTRADE_AUTH_STYLE or "body").strip().lower() == "query"

    ultimo = ""
    for intento in range(_REINTENTOS):
        _throttle.wait()
        try:
            if por_query:
                r = requests.post(
                    url, params=credenciales, headers={"Accept": "application/json"}, timeout=60
                )
            else:
                r = requests.post(
                    url,
                    json=credenciales,
                    headers={"Accept": "application/json", "Content-Type": "application/json"},
                    timeout=60,
                )
        except requests.RequestException as e:
            ultimo = f"{type(e).__name__}: {e}"
            if intento + 1 < _REINTENTOS:
                time.sleep(3 * (intento + 1))
                continue
            raise PostradeError(f"red pidiendo token: {ultimo}") from e

        if r.status_code >= 500 and intento + 1 < _REINTENTOS:
            ultimo = f"HTTP {r.status_code}"
            time.sleep(3 * (intento + 1))
            continue

        if r.status_code == 401:
            raise PostradeAuthError(f"token HTTP 401: {r.text[:300]}")
        if r.status_code != 200:
            raise PostradeError(
                f"token HTTP {r.status_code}: {r.text[:300]} "
                f"(url={url}, style={'query' if por_query else 'body'})"
            )

        try:
            j = r.json()
        except ValueError as e:
            raise PostradeError(f"token: respuesta no-JSON: {r.text[:200]}") from e

        valor = desempaquetar(j, contexto="token")
        if not isinstance(valor, str) or not valor.strip():
            raise PostradeError(f"token: el campo Value no trae un token: {str(valor)[:200]}")
        return valor.strip()

    raise PostradeError(f"token: agotados {_REINTENTOS} intentos ({ultimo})")


def _leer_token_compartido() -> tuple[str, float] | None:
    """El token que dejó otro proceso, si sigue vigente. `None` si no se puede."""
    try:
        from core.postgres import get_pool
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT token, extract(epoch FROM vence_at) "
                "FROM manager.postrade_token WHERE id = 1 AND vence_at > now()"
            )
            fila = cur.fetchone()
    except Exception as e:  # degradación a propósito: sin base, se usa memoria
        logger.debug("Postrade: no pude leer el token compartido (%s)", e)
        return None
    if not fila or not fila[0]:
        return None
    return str(fila[0]), float(fila[1])


def _guardar_token_compartido(tok: str, vence: float) -> None:
    """Deja el token para los demás procesos. Si falla, no pasa nada: el token
    ya está en memoria y esta corrida funciona igual."""
    try:
        from core.postgres import get_pool
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO manager.postrade_token (id, token, vence_at, actualizado_at) "
                "VALUES (1, %s, to_timestamp(%s), now()) "
                "ON CONFLICT (id) DO UPDATE SET token = EXCLUDED.token, "
                "  vence_at = EXCLUDED.vence_at, actualizado_at = now()",
                (tok, vence),
            )
    except Exception as e:  # degradación a propósito: sin base, se usa memoria
        logger.debug("Postrade: no pude guardar el token compartido (%s)", e)


def token(*, force_refresh: bool = False) -> str:
    """El token vigente. Memoria → Postgres → API, en ese orden.

    Dos procesos que arranquen a la vez pueden pedir dos tokens: el UPSERT hace
    ganar al último y los dos son utilizables. No se pone un lock distribuido a
    propósito — el caso es raro, el costo es un login de más, y si el proveedor
    llegara a invalidar el anterior, `_request` re-autentica ante el 401. Un
    lock distribuido mal hecho sería peor que el problema que resuelve.
    """
    global _token, _token_vence
    with _lock:
        ahora = time.time()
        if not force_refresh and _token and ahora < _token_vence - _MARGEN_S:
            return _token

        if not force_refresh:
            compartido = _leer_token_compartido()
            if compartido and compartido[1] - _MARGEN_S > ahora:
                _token, _token_vence = compartido
                logger.debug("Postrade: token tomado del compartido")
                return _token

        _token = _pedir_token_a_la_api()
        _token_vence = time.time() + TOKEN_VIDA_S
        _guardar_token_compartido(_token, _token_vence)
        logger.info("Postrade: token nuevo (vida declarada %sh)", TOKEN_VIDA_S // 3600)
        return _token


def auth_headers(*, force_refresh: bool = False) -> dict[str, str]:
    """El header `Authorization` con el token, más el Accept.

    El prefijo sale del `.env` porque el manual **no lo escribe**: dice
    «incluir el header Authorization el token obtenido» y lo ilustra con una
    captura de pantalla. De una imagen no se infiere si va crudo o con
    `Bearer `, así que se mide (`scripts/diag_postrade_auth`) y se configura.
    """
    prefijo = config.POSTRADE_TOKEN_PREFIJO or ""
    return {
        "Authorization": f"{prefijo}{token(force_refresh=force_refresh)}",
        "Accept": "application/json",
    }


def reset_token() -> None:
    """Olvida el token cacheado en memoria (no el compartido). Para diags/tests."""
    global _token, _token_vence
    with _lock:
        _token, _token_vence = None, 0.0


# --------------------------------------------------------------------------- #
# Transporte
# --------------------------------------------------------------------------- #
def _request(verbo: str, path: str, *, params=None, json_body=None) -> Any:
    """El único lugar que habla HTTP con Postrade. Devuelve `Value` desempaquetado.

    Reintenta: red y 5xx con backoff, y **una vez con token nuevo** ante 401/403.
    Ese reintento importa porque el token dura 24 hs pero el servidor puede
    invalidarlo antes (cambio de contraseña, corte de sesión) — sin él, un token
    muerto en la tabla compartida rompería a todos los procesos hasta las 24 hs.
    """
    url = f"{_base()}/{path.lstrip('/')}"
    p = {k: v for k, v in (params or {}).items() if v is not None}
    ultimo = ""
    reintentado_auth = False

    for intento in range(_REINTENTOS):
        _throttle.wait()
        try:
            r = requests.request(
                verbo,
                url,
                params=p or None,
                json=json_body,
                headers=auth_headers(force_refresh=reintentado_auth),
                timeout=120,
            )
        except requests.RequestException as e:
            ultimo = f"{type(e).__name__}: {e}"
            if intento + 1 < _REINTENTOS:
                time.sleep(3 * (intento + 1))
                continue
            raise PostradeError(f"red en {path}: {ultimo}") from e

        if r.status_code in (401, 403) and not reintentado_auth:
            logger.warning("Postrade %s en %s; reintento con token nuevo", r.status_code, path)
            reintentado_auth = True
            continue

        if r.status_code >= 500 and intento + 1 < _REINTENTOS:
            ultimo = f"HTTP {r.status_code}"
            time.sleep(3 * (intento + 1))
            continue

        if r.status_code == 401:
            raise PostradeAuthError(f"{path} HTTP 401: {r.text[:300]}")
        if r.status_code == 403:
            raise PostradeNoHabilitado(f"{path} HTTP 403: {r.text[:300]}")
        if r.status_code != 200:
            raise PostradeError(f"{path} HTTP {r.status_code}: {r.text[:400]}")

        try:
            j = r.json()
        except ValueError as e:
            raise PostradeError(f"{path}: respuesta no-JSON: {r.text[:200]}") from e

        try:
            return desempaquetar(j, contexto=path)
        except PostradeAuthError:
            # El sobre también puede decir 401 con HTTP 200. Mismo trato.
            if reintentado_auth:
                raise
            logger.warning("Postrade: el sobre de %s dice 401; reintento con token nuevo", path)
            reintentado_auth = True
            continue

    raise PostradeError(f"{path}: agotados {_REINTENTOS} intentos ({ultimo})")


# --------------------------------------------------------------------------- #
# API pública — leer y (con doble llave) escribir
# --------------------------------------------------------------------------- #
def leer(nombre: str, params: dict[str, Any] | None = None) -> Any:
    """Llama a un método de LECTURA del catálogo. Devuelve el `Value` del sobre.

    El path sale de `core/postrade_catalogo.py`: el caller nombra el método, no
    arma la URL. Un typo revienta acá con la lista de métodos válidos, en vez de
    llegar a la API como un path inexistente que contesta "no autorizado" y nos
    manda a reclamarle al proveedor algo que es nuestro.
    """
    m = metodo(nombre)
    if m.verbo != LECTURA:
        raise PostradeEscrituraBloqueada(
            f"{nombre} es un método de ESCRITURA ({m.que_trae}). "
            f"Para llamarlo, usá escribir() — y leé por qué está trabado."
        )
    faltan = [k for k in m.obligatorios if not (params or {}).get(k)]
    if faltan:
        raise ValueError(f"{nombre}: faltan parámetros obligatorios: {', '.join(faltan)}")
    return _request("GET", m.path, params=params)


def escribir(nombre: str, cuerpo: Any, *, confirmo_escritura: bool = False) -> Any:
    """Llama a un método de ESCRITURA. ⚠️ TIENE EFECTO REAL DEL OTRO LADO.

    Pide DOS llaves independientes, y las dos a la vez:

    1. `POSTRADE_ESCRITURA=1` en el `.env` — decisión de despliegue, la toma
       quien opera el servidor.
    2. `confirmo_escritura=True` en la llamada — decisión de código, la toma
       quien escribe el caller.

    Son dos porque protegen de cosas distintas: la primera evita que un deploy
    con código nuevo empiece a operar sin que nadie lo haya decidido; la segunda
    evita que una función que creía estar leyendo termine mandando una orden.
    Ninguna de las dos sola alcanzaría.
    """
    m = metodo(nombre)
    if m.verbo != ESCRITURA:
        raise ValueError(f"{nombre} es de lectura — se llama con leer()")
    if not confirmo_escritura:
        raise PostradeEscrituraBloqueada(
            f"{nombre} {m.que_trae}. Falta confirmo_escritura=True en la llamada."
        )
    if not config.POSTRADE_ESCRITURA:
        raise PostradeEscrituraBloqueada(
            f"{nombre} {m.que_trae}. La escritura contra Postrade está DESHABILITADA: "
            f"para permitirla hay que poner POSTRADE_ESCRITURA=1 en el .env del Droplet."
        )
    logger.warning("Postrade ESCRITURA: %s (%s)", nombre, m.que_trae)
    return _request("POST", m.path, json_body=cuerpo)


# --------------------------------------------------------------------------- #
# Prueba de vida
# --------------------------------------------------------------------------- #
def ping() -> Any:
    """¿El acceso funciona? Devuelve el `Value` de `CurrencyList`.

    Se eligió la lista de monedas a propósito: es un referencial, no depende de
    fechas ni de que tengamos cuentas o posiciones cargadas, y por lo tanto
    separa "el acceso anda" de "todavía no hay datos nuestros" — que son dos
    respuestas distintas y se arreglan de formas distintas.
    """
    return leer("CurrencyList")
