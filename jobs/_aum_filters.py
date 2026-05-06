"""Reglas de exclusión aplicadas a `Valuaciones.AuM`.

Single source of truth: usado por `jobs/aum.py` para no persistir registros
nuevos que matcheen, y por `scripts/cleanup_aum_excluidos.py` para limpiar
los que ya están en la colección (backfill one-shot).

Las reglas son hardcodeadas porque son decisiones de negocio puntuales
("no contabilizar cash custodiado por X casa"); si el set crece, mover a
`Valuaciones.AumExclusiones` o similar.
"""
from __future__ import annotations

import re

# Sub-strings (case-insensitive) en `cuenta` que disparan exclusión.
EXCLUDE_CUENTA_KEYWORDS: tuple[str, ...] = (
    "SCHRODER",   # captura SCHRODER y SCHRODERS
    "TORONTO",
    "ALLARIA",
    "OTC",
)

# Sub-strings (case-insensitive) en `unidad` que disparan exclusión.
EXCLUDE_UNIDAD_KEYWORDS: tuple[str, ...] = (
    "OTC",
)

# Match exacto en `unidad` (no por sub-string — USDL es código corto).
EXCLUDE_UNIDAD_EXACT: frozenset[str] = frozenset({"USDL"})

# Cuentas donde se descarta específicamente la tenencia ARS (cash) — el
# negocio decidió no contabilizar el efectivo de estas cuentas en el AuM.
CUENTAS_SIN_ARS: frozenset[str] = frozenset({
    "[100] ACA VALORES S.A.",
    "[101] ASOCIACION DE COOPERATIVAS ARGENTINAS COOP LTDA",
})

_RE_CUENTA = re.compile(
    "|".join(re.escape(k) for k in EXCLUDE_CUENTA_KEYWORDS),
    re.IGNORECASE,
)
_RE_UNIDAD = re.compile(
    "|".join(re.escape(k) for k in EXCLUDE_UNIDAD_KEYWORDS),
    re.IGNORECASE,
)


def is_excluded(cuenta: str | None, unidad: str | None) -> bool:
    """True si esta combinación NO debe persistirse (ni quedar) en AuM."""
    cuenta = cuenta or ""
    unidad = unidad or ""
    if unidad in EXCLUDE_UNIDAD_EXACT:
        return True
    if _RE_UNIDAD.search(unidad):
        return True
    if _RE_CUENTA.search(cuenta):
        return True
    return unidad == "ARS" and cuenta in CUENTAS_SIN_ARS


def mongo_match_excluded() -> dict:
    """Filtro Mongo $or equivalente a `is_excluded()`. Útil para
    `delete_many` / `count_documents` sobre la colección AuM."""
    return {
        "$or": [
            {"unidad": {"$in": list(EXCLUDE_UNIDAD_EXACT)}},
            {"unidad": {"$regex": _RE_UNIDAD.pattern, "$options": "i"}},
            {"cuenta": {"$regex": _RE_CUENTA.pattern, "$options": "i"}},
            {
                "$and": [
                    {"unidad": "ARS"},
                    {"cuenta": {"$in": list(CUENTAS_SIN_ARS)}},
                ],
            },
        ],
    }
