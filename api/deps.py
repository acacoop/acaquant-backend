"""Dependencias de FastAPI: bearer de la API y scope de cuentas por grupo.

Acá vive lo que un router inyecta con `Depends(...)` y que NO es lógica de
negocio: si necesita `HTTPException` o `Depends`, es de esta capa. Los services
(`api/services/`) siguen siendo puros y reciben el scope como parámetro
`scope: tuple[str, ...] | None`.

Scope de cuentas (docs/CLIENTES.md): `core/grupos.py` resuelve QUÉ cuentas ve
un usuario; este módulo lo aplica en la capa HTTP.
- `scope_cuentas` — inyecta el scope: tuple ordenado de id_cuenta visibles, o
  `None` (sin restricción: admin o usuario sin grupo). El tuple es hashable →
  sirve de cache key en services `@cached`.
- `verificar_id_cuenta` / `verificar_id_cuenta_opcional` — para endpoints con
  param `id_cuenta` (path o query). 403 si la cuenta está fuera del alcance.
- `verificar_account` — para órdenes, donde `account` es el id comitente crudo.
- `verificar_cuenta_str` — para el namespace `cuenta` ("[<id>] NOMBRE").

Regla: `None` = sin restricción. Tuple vacío = usuario en un grupo sin cuentas
→ no ve nada.
"""
from __future__ import annotations

import re
import secrets

from fastapi import Depends, Header, HTTPException

from api.auth import get_user_email
from config import API_KEY
from core.grupos import cuentas_visibles


def verify_api_key(authorization: str | None = Header(default=None)) -> None:
    """Valida el header Authorization: Bearer <API_KEY>.

    Si API_KEY no está configurada en .env, deja pasar todo (modo dev) —
    el header es opcional en ese caso. Si API_KEY está seteada, el header
    es obligatorio y debe matchear.

    Nota (EXT-AUTH1): en prod este "deja pasar todo" no se alcanza — el
    boot aborta si `ENV=prod` y falta `API_KEY` (ver
    `api.main._validar_postura_auth`). El fail-open queda solo para dev.
    """
    if not API_KEY:
        return
    # compare_digest sobre bytes: con str, un header con cualquier byte no-ASCII
    # lanza TypeError → 500 sin manejar desde un path no autenticado. En bytes
    # devuelve False limpio (falla cerrado → 401).
    expected = f"Bearer {API_KEY}".encode()
    received = authorization.encode("utf-8", "ignore") if authorization else b""
    if not secrets.compare_digest(received, expected):
        raise HTTPException(status_code=401, detail="API key inválida")


# ── Scope de cuentas por grupo ──────────────────────────────────────────────

# Las tablas de operaciones que no tienen `id_cuenta` traen el string `cuenta`
# con formato "[<id>] NOMBRE": se extrae el id bracketed y se matchea contra el
# scope.
_RE_ID_BRACKET = re.compile(r"^\[(\d+)\]")


def scope_cuentas(email: str = Depends(get_user_email)) -> tuple[str, ...] | None:
    """Dependency: tuple ordenado de id_cuenta visibles, o `None` (sin
    restricción). Pasar el resultado tal cual a los services."""
    visibles = cuentas_visibles(email)
    return None if visibles is None else tuple(sorted(visibles))


def verificar_id_cuenta(
    id_cuenta: str,
    email: str = Depends(get_user_email),
) -> str:
    """Dependency para endpoints con param `id_cuenta`. 403 si la cuenta no
    está en el alcance del usuario. FastAPI matchea `id_cuenta` por nombre
    (sirve tanto si el endpoint lo declara como path o como query)."""
    visibles = cuentas_visibles(email)
    if visibles is not None and str(id_cuenta) not in visibles:
        raise HTTPException(status_code=403, detail="no tenés acceso a esa cuenta")
    return id_cuenta


def verificar_id_cuenta_opcional(
    id_cuenta: str | None = None,
    email: str = Depends(get_user_email),
) -> str | None:
    """Igual que `verificar_id_cuenta` pero para endpoints donde `id_cuenta` es
    OPCIONAL (ej. una serie que sin cuenta agrega por operador y con cuenta
    baja al cliente). Usar la variante obligatoria ahí volvería el parámetro
    requerido y rompería el endpoint.

    Sin `id_cuenta` no hay objeto que verificar → pasa. El agregado que
    devuelve el endpoint en ese caso debe venir ya scopeado por el service
    (vía `scope_cuentas`), que es una defensa distinta.
    """
    if id_cuenta is None or str(id_cuenta).strip() == "":
        return None
    visibles = cuentas_visibles(email)
    if visibles is not None and str(id_cuenta) not in visibles:
        raise HTTPException(status_code=403, detail="no tenés acceso a esa cuenta")
    return id_cuenta


def verificar_account(account: str | None, scope: tuple[str, ...] | None) -> None:
    """403/400 si `account` no está en el scope. No-op si `scope` es None.

    Para endpoints de ÓRdenes (`/api/ordenes*`), donde `account` es el id
    comitente crudo (mismo espacio que `Grupos.id_cuentas`), no el string
    bracketed de operaciones. Un user scopeado DEBE pasar su cuenta explícita
    (no puede caer al default del .env, que puede no ser suya) → 400 si la
    omite. Admin / sin-grupo (`scope=None`) no se ve afectado."""
    if scope is None:
        return
    if account is None:
        raise HTTPException(
            status_code=400,
            detail="especificá la cuenta: tu usuario está scopeado a cuentas concretas",
        )
    if str(account) not in set(scope):
        raise HTTPException(status_code=403, detail="no tenés acceso a esa cuenta")


def verificar_cuenta_str(cuenta_str: str, scope: tuple[str, ...] | None) -> None:
    """403 si el id bracketed de `cuenta_str` no está en el scope. Para
    endpoints con match exacto sobre `cuenta`. No-op si `scope` es None."""
    if scope is None:
        return
    m = _RE_ID_BRACKET.match(cuenta_str or "")
    if not m or m.group(1) not in set(scope):
        raise HTTPException(status_code=403, detail="no tenés acceso a esa cuenta")
