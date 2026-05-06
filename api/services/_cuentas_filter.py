"""Helpers compartidos para filtrar pipelines Mongo por tipo de cuenta.

Single source of truth: la lista de "accionistas" vive en
`Cuentas.AccionistasAPI` y los demás filtros se derivan de ahí. Este módulo
lo usan tanto los endpoints de Operaciones (vista negocio) como los de
Portfolio (AuM por cartera, FCI, total) para que el comportamiento de los
filtros sea idéntico en todas las vistas.

Filtros soportados:
  - todas            → sin filtro extra
  - accionistas      → cuenta IN AccionistasAPI
  - sin_accionistas  → cuenta NOT IN AccionistasAPI
  - cooperativas     → NOT IN AccionistasAPI AND nombre con "coop"
"""
from __future__ import annotations

from api.cache import cached
from api.deps import get_db_cuentas

_COOP_REGEX = r"\bcoop"

VALID_FILTERS: tuple[str, ...] = (
    "todas", "accionistas", "sin_accionistas", "cooperativas",
)


@cached(ttl=600)
def _cuentas_accionistas() -> list[str]:
    """Lista de strings `cuenta` desde Cuentas.AccionistasAPI."""
    db = get_db_cuentas()
    return [
        d["cuenta"]
        for d in db["AccionistasAPI"].find({}, {"_id": 0, "cuenta": 1})
        if d.get("cuenta")
    ]


def match_cuenta_filter(filtro: str) -> dict:
    """Sub-doc de `$match` Mongo que aplica el filtro elegido sobre el campo
    `cuenta`. Si el filtro es desconocido o "todas", devuelve `{}` (no-op)."""
    if filtro == "todas" or not filtro:
        return {}
    accs = _cuentas_accionistas()
    if filtro == "accionistas":
        return {"cuenta": {"$in": accs}}
    if filtro == "sin_accionistas":
        return {"cuenta": {"$nin": accs}}
    if filtro == "cooperativas":
        return {
            "cuenta": {
                "$nin": accs,
                "$regex": _COOP_REGEX,
                "$options": "i",
            },
        }
    return {}
