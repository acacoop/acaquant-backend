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

Regla (docs/GRUPOS.md): `None` = sin restricción. Tuple vacío = usuario en
un grupo sin cuentas → no ve nada.
"""
from __future__ import annotations

from fastapi import Depends, HTTPException

from api.auth import get_user_email
from core.grupos import cuentas_visibles


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
