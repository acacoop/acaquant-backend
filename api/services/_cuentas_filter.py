"""Helpers compartidos para filtrar pipelines Mongo por tipo de cuenta.

Lo usan tanto los endpoints de Operaciones (vista negocio) como los de
Portfolio (AuM por cartera, FCI, total) para que el comportamiento de los
filtros sea idéntico en todas las vistas.

Filtros soportados:
  - todas            → sin filtro extra
  - accionistas      → cuenta IN Cuentas.AccionistasAPI
  - sin_accionistas  → cuenta NOT IN AccionistasAPI
  - cooperativas     → NOT IN AccionistasAPI AND nombre con "coop"
  - productores      → id_cuenta de Comitentes con nivel_1 == PRODUCTORES
"""
from __future__ import annotations

from api.cache import cached
from api.db import get_db_cashflow

_COOP_REGEX = r"\bcoop"

VALID_FILTERS: tuple[str, ...] = (
    "todas", "accionistas", "sin_accionistas", "cooperativas", "productores",
)


@cached(ttl=600)
def _cuentas_accionistas() -> list[str]:
    """Lista de strings `cuenta` ('[N] NOMBRE') desde la fuente CashFlow.Accionistas
    (sin el espejo CuentasAPI.AccionistasAPI — el campo `cuenta` es idéntico)."""
    db = get_db_cashflow()
    return [
        d["cuenta"]
        for d in db["Accionistas"].find({}, {"_id": 0, "cuenta": 1})
        if d.get("cuenta")
    ]


@cached(ttl=600)
def _ids_cuenta_productores() -> list[str]:
    """`id_cuenta` de los comitentes con nivel_1 == PRODUCTORES (Clientes.Comitentes).

    Un productor es un comitente cuya segmentación nivel_1 es 'PRODUCTORES' (los
    niveles se guardan en MAYÚSCULAS, ver segmentacion.py). El match contra los
    movimientos / AuM va por `id_cuenta`, NO por el string `cuenta`
    ('[534] EGUREN, NE'): ese formato vive en los movs, pero Comitentes relaciona
    por id_cuenta — igual que todo el tablero comercial."""
    from core.postgres import get_pool
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT id_cuenta FROM comitentes WHERE nivel_1 = 'PRODUCTORES' "
                    "AND id_cuenta IS NOT NULL")
        return [str(r[0]) for r in cur.fetchall()]


def match_cuenta_filter(filtro: str) -> dict:
    """Sub-doc de `$match` Mongo que aplica el filtro elegido sobre el campo
    `cuenta`. Si el filtro es desconocido o "todas", devuelve `{}` (no-op)."""
    if filtro == "todas" or not filtro:
        return {}
    if filtro == "productores":
        # Único filtro que matchea por `id_cuenta` (la relación de Comitentes);
        # el resto va por el string `cuenta`.
        return {"id_cuenta": {"$in": _ids_cuenta_productores()}}
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
