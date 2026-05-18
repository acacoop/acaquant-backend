"""core/grupos.py — grupos de acceso por cuenta (scoping multi-tenant).

Un grupo agrupa usuarios (emails) + cuentas comitentes (id_cuenta). El
admin los gestiona desde /manager → GRUPOS. Colección `Manager.Grupos`.

Reglas de `cuentas_visibles`:
- admin → ve TODO (gestiona los grupos, sin restricción).
- usuario en NINGÚN grupo → ve TODO (transición: nada se rompe hasta que
  el admin lo asigne a un grupo).
- usuario en ≥1 grupo → unión de las id_cuentas de sus grupos.

FASE 1: este módulo + el CRUD del panel. Todavía NO enforcea — el scoping
real (aplicar `cuentas_visibles` en los endpoints de cuentas) es Fase 2.
"""
from __future__ import annotations

import threading
import time
from datetime import UTC, datetime
from typing import Any

from bson import ObjectId
from bson.errors import InvalidId

from core.mongo import get_mongo_client
from core.roles import get_user_role

_DB_NAME = "Manager"
_COL_NAME = "Grupos"
_CACHE_TTL = 60.0

_cache_lock = threading.Lock()
_cuentas_by_email: dict[str, tuple[float, set[str] | None]] = {}


def _col():
    return get_mongo_client()[_DB_NAME][_COL_NAME]


def _norm_emails(emails: Any) -> list[str]:
    return sorted({str(e).lower().strip() for e in (emails or []) if str(e).strip()})


def _norm_cuentas(ids: Any) -> list[str]:
    return sorted({str(c).strip() for c in (ids or []) if str(c).strip()})


def invalidate_cache() -> None:
    """Vacía el cache de `cuentas_visibles` — llamar tras cada mutación."""
    with _cache_lock:
        _cuentas_by_email.clear()


def cuentas_visibles(email: str) -> set[str] | None:
    """Cuentas que el usuario puede ver/operar. `None` = SIN restricción.

    `None` para: admin, y usuarios que no están en ningún grupo (transición).
    `set` para: usuarios en ≥1 grupo → unión de las id_cuentas de sus grupos.

    Ante error de DB devuelve `None` (fail-open) — los grupos no deben
    tumbar la app; el peor caso es "ve de más", igual al estado actual.
    """
    if not email:
        return None
    email_norm = email.lower().strip()
    now = time.time()
    with _cache_lock:
        hit = _cuentas_by_email.get(email_norm)
        if hit and now - hit[0] < _CACHE_TTL:
            return hit[1]

    res: set[str] | None
    try:
        if get_user_role(email_norm) == "admin":
            res = None
        else:
            cuentas: set[str] = set()
            en_grupo = False
            for g in _col().find({"emails": email_norm}, {"_id": 0, "id_cuentas": 1}):
                en_grupo = True
                for c in g.get("id_cuentas") or []:
                    cuentas.add(str(c))
            res = cuentas if en_grupo else None
    except Exception:
        res = None

    with _cache_lock:
        _cuentas_by_email[email_norm] = (now, res)
    return res


def listar_grupos() -> list[dict[str, Any]]:
    """Todos los grupos, con `_id` como string."""
    out: list[dict[str, Any]] = []
    for d in _col().find({}).sort("nombre", 1):
        d["_id"] = str(d["_id"])
        out.append(d)
    return out


def crear_grupo(nombre: str, emails: Any, id_cuentas: Any, actor: str) -> dict[str, Any]:
    nombre = (nombre or "").strip()
    if not nombre:
        raise ValueError("el nombre del grupo no puede estar vacío")
    ahora = datetime.now(UTC)
    doc = {
        "nombre":     nombre,
        "emails":     _norm_emails(emails),
        "id_cuentas": _norm_cuentas(id_cuentas),
        "creado_por": actor,
        "creado_at":  ahora,
        "updated_at": ahora,
    }
    doc["_id"] = str(_col().insert_one(doc).inserted_id)
    invalidate_cache()
    return doc


def actualizar_grupo(grupo_id: str, nombre: str, emails: Any, id_cuentas: Any,
                     actor: str) -> dict[str, Any]:
    try:
        oid = ObjectId(grupo_id)
    except (InvalidId, TypeError) as e:
        raise ValueError(f"grupo_id inválido: {grupo_id!r}") from e
    nombre = (nombre or "").strip()
    if not nombre:
        raise ValueError("el nombre del grupo no puede estar vacío")
    upd = {
        "nombre":      nombre,
        "emails":      _norm_emails(emails),
        "id_cuentas":  _norm_cuentas(id_cuentas),
        "updated_at":  datetime.now(UTC),
        "updated_por": actor,
    }
    if _col().update_one({"_id": oid}, {"$set": upd}).matched_count == 0:
        raise ValueError(f"grupo no encontrado: {grupo_id}")
    invalidate_cache()
    doc = _col().find_one({"_id": oid})
    doc["_id"] = str(doc["_id"])
    return doc


def eliminar_grupo(grupo_id: str) -> bool:
    try:
        oid = ObjectId(grupo_id)
    except (InvalidId, TypeError) as e:
        raise ValueError(f"grupo_id inválido: {grupo_id!r}") from e
    deleted = _col().delete_one({"_id": oid}).deleted_count > 0
    invalidate_cache()
    return deleted
