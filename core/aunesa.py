"""core/aunesa.py — cliente único de la API del custodio Aunesa.

Centraliza la autenticación (login → Bearer token cacheado, con re-auth ante
401) y un GET genérico con retry de timeout. Reemplaza las copias de
`_autenticar` / `_auth` que estaban dispersas en jobs / services / scripts.

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
from typing import Any

import requests

import config
from core import proveedores

logger = logging.getLogger(__name__)

BASE_URL = "https://aca.aunesa.com/Irmo/api"
AUTH_URL = f"{BASE_URL}/login"

_lock = threading.Lock()
_token: str | None = None


def _login() -> str:
    """POST /login con las credenciales de config. Devuelve el token.

    Deja constancia de cómo contestó (`core/proveedores`). **Acá es donde se
    entera el agente de que Aunesa se cayó**, sin una sola llamada extra: los
    daemons le pegan todo el tiempo, así que un 500 en el login queda anotado en
    segundos y con el mensaje exacto. Ver AV_AGENT.md §0.ad.
    """
    try:
        resp = requests.post(
            AUTH_URL,
            json={
                "clientId": config.AUNESA_CLIENT_ID,
                "username": config.AUNESA_USERNAME,
                "password": config.AUNESA_PASSWORD,
            },
            headers={"Content-Type": "application/json"},
            timeout=15,
        )
        resp.raise_for_status()
    except Exception as e:
        proveedores.anotar("aunesa", ok=False, donde="login",
                           error=f"{type(e).__name__}: {e}")
        raise
    tok = resp.json().get("token")
    if not tok:
        proveedores.anotar("aunesa", ok=False, donde="login",
                           error="contestó 200 pero sin token")
        raise RuntimeError("Aunesa /login no devolvió token")
    proveedores.anotar("aunesa", ok=True, donde="login")
    return tok


def auth_headers(*, force_refresh: bool = False) -> dict[str, str]:
    """Headers con Bearer token, cacheado entre llamadas. `force_refresh`
    fuerza un nuevo login (usar ante un 401)."""
    global _token
    with _lock:
        if force_refresh or _token is None:
            _token = _login()
        tok = _token
    return {"Content-Type": "application/json", "Authorization": f"Bearer {tok}"}


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
