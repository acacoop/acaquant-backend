"""Reglas de exclusión aplicadas a `Valuaciones.AuM`.

Single source of truth: usado por `jobs/aum.py` para no persistir registros
nuevos que matcheen, y por `scripts/cleanup_aum_excluidos.py` para limpiar
los que ya están en la colección (backfill one-shot).

Reglas:
  1. `unidad == "USDL"` (cash USD link, no contabiliza).
  2. `cuenta` o `unidad` contiene "OTC" (case-insensitive).
  3. `cuenta` aparece en `CuentasAPI.ContrapartesAPI.cuenta` — son cuentas de
     fondos / sociedades gerentes (SCHRODER, TORONTO, ALLARIA, etc.) que
     operamos pero cuyas tenencias no son AuM real, son cuotapartes.
  4. `unidad == "ARS"` para `[100]` y `[101]` (decisión puntual de negocio:
     no contabilizar el cash ARS de esas dos cuentas en el AuM).

La regla 3 vive en BD (no se hardcodea más en este módulo) — el equipo edita
la lista desde el panel de Contrapartes y la exclusión la respeta sola.
"""
from __future__ import annotations

import re

# Sub-strings (case-insensitive) en `cuenta` o `unidad` que disparan exclusión
# por patrón. Todo lo que no es patrón (sociedades gerentes específicas) sale
# de Mongo via `load_contrapartes_cuentas()`.
EXCLUDE_OTC_KEYWORDS: tuple[str, ...] = ("OTC",)

# Match exacto en `unidad` — códigos cortos donde un substring matchearía
# falsos positivos.
EXCLUDE_UNIDAD_EXACT: frozenset[str] = frozenset({"USDL"})

# Cuentas donde se descarta específicamente la tenencia ARS (cash) — el
# negocio decidió no contabilizar el efectivo de estas cuentas en el AuM.
CUENTAS_SIN_ARS: frozenset[str] = frozenset({
    "[100] ACA VALORES S.A.",
    "[101] ASOCIACION DE COOPERATIVAS ARGENTINAS COOP LTDA",
})

_RE_OTC = re.compile(
    "|".join(re.escape(k) for k in EXCLUDE_OTC_KEYWORDS),
    re.IGNORECASE,
)


def load_contrapartes_cuentas() -> frozenset[str]:
    """Lee `CuentasAPI.ContrapartesAPI.cuenta` y devuelve el set de
    denominaciones (`cuenta` con formato '[NN] NOMBRE'). Cargar una vez por
    proceso e inyectar en `is_excluded` / `mongo_match_excluded`."""
    from core.mongo import get_mongo_client_read

    col = get_mongo_client_read()["CuentasAPI"]["ContrapartesAPI"]
    return frozenset(
        d["cuenta"]
        for d in col.find({}, {"_id": 0, "cuenta": 1})
        if d.get("cuenta")
    )


def is_excluded(
    cuenta: str | None,
    unidad: str | None,
    contrapartes: frozenset[str] | set[str] | None = None,
) -> bool:
    """True si esta combinación NO debe persistirse (ni quedar) en AuM.

    `contrapartes` es la lista de denominaciones a excluir (típicamente
    `load_contrapartes_cuentas()`). Si se omite, la regla #3 no aplica.
    """
    cuenta = cuenta or ""
    unidad = unidad or ""
    if unidad in EXCLUDE_UNIDAD_EXACT:
        return True
    if _RE_OTC.search(unidad) or _RE_OTC.search(cuenta):
        return True
    if contrapartes and cuenta in contrapartes:
        return True
    return unidad == "ARS" and cuenta in CUENTAS_SIN_ARS


def mongo_match_excluded(
    contrapartes: frozenset[str] | set[str] | None = None,
) -> dict:
    """Filtro Mongo $or equivalente a `is_excluded()`. Útil para
    `delete_many` / `count_documents` sobre la colección AuM."""
    or_clauses: list[dict] = [
        {"unidad": {"$in": list(EXCLUDE_UNIDAD_EXACT)}},
        {"unidad": {"$regex": _RE_OTC.pattern, "$options": "i"}},
        {"cuenta": {"$regex": _RE_OTC.pattern, "$options": "i"}},
        {
            "$and": [
                {"unidad": "ARS"},
                {"cuenta": {"$in": list(CUENTAS_SIN_ARS)}},
            ],
        },
    ]
    if contrapartes:
        or_clauses.append({"cuenta": {"$in": list(contrapartes)}})
    return {"$or": or_clauses}
