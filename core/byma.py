"""Cliente BYMA Primarias Placements.

OAuth2 client_credentials flow (per docs del portal BYMA):
1. POST a BYMA_TOKEN_URL con Content-Type form-urlencoded y body con 4
   params: client_id, client_secret, grant_type=client_credentials,
   scope=bymaPrimariasPlacements.read. NO usa Basic Auth — las
   credenciales viajan en el body.
2. Response trae {access_token, expires_in, scope}. Cacheamos el token en
   memoria del proceso hasta (now + expires_in - 60s de buffer).
3. Cada request GET al API se autentica con `Authorization: Bearer <token>`.
4. Si llega 401 por token expirado antes de tiempo, se fuerza refresh y
   se reintenta una sola vez.

Rate limiting interno: hard-cap de 30 req/min por seguridad. BYMA no
publica límites oficiales en la doc pero el SLO habla de disponibilidad
99.97% — no conviene pegarle en ráfaga.

Endpoints wrapped:
- GET /underwriters           — colocadores (paginado)
- GET /issuers                — emisores (paginado)
- GET /historical-placements  — colocaciones históricas (paginado + filtros)
- GET /document-content.raw   — documento asociado a una colocación (bytes)

Helper `iter_pages(fn)` para recorrer toda la paginación en un generador.

Excepciones:
- BymaNotConfigured  — falta algo en .env
- BymaAuthError      — no se pudo autenticar
- BymaRateLimitError — 429 o quota local
- BymaError          — cualquier otro fallo del cliente
"""
from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Iterator
from typing import Any

import requests

from config import (
    BYMA_BASE_URL,
    BYMA_CLIENT_ID,
    BYMA_CLIENT_SECRET,
    BYMA_TOKEN_URL,
)

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Estado del cliente (proceso-wide)
# ─────────────────────────────────────────────────────────────────────────────

_MAX_CALLS_PER_MIN = 30
_rate_lock = threading.Lock()
_calls_ts: list[float] = []

_token_lock = threading.Lock()
_token_cache: dict[str, Any] = {
    "access_token": None,
    "expires_at":   0.0,
    "scope":        None,
}


# ─────────────────────────────────────────────────────────────────────────────
# Excepciones
# ─────────────────────────────────────────────────────────────────────────────


class BymaError(RuntimeError):
    """Error del cliente BYMA."""


class BymaAuthError(BymaError):
    """No se pudo autenticar (credenciales, scope, red)."""


class BymaRateLimitError(BymaError):
    """Hit 429 del server o quota local excedida."""


class BymaNotConfigured(BymaError):
    """Falta BYMA_CLIENT_ID / SECRET / URLs en el entorno."""


# ─────────────────────────────────────────────────────────────────────────────
# OAuth2 — gestión del token
# ─────────────────────────────────────────────────────────────────────────────


def _ensure_configured() -> None:
    missing = [
        name for name, value in (
            ("BYMA_CLIENT_ID",     BYMA_CLIENT_ID),
            ("BYMA_CLIENT_SECRET", BYMA_CLIENT_SECRET),
            ("BYMA_TOKEN_URL",     BYMA_TOKEN_URL),
            ("BYMA_BASE_URL",      BYMA_BASE_URL),
        )
        if not value
    ]
    if missing:
        raise BymaNotConfigured(f"Faltan env vars: {', '.join(missing)}")


def _fetch_new_token() -> dict[str, Any]:
    """Intercambia client_credentials por access_token.

    Flujo según doc portal BYMA: todas las credenciales en el body como
    form-urlencoded. No usa Basic Auth.
    """
    _ensure_configured()
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept":       "application/json",
    }
    body = {
        "client_id":     BYMA_CLIENT_ID,
        "client_secret": BYMA_CLIENT_SECRET,
        "grant_type":    "client_credentials",
        "scope":         "bymaPrimariasPlacements.read",
    }
    try:
        r = requests.post(BYMA_TOKEN_URL, headers=headers, data=body, timeout=15)
    except requests.RequestException as e:
        raise BymaAuthError(f"no pude contactar token endpoint: {e}") from e

    if r.status_code != 200:
        raise BymaAuthError(
            f"token endpoint devolvió {r.status_code}: {r.text[:400]}"
        )
    try:
        data = r.json()
    except ValueError as e:
        raise BymaAuthError(f"respuesta no-JSON del token endpoint: {e}") from e

    if not data.get("access_token"):
        raise BymaAuthError(f"respuesta sin access_token: {data}")
    return data


def get_access_token() -> str:
    """Devuelve un access_token válido. Si hay cache vigente, lo reusa."""
    now = time.time()
    with _token_lock:
        if _token_cache["access_token"] and now < _token_cache["expires_at"]:
            return _token_cache["access_token"]

        data = _fetch_new_token()
        expires_in = int(data.get("expires_in", 3600))
        # Buffer de 60s para no servir tokens a punto de expirar entre
        # get_token() y el request real.
        _token_cache["access_token"] = data["access_token"]
        _token_cache["expires_at"]   = now + expires_in - 60
        _token_cache["scope"]        = data.get("scope")
        logger.info(
            "BYMA token renovado (expira en %ds, scope=%r)",
            expires_in, data.get("scope"),
        )
        return _token_cache["access_token"]


def invalidate_token() -> None:
    """Fuerza refresh del token en el próximo request."""
    with _token_lock:
        _token_cache["access_token"] = None
        _token_cache["expires_at"]   = 0.0


# ─────────────────────────────────────────────────────────────────────────────
# Rate limit interno
# ─────────────────────────────────────────────────────────────────────────────


def _wait_for_rate_limit() -> None:
    with _rate_lock:
        now = time.time()
        # Purgar entradas > 60s
        while _calls_ts and now - _calls_ts[0] > 60:
            _calls_ts.pop(0)
        if len(_calls_ts) >= _MAX_CALLS_PER_MIN:
            wait = 60 - (now - _calls_ts[0]) + 0.1
            logger.warning("BYMA rate limit interno: esperando %.1fs", wait)
            time.sleep(wait)
            now = time.time()
            while _calls_ts and now - _calls_ts[0] > 60:
                _calls_ts.pop(0)
        _calls_ts.append(now)


# ─────────────────────────────────────────────────────────────────────────────
# HTTP GET con auto-refresh del token
# ─────────────────────────────────────────────────────────────────────────────


def _get_json(path: str, params: dict[str, Any] | None = None,
              *, _retry_on_401: bool = True) -> Any:
    """GET a BYMA_BASE_URL + path, Bearer token. Retry una vez ante 401."""
    _ensure_configured()
    _wait_for_rate_limit()

    url = f"{BYMA_BASE_URL}{path}"
    token = get_access_token()
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept":        "application/json",
    }
    try:
        r = requests.get(url, headers=headers, params=params, timeout=20)
    except requests.RequestException as e:
        raise BymaError(f"red falló en GET {path}: {e}") from e

    if r.status_code == 401 and _retry_on_401:
        # Token expiró antes de lo esperado → invalidate y reintentar.
        invalidate_token()
        return _get_json(path, params=params, _retry_on_401=False)
    if r.status_code == 429:
        raise BymaRateLimitError(f"429 en {path}: {r.text[:200]}")
    if r.status_code != 200:
        raise BymaError(f"{r.status_code} en {path}: {r.text[:400]}")

    try:
        return r.json()
    except ValueError as e:
        raise BymaError(f"respuesta no-JSON en {path}: {e}") from e


def _get_raw(path: str, params: dict[str, Any] | None = None,
             *, _retry_on_401: bool = True) -> bytes:
    """GET que devuelve bytes (ej: document-content, puede ser PDF)."""
    _ensure_configured()
    _wait_for_rate_limit()

    url = f"{BYMA_BASE_URL}{path}"
    token = get_access_token()
    headers = {"Authorization": f"Bearer {token}"}
    try:
        r = requests.get(url, headers=headers, params=params, timeout=30)
    except requests.RequestException as e:
        raise BymaError(f"red falló en GET {path}: {e}") from e

    if r.status_code == 401 and _retry_on_401:
        invalidate_token()
        return _get_raw(path, params=params, _retry_on_401=False)
    if r.status_code != 200:
        raise BymaError(f"{r.status_code} en {path}: {r.text[:400]}")
    return r.content


# ─────────────────────────────────────────────────────────────────────────────
# Métodos públicos (uno por endpoint)
# ─────────────────────────────────────────────────────────────────────────────


def get_underwriters(page: int = 0, size: int = 50) -> dict[str, Any]:
    """Colocadores (brokers/ALYCs) registrados en BYMA Primarias.

    Response shape: {meta: {...paginación}, result: [{underwriterId, underwriterName}, ...]}
    """
    return _get_json("/underwriters.json", {"page": page, "size": size})


def get_issuers(page: int = 0, size: int = 50) -> dict[str, Any]:
    """Emisores (Tesoro Nacional, provincias, corporativos).

    Response shape: {meta: {...paginación}, result: [{issuerId, issuerName, ...}, ...]}
    """
    return _get_json("/issuers.json", {"page": page, "size": size})


def get_historical_placements(
    page: int = 0,
    size: int = 50,
    **filters: Any,
) -> dict[str, Any]:
    """Colocaciones históricas (licitaciones ya cerradas).

    Parámetros de filtro exactos TBD — hay que confirmar con la doc real del
    portal. Probablemente acepta: fechaDesde, fechaHasta, issuerId,
    underwriterId, placementType, etc. Se pasan como kwargs.
    """
    params: dict[str, Any] = {"page": page, "size": size}
    params.update(filters)
    return _get_json("/historical-placements.json", params)


def get_document_content(file_id: int, placement_id: int) -> bytes:
    """Documento asociado a una colocación (pliego, prospecto, etc.).
    Devuelve bytes crudos — el formato real (PDF/HTML) lo determina el
    response original."""
    return _get_raw(
        "/document-content.raw",
        {"fileId": file_id, "placementId": placement_id},
    )


# ─────────────────────────────────────────────────────────────────────────────
# Helper de paginación
# ─────────────────────────────────────────────────────────────────────────────


def iter_pages(
    fn: Callable[..., dict[str, Any]],
    page_size: int = 100,
    **extra_params: Any,
) -> Iterator[dict[str, Any]]:
    """Itera todas las páginas de un endpoint paginado y yield cada item.

    Espera que el response tenga shape:
        {meta: {page, totalPages, ...}, result: [item1, item2, ...]}

    Uso:
        for uw in iter_pages(get_underwriters):
            print(uw["underwriterName"])
    """
    page = 0
    while True:
        resp = fn(page=page, size=page_size, **extra_params)
        meta = resp.get("meta") or {}
        yield from (resp.get("result") or [])

        total_pages = meta.get("totalPages")
        if total_pages is None:
            # Sin metadata de paginación — cortar después de 1 página.
            break
        if page + 1 >= total_pages:
            break
        page += 1
