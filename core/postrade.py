"""core/postrade.py — cliente único de la API Postrade (A3 Mercados / ACyRSA).

Es la API de **post-trade** de Argentina Clearing y Registro S.A.: participantes,
cuentas, posiciones, garantías, márgenes, contabilidad y parámetros de los
contratos negociados en A3 Mercados. Todo por HTTP y contra
`anywhereportfolio.com.ar`.

ETAPA 1 (lo que hay hoy): **acceso y conexión**. Autenticación, token cacheado y
un GET genérico. Los métodos de negocio se agregan por etapas — ver
`docs/POSTRADE.md`.

Regla de capas: `core/` no importa nada del proyecto salvo `config`.

Autenticación — en qué se DIFERENCIA de los otros clientes del repo:

- No es OAuth. No hay `client_id` ni `client_secret` ni `grant_type`: se manda
  **usuario y contraseña** (`nombreUsuario` / `password`) a un único endpoint
  y devuelve un token opaco.
- La respuesta NO es un objeto OAuth (`access_token` / `expires_in`) sino el
  sobre propio de esta API: `{"Status": "OK", "Code": "200", "Value": "<token>"}`.
  El token viene en **`Value`**.
- El token **dura 24 horas** y eso está fijo en el contrato: la respuesta no
  trae vencimiento, así que la vida la sabe el cliente, no el servidor.
- Después va en el header `Authorization` de cada request.

⚠️ Todas las respuestas viajan dentro del mismo sobre `{Status, Code, Value}`,
y `Code` es un **string**. Un error puede llegar con HTTP 200 y `Status` != OK
— por eso `get()` valida el sobre y no solo el status HTTP.

Env vars (ver config.py): POSTRADE_USUARIO, POSTRADE_PASSWORD y los overrides
POSTRADE_BASE_URL / POSTRADE_AUTH_STYLE / POSTRADE_TOKEN_PREFIJO.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any

import requests

import config
from core.http_base import Throttle

logger = logging.getLogger(__name__)

TOKEN_PATH = "/AuthToken/AuthToken"

# El proveedor no publica un límite de llamadas. Sin dato medido (REGLA #2) el
# cliente se auto-limita a 5 req/s, que alcanza de sobra para consultas batch y
# no puede ser tomado por abuso.
_throttle = Throttle(0.2)

# Vida del token declarada en el manual (la respuesta no la informa). Se renueva
# con 30' de margen para que un job largo no se quede a mitad de camino.
TOKEN_VIDA_S = 24 * 3600
_MARGEN_S = 30 * 60

_lock = threading.Lock()
_token: str | None = None
_token_vence: float = 0.0


class PostradeError(RuntimeError):
    """Cualquier fallo hablando con Postrade (auth, red, status o sobre inválido)."""


def _base() -> str:
    return (config.POSTRADE_BASE_URL or "").rstrip("/")


# --------------------------------------------------------------------------- #
# Sobre de respuesta
# --------------------------------------------------------------------------- #
def desempaquetar(j: Any, *, contexto: str) -> Any:
    """Valida el sobre `{Status, Code, Value}` y devuelve `Value`.

    ⚠️ VERIFICADO contra producción: un rechazo de credenciales llega con
    **HTTP 200** y el error adentro del sobre
    (`{"Status":"Unauthorized","Code":"401","ErrorMessage":...}`). Un cliente
    que mirara solo el status HTTP daría eso por bueno — de ahí que esta
    función exista y que el `get()` la use siempre.

    Existe separado para que el diag pueda usar la MISMA validación que el
    cliente: si el diag afloja el criterio, deja de estar midiendo lo que
    después corre en producción.
    """
    if not isinstance(j, dict):
        raise PostradeError(f"{contexto}: la respuesta no es un objeto JSON: {str(j)[:200]}")
    status = str(j.get("Status", "")).upper()
    if status != "OK":
        # El motivo real viaja en ErrorMessage/ErrorDescription, no en Value.
        detalle = " ".join(
            str(j[k]) for k in ("ErrorMessage", "ErrorDescription") if j.get(k)
        ) or str(j.get("Value"))
        raise PostradeError(
            f"{contexto}: Status={j.get('Status')!r} Code={j.get('Code')!r} → {detalle[:300]}"
        )
    return j.get("Value")


# --------------------------------------------------------------------------- #
# Auth
# --------------------------------------------------------------------------- #
def _pedir_token() -> str:
    """POST al endpoint de token con usuario/contraseña. Devuelve el token opaco.

    Las credenciales se pueden mandar por querystring o por body JSON; los dos
    caminos aceptan los MISMOS dos campos. El default es body: la querystring
    deja la contraseña escrita en la URL, y una URL se loguea en todos lados.
    """
    usuario = (config.POSTRADE_USUARIO or "").strip()
    password = config.POSTRADE_PASSWORD or ""
    if not usuario or not password:
        raise PostradeError("Faltan POSTRADE_USUARIO / POSTRADE_PASSWORD en el .env")

    url = f"{_base()}{TOKEN_PATH}"
    credenciales = {"nombreUsuario": usuario, "password": password}
    por_query = (config.POSTRADE_AUTH_STYLE or "body").strip().lower() == "query"

    try:
        _throttle.wait()
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
        raise PostradeError(f"red pidiendo token: {e}") from e

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


def token(*, force_refresh: bool = False) -> str:
    """Token cacheado en memoria. Se renueva 30' antes de las 24hs."""
    global _token, _token_vence
    with _lock:
        if not force_refresh and _token and time.time() < _token_vence - _MARGEN_S:
            return _token
        _token = _pedir_token()
        _token_vence = time.time() + TOKEN_VIDA_S
        logger.info("Postrade: token nuevo (vida declarada %sh)", TOKEN_VIDA_S // 3600)
        return _token


def auth_headers(*, force_refresh: bool = False) -> dict[str, str]:
    """El header `Authorization` con el token, más el Accept."""
    prefijo = config.POSTRADE_TOKEN_PREFIJO or ""
    return {
        "Authorization": f"{prefijo}{token(force_refresh=force_refresh)}",
        "Accept": "application/json",
    }


def reset_token() -> None:
    """Olvida el token cacheado. Para los diags y los tests."""
    global _token, _token_vence
    with _lock:
        _token, _token_vence = None, 0.0


# --------------------------------------------------------------------------- #
# GET genérico
# --------------------------------------------------------------------------- #
def get(path: str, params: dict[str, Any] | None = None) -> Any:
    """GET autenticado. Devuelve el `Value` del sobre, ya desempaquetado.

    `path` va con o sin barra inicial (`PosTrade/ClosingProcesses`). Reintenta
    UNA vez con token nuevo ante 401/403: el token dura 24hs pero el servidor
    puede invalidarlo antes (cambio de contraseña, corte de sesión) y en ese
    caso el cacheado es basura.
    """
    url = f"{_base()}/{path.lstrip('/')}"
    p = {k: v for k, v in (params or {}).items() if v is not None}

    for intento in (0, 1):
        _throttle.wait()
        try:
            r = requests.get(
                url, params=p, headers=auth_headers(force_refresh=bool(intento)), timeout=60
            )
        except requests.RequestException as e:
            raise PostradeError(f"red en {path}: {e}") from e

        if r.status_code in (401, 403) and intento == 0:
            logger.warning("Postrade %s en %s; reintento con token nuevo", r.status_code, path)
            continue

        if r.status_code != 200:
            raise PostradeError(f"{path} HTTP {r.status_code}: {r.text[:400]}")

        try:
            j = r.json()
        except ValueError as e:
            raise PostradeError(f"{path}: respuesta no-JSON: {r.text[:200]}") from e

        return desempaquetar(j, contexto=path)

    raise PostradeError(f"{path}: sigue rechazando el token recién pedido")


# --------------------------------------------------------------------------- #
# Endpoints — ETAPA 1: solo el que sirve para verificar el acceso
# --------------------------------------------------------------------------- #
def procesos_de_cierre(entry_date: str, process_type_code: int | str | None = None) -> Any:
    """GET /PosTrade/ClosingProcesses — cuándo terminaron los procesos de ACyRSA.

    `entry_date` en formato AAAAMMDD y es obligatorio. Se usa como prueba de
    vida del token porque no depende de que tengamos cuentas ni posiciones
    cargadas: si contesta, el acceso está bien.
    """
    return get(
        "PosTrade/ClosingProcesses",
        {"EntryDate": entry_date, "ProcessTypeCode": process_type_code},
    )
