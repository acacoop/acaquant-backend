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

import logging
import threading
import time
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from core.roles import get_user_role

logger = logging.getLogger(__name__)

_CACHE_TTL = 60.0

_cache_lock = threading.Lock()
_cuentas_by_email: dict[str, tuple[float, set[str] | None]] = {}


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
            res = _cuentas_de_grupos(email_norm)
    except Exception:
        res = None  # fail-open (ve de más, nunca lockea)

    with _cache_lock:
        _cuentas_by_email[email_norm] = (now, res)
    return res


def _cuentas_de_grupos(email_norm: str) -> set[str] | None:
    """Cuentas de los grupos del email desde SQL (manager.grupos). None = no está en
    ningún grupo (ve todo). SQL-native (decomiso Mongo): Manager.Grupos dropeada. Si SQL
    falla, propaga → `cuentas_visibles` lo captura y hace fail-open (None = ve todo)."""
    from core import grupos_sql
    return grupos_sql.cuentas_de_grupos_sql(email_norm)


def listar_grupos() -> list[dict[str, Any]]:
    """Todos los grupos, con `_id` como string. SQL-ONLY (decomiso Mongo 2026-06-28):
    lee manager.grupos (fuente de verdad)."""
    from core import grupos_sql
    return grupos_sql.listar_grupos_sql()


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
    # SQL-ONLY (decomiso Mongo 2026-06-28): manager.grupos es la fuente de verdad. La PK ya
    # NO es str(ObjectId) Mongo → generamos un uuid SQL-native. AUTORITATIVO: si SQL falla,
    # propagamos (el panel ve el error).
    doc["_id"] = str(uuid4())
    from core import grupos_sql
    grupos_sql.crear_grupo_sql(doc["_id"], nombre, doc["emails"], doc["id_cuentas"],
                               actor, ahora, ahora)

    invalidate_cache()
    return doc


def actualizar_grupo(grupo_id: str, nombre: str, emails: Any, id_cuentas: Any,
                     actor: str) -> dict[str, Any]:
    if not grupo_id:
        raise ValueError(f"grupo_id inválido: {grupo_id!r}")
    nombre = (nombre or "").strip()
    if not nombre:
        raise ValueError("el nombre del grupo no puede estar vacío")
    ahora = datetime.now(UTC)
    # SQL-ONLY (decomiso Mongo 2026-06-28): manager.grupos es la fuente de verdad. El id ya
    # NO se parsea como ObjectId (los grupos SQL-native usan uuid). AUTORITATIVO: si SQL falla,
    # propagamos. La tabla SQL no tiene `updated_por` (metadata no leída) → se omite.
    from core import grupos_sql
    if not grupos_sql.actualizar_grupo_sql(grupo_id, nombre, _norm_emails(emails),
                                           _norm_cuentas(id_cuentas), ahora):
        raise ValueError(f"grupo no encontrado: {grupo_id}")

    invalidate_cache()
    return grupos_sql.get_grupo_sql(grupo_id) or {}


def eliminar_grupo(grupo_id: str) -> bool:
    if not grupo_id:
        raise ValueError(f"grupo_id inválido: {grupo_id!r}")
    # SQL-ONLY (decomiso Mongo 2026-06-28): manager.grupos es la fuente de verdad. El id ya
    # NO se parsea como ObjectId. AUTORITATIVO: si SQL falla, propagamos.
    from core import grupos_sql
    deleted = grupos_sql.eliminar_grupo_sql(grupo_id)

    invalidate_cache()
    return deleted
