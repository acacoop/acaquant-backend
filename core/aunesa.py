"""core/aunesa.py — cliente único de la API del custodio Aunesa.

Centraliza la autenticación (login → Bearer token cacheado, con re-auth ante
401) y un GET genérico con retry de timeout. Reemplaza las copias de
`_autenticar` / `_auth` que estaban dispersas en jobs / services / scripts.

SU LOGIN SE CAE (2026-08-07/09 y 2026-08-20: HTTP 500 en `POST /login`, sin que
nada nuestro cambiara). Por eso el login reintenta ante 5xx / corte de red, NO
reintenta ante 4xx (eso es credencial nuestra y machacarlo bloquea la cuenta), y
detrás hay un CORTACIRCUITO (`FALLO_TTL_S`) para no pagar la tanda de reintentos
en cada poll de la vista. Cuando el custodio es el que está roto, el error que
sube es `AunesaCaido` — un tipo propio, para que quien lo atrape pueda decir
"es de ellos" en vez de mostrar un `HTTPError` crudo que se lee como bug nuestro.
Diagnóstico: `python -m scripts.diag_aunesa`.

Regla de capas: `core/` no importa nada del proyecto salvo `config`.

Uso:
    from core import aunesa
    headers = aunesa.auth_headers()                       # para callers simples
    resp = aunesa.get("operaciones/informes", {"cuenta": "805", ...})

Endpoints conocidos (path relativo a BASE_URL):
    cuentas/listadoCuentas
    cuentas/{id}/posicionValuada
    operaciones/consolidadosGenerales
    operaciones/informes
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any

import requests

import config
from core import proveedores

logger = logging.getLogger(__name__)

BASE_URL = "https://aca.aunesa.com/Irmo/api"
AUTH_URL = f"{BASE_URL}/login"

# El login de Aunesa se cae solo: HTTP 500 dos días seguidos (2026-08-07/09) y otra
# vez el 2026-08-20, siempre en `POST /login`. Tres constantes gobiernan la respuesta.
LOGIN_TIMEOUT_S = 15
LOGIN_INTENTOS = 3            # 5xx / corte de red: se reintenta. 4xx NO (ver `_login`).
LOGIN_ESPERAS_S = (1.0, 3.0)  # backoff entre intento 1→2 y 2→3
# CORTACIRCUITO: cuánto NO se vuelve a pedir un token después de un login fallido.
# Sin esto, con Aunesa caído CADA poll de la vista (20s) y CADA usuario pagaban la
# tanda entera de reintentos — y como el login se hace con `_lock` tomado, los polls
# se encolaban y la Tesorería quedaba lenta ADEMÁS de incompleta. Es más corto que el
# poll del front, así que la recuperación se nota en el primer refresh.
FALLO_TTL_S = 45

_lock = threading.Lock()
_token: str | None = None
# (monotonic del fallo, mensaje) mientras el cortacircuito está abierto.
_fallo: tuple[float, str] | None = None


class AunesaCaido(RuntimeError):
    """El custodio no contesta. Es SU servidor, no el nuestro.

    Existe para que el que la atrapa pueda DECIRLO: una caída de Aunesa y un bug
    nuestro se veían igual (un `HTTPError` crudo en pantalla), y el back office
    terminaba reportando "Tesorería tira 500" cuando el 500 lo devolvía Aunesa.
    """


def _login() -> str:
    """POST /login con las credenciales de config. Devuelve el token.

    Reintenta ante 5xx y ante corte de red, NO ante 4xx: un 400/401/403 es un
    problema NUESTRO (credenciales o payload) y machacarles el login con las
    mismas credenciales no lo arregla — puede bloquear la cuenta.

    Cada intento deja constancia de cómo contestó (`core/proveedores`). **Acá es
    donde se entera el agente de que Aunesa se cayó**, sin una sola llamada extra:
    los daemons le pegan todo el tiempo, así que un 500 en el login queda anotado
    en segundos y con el mensaje exacto. `anotar` se auto-limita a una escritura
    por minuto y nunca levanta, así que llamarlo por intento es gratis. Ver
    AV_AGENT.md §0.ad.
    """
    # ⚠️⚠️ **`AUNESA_CLIENT_ID` VA VACÍO Y SIEMPRE FUE ASÍ** (user, 2026-08-20).
    #
    # Exigirlo acá dejó a Aunesa muerto de NUESTRO lado: el login cortaba antes de
    # tocar la red y todo lo que depende del custodio —Tesorería, los saldos
    # liquidados, la tenencia del día, los informes de operaciones— se caía con
    # «faltan credenciales», que además apuntaba al lugar equivocado.
    #
    # Y el modo de falla es el peor: **no fallaba nada nuevo**. Aunesa ya estaba
    # devolviendo 500, así que la vista ya decía CAÍDO; el cambio solo reemplazó
    # una causa ajena por una propia sin que se notara la diferencia.
    #
    # La lección, que vale para cualquier credencial: **una validación de config
    # que nunca se probó contra la config REAL es una hipótesis, no una guarda**
    # (REGLA #2). Que un campo se llame `clientId` no significa que este proveedor
    # lo pida — y meses de logins exitosos con el campo vacío son la medición que
    # cualquier razonamiento sobre el nombre tenía que respetar.
    faltan = [n for n, v in (("AUNESA_USERNAME", config.AUNESA_USERNAME),
                             ("AUNESA_PASSWORD", config.AUNESA_PASSWORD)) if not v]
    if faltan:
        # Se corta ANTES de la red: sin credenciales no hay nada que probar, y el
        # error tiene que nombrar lo que falta en vez de disfrazarse de caída ajena.
        # Igual se anota: para el agente, "no tenemos la clave" y "se cayó el
        # proveedor" son dos avisos distintos y los dos hay que darlos.
        msg = f"faltan credenciales de Aunesa en el .env: {', '.join(faltan)}"
        proveedores.anotar("aunesa", ok=False, donde="login", error=msg)
        raise RuntimeError(msg)

    ultimo = ""
    for intento in range(1, LOGIN_INTENTOS + 1):
        try:
            resp = requests.post(
                AUTH_URL,
                json={
                    "clientId": config.AUNESA_CLIENT_ID,
                    "username": config.AUNESA_USERNAME,
                    "password": config.AUNESA_PASSWORD,
                },
                headers={"Content-Type": "application/json"},
                timeout=LOGIN_TIMEOUT_S,
            )
        except requests.exceptions.RequestException as e:
            ultimo = f"no pude conectarme ({type(e).__name__}: {e})"
        else:
            if resp.status_code < 400:
                try:
                    tok = (resp.json() or {}).get("token")
                except ValueError:
                    tok = None
                if tok:
                    proveedores.anotar("aunesa", ok=True, donde="login")
                    return tok
                # 200 sin token = gateway degradado; se reintenta como un 5xx.
                ultimo = f"HTTP {resp.status_code} pero sin token"
            elif resp.status_code < 500:
                # Credencial nuestra: no se reintenta, pero SÍ se anota — sin el
                # error textual no se distingue de una caída del proveedor.
                msg = (f"Aunesa /login rechazó las credenciales "
                       f"[{resp.status_code}]: {resp.text[:200]}")
                proveedores.anotar("aunesa", ok=False, donde="login", error=msg)
                raise RuntimeError(msg)
            else:
                ultimo = f"HTTP {resp.status_code}"
        proveedores.anotar("aunesa", ok=False, donde="login", error=ultimo)
        logger.warning("aunesa login falló (%s) intento %d/%d", ultimo, intento, LOGIN_INTENTOS)
        if intento < LOGIN_INTENTOS:
            time.sleep(LOGIN_ESPERAS_S[intento - 1])
    raise AunesaCaido(
        f"el login de Aunesa falló {LOGIN_INTENTOS} veces seguidas ({ultimo}). "
        "Es el servidor del custodio, no la app.")


def auth_headers(*, force_refresh: bool = False) -> dict[str, str]:
    """Headers con Bearer token, cacheado entre llamadas. `force_refresh`
    fuerza un nuevo login (usar ante un 401).

    Con el cortacircuito abierto NO se vuelve a intentar hasta `FALLO_TTL_S`:
    falla al toque y con el mismo mensaje del fallo real.
    """
    global _token, _fallo
    with _lock:
        if force_refresh or _token is None:
            if _fallo is not None:
                desde, msg = _fallo
                resto = FALLO_TTL_S - (time.monotonic() - desde)
                if resto > 0:
                    raise AunesaCaido(f"{msg} (se reintenta en {resto:.0f}s)")
                _fallo = None
            try:
                _token = _login()
            except Exception as e:
                # Cualquier fallo abre el cortacircuito, también el de credenciales:
                # reintentar 3 veces por segundo con la clave mal es la forma más
                # rápida de que el custodio nos bloquee la cuenta.
                _fallo = (time.monotonic(), str(e))
                raise
            _fallo = None
        tok = _token
    return {"Content-Type": "application/json", "Authorization": f"Bearer {tok}"}


def reset_estado() -> None:
    """Olvida el token y el cortacircuito. Para los diags: que `--forzar` pruebe
    el login DE VERDAD y no la memoria del proceso."""
    global _token, _fallo
    with _lock:
        _token, _fallo = None, None


def get(
    path: str,
    params: dict[str, Any] | None = None,
    *,
    timeout: int = 180,
    retries: int = 3,
) -> requests.Response:
    """GET contra Aunesa con re-auth automático ante 401 y retry de timeout.

    `path` relativo a BASE_URL (ej. `operaciones/informes`) o URL absoluta.
    Devuelve el `Response` crudo (el caller decide cómo parsear / qué status
    tolerar). Lanza la última excepción si agota los reintentos de timeout.
    """
    url = path if path.startswith("http") else f"{BASE_URL}/{path.lstrip('/')}"
    last_err: Exception | None = None
    for intento in range(1, retries + 1):
        try:
            resp = requests.get(url, params=params, headers=auth_headers(), timeout=timeout)
            if resp.status_code == 401:
                # token expirado → re-login y un reintento de esta request.
                resp = requests.get(
                    url, params=params, headers=auth_headers(force_refresh=True), timeout=timeout,
                )
            # El estado se anota acá y no en cada caller: el status ≥400 no
            # levanta excepción (el caller decide qué tolerar), así que un 500
            # repetido pasaría entero sin dejar rastro en ningún lado.
            proveedores.anotar(
                "aunesa", ok=resp.status_code < 400, donde=f"GET {path}"[:120],
                error=f"HTTP {resp.status_code} en {path}")
            return resp
        except requests.exceptions.Timeout as e:
            last_err = e
            logger.warning("aunesa GET timeout %s intento %d/%d", path, intento, retries)
            continue
        except Exception as e:
            # Conexión rechazada, DNS, TLS: también es el proveedor caído, y sin
            # esto la única señal sería el traceback de quien haya llamado.
            proveedores.anotar("aunesa", ok=False, donde=f"GET {path}"[:120],
                               error=f"{type(e).__name__}: {e}")
            raise
    proveedores.anotar("aunesa", ok=False, donde=f"GET {path}"[:120],
                       error=f"timeout tras {retries} intentos")
    raise requests.exceptions.Timeout(f"aunesa GET {path}: timeout tras {retries} intentos: {last_err}")
