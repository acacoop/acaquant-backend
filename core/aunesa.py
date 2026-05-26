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

logger = logging.getLogger(__name__)

BASE_URL = "https://aca.aunesa.com/Irmo/api"
AUTH_URL = f"{BASE_URL}/login"

_lock = threading.Lock()
_token: str | None = None


def _login() -> str:
    """POST /login con las credenciales de config. Devuelve el token."""
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
    tok = resp.json().get("token")
    if not tok:
        raise RuntimeError("Aunesa /login no devolvió token")
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
            return resp
        except requests.exceptions.Timeout as e:
            last_err = e
            logger.warning("aunesa GET timeout %s intento %d/%d", path, intento, retries)
            continue
    raise requests.exceptions.Timeout(f"aunesa GET {path}: timeout tras {retries} intentos: {last_err}")
