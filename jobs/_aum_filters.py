"""Reglas de exclusión aplicadas a `Valuaciones.AuM`.

Single source of truth: usado por `jobs/aum.py` para no persistir registros
nuevos que matcheen, y por `scripts/cleanup_aum_excluidos.py` para limpiar
los que ya están en la colección (backfill one-shot).

Reglas:
  1. `unidad == "USDL"` (cash USD link, no contabiliza).
  2. `cuenta` o `unidad` contiene "OTC" (case-insensitive).
  3. `id_cuenta` aparece en `CuentasAPI.ContrapartesAPI.id_cuenta` — son
     cuentas de fondos / sociedades gerentes (SCHRODER, TORONTO, LOMBARD,
     etc.) que operamos pero cuyas tenencias no son AuM real, son
     cuotapartes. Match por `id_cuenta` (no por `cuenta`) porque la
     denominación difiere de formato entre las dos colecciones —
     Valuaciones.AuM tiene prefijo "[NN] " y ContrapartesAPI no.
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


def load_contrapartes_id_cuentas() -> frozenset[str]:
    """Lee `CuentasAPI.ContrapartesAPI.id_cuenta` y devuelve el set como
    strings normalizados. Match por `id_cuenta` (no por `cuenta`) porque la
    denominación de cuenta difiere de formato entre Valuaciones.AuM y
    ContrapartesAPI (prefijo "[NN] ", espacios en blanco, etc.). El
    `id_cuenta` numérico es la única clave estable."""
    from core.mongo import get_mongo_client_read

    col = get_mongo_client_read()["CuentasAPI"]["ContrapartesAPI"]
    return frozenset(
        str(d["id_cuenta"])
        for d in col.find({}, {"_id": 0, "id_cuenta": 1})
        if d.get("id_cuenta") is not None
    )


def is_excluded(
    cuenta: str | None,
    unidad: str | None,
    id_cuenta: str | int | None = None,
    contrapartes_ids: frozenset[str] | set[str] | None = None,
) -> bool:
    """True si esta combinación NO debe persistirse (ni quedar) en AuM.

    `contrapartes_ids` es la lista de `id_cuenta` (como strings) a excluir
    — típicamente `load_contrapartes_id_cuentas()`. Si se omite, la regla
    #3 no aplica.
    """
    cuenta = cuenta or ""
    unidad = unidad or ""
    if unidad in EXCLUDE_UNIDAD_EXACT:
        return True
    if _RE_OTC.search(unidad) or _RE_OTC.search(cuenta):
        return True
    if (
        contrapartes_ids
        and id_cuenta is not None
        and str(id_cuenta) in contrapartes_ids
    ):
        return True
    return unidad == "ARS" and cuenta in CUENTAS_SIN_ARS


def mongo_match_excluded(
    contrapartes_ids: frozenset[str] | set[str] | None = None,
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
    if contrapartes_ids:
        # Toleramos `id_cuenta` stored como string OR int — los writers de
        # Valuaciones.AuM lo guardan como str (regex extract de pandas) pero
        # otros pipelines podrían normalizar a int.
        ids_str = list(contrapartes_ids)
        ids_int = [int(x) for x in contrapartes_ids if x.lstrip("-").isdigit()]
        or_clauses.append({"id_cuenta": {"$in": ids_str + ids_int}})
    return {"$or": or_clauses}
