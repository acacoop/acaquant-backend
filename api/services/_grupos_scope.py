"""Enforcement de grupos — scoping de cuentas por usuario (Fase 2).

`core/grupos.py` resuelve QUÉ cuentas ve un usuario; este módulo lo aplica
en la capa HTTP. Es la cara "router" del feature — los services reciben el
scope como parámetro `scope: tuple[str, ...] | None` y lo aplican ellos.

Dependencies FastAPI:
- `verificar_id_cuenta` — para endpoints con param `id_cuenta` (path o
  query). 403 si la cuenta está fuera del alcance del usuario.
- `scope_cuentas` — inyecta el scope: tuple ordenado de id_cuenta visibles,
  o `None` (sin restricción: admin o usuario sin grupo). El tuple es
  hashable → sirve de cache key en services `@cached`.

Helper:
- `filtrar_rows` — filtra una lista de dicts ya materializada al scope.

Regla (docs/CLIENTES.md): `None` = sin restricción. Tuple vacío = usuario en
un grupo sin cuentas → no ve nada.
"""
from __future__ import annotations

import re

from fastapi import Depends, HTTPException

from api.auth import get_user_email
from core.grupos import cuentas_visibles

# Las colecciones de operaciones (FlujosAPI, NegocioMovimientos) NO tienen
# `id_cuenta`: sólo el string `cuenta` con formato "[<id>] NOMBRE". Para
# scopearlas extraemos el id bracketed y lo matcheamos contra el scope.
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


def filtrar_rows(
    rows: list[dict],
    scope: tuple[str, ...] | None,
    campo: str = "id_cuenta",
) -> list[dict]:
    """Filtra una lista de dicts a las cuentas del scope. Sin cambios si
    `scope` es None."""
    if scope is None:
        return rows
    permitidas = set(scope)
    return [r for r in rows if str(r.get(campo, "")) in permitidas]


# ── Namespace `cuenta` ("[<id>] NOMBRE") — operaciones / negocio ──────────


def scope_cuenta_match(
    scope: tuple[str, ...] | None, campo: str | None = None,
) -> dict | None:
    """Sub-doc `$match` Mongo que restringe al scope. `None` si `scope` es None
    (sin restricción). Tuple vacío → `{$in: []}` (no matchea nada).

    El formato del campo DIFIERE por colección (verificado 2026-06-03):
      - `campo` dado → igualdad INDEXABLE `{campo: {$in: scope}}`. Usar para
        `campo="cuenta"` en Operaciones (cuenta == id pelado "100", índice
        cuenta_concertacion) y `campo="id_cuenta"` en NegocioMovimientos
        (id_cuenta poblado 100%, índice idcuenta_*).
      - `campo=None` (default) → regex sobre el `cuenta` bracketed "[id] NOMBRE"
        (FlujosAPI y otras que NO tienen id_cuenta).

    OJO: el regex bracketed NUNCA matchea en Operaciones (cuenta es id pelado) —
    por eso ahí va `campo="cuenta"`. Agregarse vía `$and` (no pisa un filtro previo)."""
    if scope is None:
        return None
    if not scope:
        return {(campo or "cuenta"): {"$in": []}}
    if campo:
        return {campo: {"$in": list(scope)}}
    alternation = "|".join(re.escape(s) for s in scope)
    return {"cuenta": {"$regex": rf"^\[({alternation})\]"}}


def aplicar_scope_cuenta(
    match_doc: dict, scope: tuple[str, ...] | None, campo: str | None = None,
) -> None:
    """Agrega la restricción de scope al `match_doc` vía `$and` (no pisa un
    filtro previo). `campo` selecciona igualdad indexable por colección (ver
    `scope_cuenta_match`). No-op si `scope` es None."""
    sub = scope_cuenta_match(scope, campo)
    if sub is not None:
        match_doc.setdefault("$and", []).append(sub)


def verificar_cuenta_str(cuenta_str: str, scope: tuple[str, ...] | None) -> None:
    """403 si el id bracketed de `cuenta_str` no está en el scope. Para
    endpoints con match exacto sobre `cuenta`. No-op si `scope` es None."""
    if scope is None:
        return
    m = _RE_ID_BRACKET.match(cuenta_str or "")
    if not m or m.group(1) not in set(scope):
        raise HTTPException(status_code=403, detail="no tenés acceso a esa cuenta")


def filtrar_cuentas_str(
    cuentas: list[str],
    scope: tuple[str, ...] | None,
) -> list[str]:
    """Filtra una lista de strings `cuenta` al scope (por id bracketed).
    Sin cambios si `scope` es None."""
    if scope is None:
        return cuentas
    permitidas = set(scope)
    return [
        c for c in cuentas
        if (m := _RE_ID_BRACKET.match(c or "")) and m.group(1) in permitidas
    ]
