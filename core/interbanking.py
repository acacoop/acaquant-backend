"""core/interbanking.py — cliente único de las APIs de Interbanking.

Centraliza la autenticación OAuth2 `client_credentials` (token cacheado con
re-auth ante 401) y un GET genérico contra el gateway. Mismo patrón que
`core/aunesa.py`.

Regla de capas: `core/` no importa nada del proyecto salvo `config`.

APIs cubiertas (todas de SOLO LECTURA — no hay endpoint que origine pagos):
    Cuentas        GET /accounts, /accounts/{n}
    Saldos         GET /accounts/{n}/balances
    Extractos      GET /accounts/{n}/statements
    Movimientos    GET /v2/accounts/{n}/movements/{tipo}      (OJO: otro base URL)
    Transferencias GET /transfers/details, /transfers/vouchers

⚠️ DOS TRAMPAS DE ESTA API, ninguna inferible del YAML:

1. **El `tokenUrl` de los YAML del proveedor está MAL.** Declaran
   `/cas/oidc/accessToken`; el servidor publica `/cas/oidc/oidcAccessToken` en su
   documento de descubrimiento. Como el path inexistente cae detrás de Spring
   Security, la respuesta es un **401 genérico y no un 404** — o sea que el error
   aparenta ser de credenciales y no lo es.

2. **Movimientos usa OTRO base URL.** El resto de las APIs cuelga de
   `.../api/prod/v1`; Movimientos de `.../api/prod` con `/v1` o `/v2` en el path.

Autenticación: DOS cosas a la vez, no una.
    - header `client_id: <client_id>`  (apiKey del gateway)
    - header `Authorization: Bearer <token>`  (OAuth del CAS)
Mandar solo una de las dos da 401.

Env vars (ver config.py): INTERBANKING_CLIENT_ID, INTERBANKING_CLIENT_SECRET,
INTERBANKING_CUSTOMER_ID y los overrides INTERBANKING_TOKEN_URL /
INTERBANKING_AUTH_STYLE / INTERBANKING_SCOPE.
"""
from __future__ import annotations

import base64
import logging
import threading
import time
from typing import Any

import requests

import config
from core.http_base import RateLimiter

logger = logging.getLogger(__name__)

BASE_URL = "https://api-gw.interbanking.com.ar/api/prod/v1"
BASE_URL_MOV = "https://api-gw.interbanking.com.ar/api/prod"
DISCOVERY_URL = "https://auth.interbanking.com.ar/cas/oidc/.well-known/openid-configuration"

# El plan contratado admite 100 llamadas por minuto. Dejamos margen: si dos
# procesos del Droplet consultan a la vez, el límite es del ABONADO, no del proceso.
_rate = RateLimiter(80)

_lock = threading.Lock()
_token: str | None = None
_token_vence: float = 0.0


class InterbankingError(RuntimeError):
    """Cualquier fallo hablando con Interbanking (auth, red o status)."""


# --------------------------------------------------------------------------- #
# Auth
# --------------------------------------------------------------------------- #
def _pedir_token() -> tuple[str, int]:
    """POST al endpoint de token con `client_credentials`.

    Devuelve (access_token, segundos_de_vida). Soporta los dos estilos que
    declara el servidor en `token_endpoint_auth_methods_supported`:
    `client_secret_basic` (default) y `client_secret_post`.
    """
    cid = (config.INTERBANKING_CLIENT_ID or "").strip()
    secret = (config.INTERBANKING_CLIENT_SECRET or "").strip()
    if not cid or not secret:
        raise InterbankingError(
            "Faltan INTERBANKING_CLIENT_ID / INTERBANKING_CLIENT_SECRET en el .env"
        )

    data = {"grant_type": "client_credentials"}
    if config.INTERBANKING_SCOPE:
        data["scope"] = config.INTERBANKING_SCOPE
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json",
    }

    if config.INTERBANKING_AUTH_STYLE == "post":
        data["client_id"] = cid
        data["client_secret"] = secret
    else:
        cred = base64.b64encode(f"{cid}:{secret}".encode()).decode()
        headers["Authorization"] = f"Basic {cred}"

    try:
        r = requests.post(
            config.INTERBANKING_TOKEN_URL, data=data, headers=headers, timeout=20
        )
    except requests.RequestException as e:
        raise InterbankingError(f"red pidiendo token: {e}") from e

    if r.status_code != 200:
        # El WWW-Authenticate suele traer el motivo real cuando el cuerpo no dice nada.
        wa = r.headers.get("WWW-Authenticate", "")
        extra = f" | WWW-Authenticate: {wa}" if wa else ""
        raise InterbankingError(
            f"token HTTP {r.status_code}: {r.text[:300]}{extra} "
            f"(url={config.INTERBANKING_TOKEN_URL}, style={config.INTERBANKING_AUTH_STYLE})"
        )

    try:
        j = r.json()
    except ValueError as e:
        raise InterbankingError(f"token: respuesta no-JSON: {r.text[:200]}") from e

    tok = j.get("access_token")
    if not tok:
        raise InterbankingError(f"token: la respuesta no trae access_token: {j}")
    return tok, int(j.get("expires_in") or 3600)


def token(*, force_refresh: bool = False) -> str:
    """Access token cacheado. Se renueva 60s antes de vencer."""
    global _token, _token_vence
    with _lock:
        if not force_refresh and _token and time.time() < _token_vence - 60:
            return _token
        _token, dura = _pedir_token()
        _token_vence = time.time() + dura
        logger.info("Interbanking: token nuevo, vence en %ss", dura)
        return _token


def auth_headers(*, force_refresh: bool = False) -> dict[str, str]:
    """Los DOS headers que pide el gateway: apiKey `client_id` + Bearer."""
    return {
        "client_id": (config.INTERBANKING_CLIENT_ID or "").strip(),
        "Authorization": f"Bearer {token(force_refresh=force_refresh)}",
        "Accept": "application/json",
    }


# --------------------------------------------------------------------------- #
# GET genérico
# --------------------------------------------------------------------------- #
def get(path: str, params: dict[str, Any] | None = None, *, base: str | None = None) -> Any:
    """GET contra el gateway, con el `customer-id` puesto solo y re-auth ante 401.

    `path` va SIN barra inicial. `base` permite apuntar al base URL de
    Movimientos, que es distinto del resto.
    """
    url = f"{base or BASE_URL}/{path.lstrip('/')}"
    p: dict[str, Any] = dict(params or {})
    # Todos los endpoints lo exigen; se pone acá para no repetirlo en cada caller.
    p.setdefault("customer-id", config.INTERBANKING_CUSTOMER_ID)
    p = {k: v for k, v in p.items() if v is not None}

    for intento in (0, 1):
        _rate.wait()
        try:
            r = requests.get(
                url, params=p, headers=auth_headers(force_refresh=bool(intento)), timeout=30
            )
        except requests.RequestException as e:
            raise InterbankingError(f"red en {path}: {e}") from e

        # Un 401 en el primer intento puede ser un token vencido antes de tiempo.
        if r.status_code == 401 and intento == 0:
            logger.warning("Interbanking 401 en %s; reintentando con token nuevo", path)
            continue

        if r.status_code != 200:
            raise InterbankingError(f"{path} HTTP {r.status_code}: {r.text[:400]}")

        try:
            return r.json()
        except ValueError as e:
            raise InterbankingError(f"{path}: respuesta no-JSON: {r.text[:200]}") from e

    raise InterbankingError(f"{path}: 401 incluso con token nuevo")


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #
def cuentas(
    *, account_type: str = "CC", currency: str | None = None,
    bank_number: str | None = None, limit: int = 100, page: int = 0,
) -> dict:
    """GET /accounts — maestro de cuentas.

    OJO: `account-type` FILTRA. Para el universo completo hay que llamar dos
    veces, con 'CC' y con 'CA' (ver `todas_las_cuentas`).
    """
    return get("accounts", {
        "account-type": account_type, "currency": currency,
        "bank-number": bank_number, "limit": limit, "page": page,
    })


def todas_las_cuentas() -> list[dict]:
    """Las cuentas de los DOS tipos, en una sola lista. 2 llamadas."""
    out: list[dict] = []
    for tipo in ("CC", "CA"):
        try:
            out.extend(cuentas(account_type=tipo).get("accounts") or [])
        except InterbankingError as e:
            logger.warning("Interbanking: falló el listado de cuentas %s: %s", tipo, e)
    return out


def saldos(
    account_number: str, bank_number: str, *, account_type: str = "CC",
    currency: str = "ARS", date_since: str | None = None,
    date_until: str | None = None, limit: int = 64, page: int = 0,
) -> dict:
    """GET /accounts/{n}/balances — saldo actual y, con fechas, el histórico diario.

    `initial_operating_balance` es el saldo inicial que hoy el back office carga
    a mano en `operaciones.tesoreria_saldos`.
    """
    return get(f"accounts/{account_number}/balances", {
        "bank-number": bank_number, "account-type": account_type, "currency": currency,
        "date-since": date_since, "date-until": date_until, "limit": limit, "page": page,
    })


def extractos(
    account_number: str, bank_number: str, date_since: str, date_until: str,
    *, account_type: str = "CC", currency: str = "ARS",
    limit: int = 100, page: int = 0,
) -> dict:
    """GET /accounts/{n}/statements — extracto oficial del banco, día por día.

    Trae `opening_balance` / `ending_balance` por día: es la fuente para conciliar
    el saldo final que calcula la grilla BANCOS de Tesorería.
    Máximo 180 días hacia atrás, en ventanas de 60 días por llamada.
    """
    return get(f"accounts/{account_number}/statements", {
        "bank-number": bank_number, "account-type": account_type, "currency": currency,
        "date-since": date_since, "date-until": date_until, "limit": limit, "page": page,
    })


def movimientos(
    account_number: str, bank_number: str, tipo: str = "dia", *,
    account_type: str = "CC", currency: str = "ARS",
    date_since: str | None = None, date_until: str | None = None,
    limit: int = 100, page: int = 0, version: str = "v2",
) -> dict:
    """GET /{v1|v2}/accounts/{n}/movements/{tipo}.

    `tipo`: dia | anteriores | diferidos | zughus (zughus solo en v2).
    OJO: base URL distinto al del resto de las APIs.
    """
    return get(
        f"{version}/accounts/{account_number}/movements/{tipo}",
        {
            "bank-number": bank_number, "account-type": account_type, "currency": currency,
            "date-since": date_since, "date-until": date_until, "limit": limit, "page": page,
        },
        base=BASE_URL_MOV,
    )


def transferencias(
    date_since: str, date_until: str, *, comprobantes: bool = False,
    limit: int = 100, page: int = 0, **filtros: Any,
) -> dict:
    """GET /transfers/details (o /vouchers con `comprobantes=True`).

    Los vouchers traen el bloque `afip` con `vep_number` — el número de VEP que
    hoy se tipea a mano en la tab VEPS de Tesorería.
    `filtros` acepta debit-*/credit-* tal cual los nombra la API.
    """
    path = "transfers/vouchers" if comprobantes else "transfers/details"
    return get(path, {
        "date-since": date_since, "date-until": date_until,
        "limit": limit, "page": page, **filtros,
    })


def discovery() -> dict:
    """Documento de descubrimiento OIDC. Público, no lleva credenciales.

    Es la fuente de verdad ante cualquier fallo de auth: dice el endpoint de
    token real, los grants habilitados y los métodos de autenticación aceptados.
    La documentación del proveedor ya se comprobó que puede estar desactualizada.
    """
    r = requests.get(DISCOVERY_URL, headers={"Accept": "application/json"}, timeout=20)
    r.raise_for_status()
    return r.json()
